"""The machine operations, and nothing else.

Everything a program can name is written in the language now: the classes and
`print` in `turkey/lib/Prelude.tl`, and `Array.push`, `Int.toString` and the
rest in `turkey/lib/Data/*.tl` (SPEC-DELTAS.md entry 42). What is left here is
the floor they stand on -- integer addition, the comparison that reads one
string against another, the two writes to stdout -- under names that begin
`Prim.`.

A `Prim.` name is in the *environment* for every module, because a value
elaborated in one module still has to run in the same evaluator. What keeps it
out of the surface language is a module's scope: `turkey/modules.py` spells
`Prim.intAdd` only for a module under `turkey/lib`, and a user program that
writes it is told the name is not defined.

Every operation here is defined by `PRIMITIVES.md`, not by what Python
happens to do. That distinction is the whole point of that document: `Int` is
64-bit and traps, `Float` is IEEE 754 binary64 and does not, `String` is
well-formed UTF-8 addressed by byte, and `Char` is a Unicode scalar value.
Where the host disagrees -- Python integers are unbounded, Python raises on
float division by zero -- the disagreement is resolved *here*, so that the
evaluator and the native backend can be held to the same statements.
"""

from __future__ import annotations

import functools
import math
import struct
import sys

from .errors import TurkeyPanic
from .constraints import Binding, Env
from .prelude import BOOL_FALSE, BOOL_TRUE
from .types import (
    BOOL, BYTE, BYTE_MAX, BYTE_MIN, CHAR, FLOAT, INT, INT_MAX, INT_MIN, STRING,
    UNIT, TFun, TVar, array_of, float_to_string, generalize,
    is_scalar_value, mono, raw_array_of,
)
from .values import (
    UNIT as UNIT_VALUE, ArrayObj, Builtin, ConValue, RecordObj, from_bool, truth,
)


def _scheme(build):
    """Build a type using a fresh variable, then quantify it."""
    var = TVar(1)
    return generalize(build(var), 0)


def _bi(name, arity, fn):
    return Builtin(name, arity, fn)


def _set(arr, index, value):
    arr.set(index, value)
    return UNIT_VALUE


def _print(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return UNIT_VALUE


def _write(text):
    """`print` without the newline. Flushes, so it interleaves correctly."""
    sys.stdout.write(text)
    sys.stdout.flush()
    return UNIT_VALUE


def _error(message):
    raise TurkeyPanic(message)


# ------------------------------------------------------------- the outside world
#
# `print`, `write` and `error` were the whole of the language's contact with
# anything outside itself, which meant a Turkey program could not read a file
# and so could not be a compiler (plan.txt item 9). These are the rest of the
# floor, and they are deliberately few: every one of them has to be written a
# second time in the C runtime, so the cost of a primitive is paid twice.
#
# Bytes, not text, on both doors. A file is not guaranteed to be well-formed
# UTF-8 and a `String` is, so the validating constructor stays where
# PRIMITIVES.md 4.7 puts it -- `String.fromBytes` in the library -- rather than
# being hidden inside a read. `Prim.readFileBytes` is total only after
# `Prim.fileCanRead` says so, which is the same predicate-plus-total-primitive
# split `Prim.floatCanParse`/`Prim.floatParse` already uses (PRIMITIVES.md 7.2)
# and for the same reason: the `Option` is built in the library, where `Some`
# and `None` are in scope.

_ARGS: list[str] = []


def set_args(args) -> None:
    """Record the arguments a program will see through `Prim.args`."""
    _ARGS[:] = list(args)


def program_args() -> list[str]:
    """What `set_args` recorded, for a host that runs the program natively.

    The native backend cannot read `_ARGS` the way `_args` does -- it hands
    the bytes to the runtime before the program starts -- so the two hosts
    share the setter and this is the other half of it.
    """
    return list(_ARGS)


def _args() -> ConValue:
    return _array_of_values(list(_ARGS))


def _file_can_read(path: str) -> bool:
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def _read_file_bytes(path: str) -> ConValue:
    try:
        with open(path, "rb") as handle:
            return _array_of_values(list(handle.read()))
    except OSError as exc:
        raise TurkeyPanic(f"cannot read {path}: {exc.strerror}") from None


def _write_file_bytes(path: str, data: ConValue):
    """Answers whether it worked, rather than panicking.

    A failed write is an ordinary thing for a program to want to report --
    a full disk, a read-only directory -- and unlike a failed read there is
    no predicate that could be asked first without lying about the race.
    """
    try:
        with open(path, "wb") as handle:
            handle.write(_array_bytes(data))
        return from_bool(True)
    except OSError:
        return from_bool(False)


def _stderr_write(text):
    sys.stderr.write(text)
    sys.stderr.flush()
    return UNIT_VALUE


def _exit(status: int):
    raise SystemExit(status)


# ------------------------------------------------------------------- integers
#
# `Int` is two's-complement signed 64-bit, and arithmetic *traps* rather than
# wrapping (PRIMITIVES.md 1.1). Python's own integers are unbounded, so the
# range check is not a redundant assertion about the host -- it is the
# semantics, and removing it would silently restore bignum `Int`.


def _trap(name: str, value: int) -> int:
    if INT_MIN <= value <= INT_MAX:
        return value
    raise TurkeyPanic(f"integer overflow in {name}")


def _wrap(value: int) -> int:
    """Reduce modulo 2^64 into two's-complement range."""
    return ((value + (1 << 63)) & ((1 << 64) - 1)) - (1 << 63)


def _int_div(a: int, b: int) -> int:
    """Truncating, not flooring (SPEC-DELTAS.md entry 18)."""
    if b == 0:
        raise TurkeyPanic("division by zero")
    # The one overflowing quotient: -2^63 / -1 is 2^63, which is not an `Int`.
    if a == INT_MIN and b == -1:
        raise TurkeyPanic("integer overflow in /")
    return -(-a // b) if (a < 0) != (b < 0) else a // b


def _int_rem(a: int, b: int) -> int:
    if b == 0:
        raise TurkeyPanic("remainder by zero")
    # `minInt % -1` is 0 and does not overflow, even though the quotient does,
    # so it must not be routed through `_int_div`.
    if b == -1:
        return 0
    return a - b * _int_div(a, b)


def _int_shift_amount(n: int) -> int:
    """Shifts panic outside 0..63 rather than masking or saturating.

    Masking is the C wart nobody predicts (`x << 64 == x`), and LLVM's `shl`
    is poison there, so a panic is the only answer that is both defined and
    unsurprising (PRIMITIVES.md 1.4).
    """
    if 0 <= n < 64:
        return n
    raise TurkeyPanic(f"shift amount {n} is not in 0..63")


# ---------------------------------------------------------------------- bytes


def _byte_from_int(n: int) -> int:
    if BYTE_MIN <= n <= BYTE_MAX:
        return n
    raise TurkeyPanic(f"{n} is not a Byte")


# ---------------------------------------------------------------------- float
#
# IEEE 754 binary64 throughout, in the default rounding mode. The two places
# Python is not that are division by zero (it raises) and the spelling of
# `Show` (its `repr` says `inf` and `nan`), and both are corrected here.


def _float_div(a: float, b: float) -> float:
    """IEEE division. Zero divisors give infinities and NaN, not a panic.

    The old panic here was the largest single departure from IEEE in the
    implementation (PRIMITIVES.md 3.1). A language that claims 754 cannot
    stop the program where the standard says to return an infinity.
    """
    if b == 0.0:
        if a != a or a == 0.0:
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b




_FLOAT_SPECIALS = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}


def _float_parse(text: str) -> float:
    """The inverse of `float_to_string`, for the strings it can invert.

    Accepts what the language writes -- including `Infinity` and `NaN`, which
    have no literal syntax -- and rejects Python's own spellings (`inf`,
    `nan`, `1_0.0`) so that the accepted language is the one this file
    defines rather than the host's.
    """
    if text in _FLOAT_SPECIALS:
        return _FLOAT_SPECIALS[text]
    body = text[1:] if text[:1] in "+-" else text
    mantissa, _, exponent = body.partition("e") if "e" in body else body.partition("E")
    whole, dot, fraction = mantissa.partition(".")
    if not (whole.isdigit() and dot and fraction.isdigit()):
        raise TurkeyPanic(f"'{text}' is not a Float")
    if exponent:
        digits = exponent[1:] if exponent[:1] in "+-" else exponent
        if not digits.isdigit():
            raise TurkeyPanic(f"'{text}' is not a Float")
    return float(text)


def _float_can_parse(text: str) -> bool:
    try:
        _float_parse(text)
    except TurkeyPanic:
        return False
    return True


def _float_bits(x: float) -> int:
    """The 64-bit pattern, as a signed `Int`.

    This is the only way to observe a NaN payload or the sign of a zero, and
    it is what `Float.totalCompare` is built out of. Nothing else in the
    language distinguishes `0.0` from `-0.0` (PRIMITIVES.md 3.2).
    """
    return struct.unpack("<q", struct.pack("<d", x))[0]


def _float_from_bits(n: int) -> float:
    return struct.unpack("<d", struct.pack("<q", _trap("floatFromBits", n)))[0]


def _float_fits_int(x: float) -> bool:
    """Whether truncating `x` toward zero lands inside `Int`.

    False for NaN and both infinities. The check exists because LLVM's
    `fptosi` is *poison* out of range, so an unchecked conversion would be
    undefined behaviour in the native backend rather than merely wrong
    (PRIMITIVES.md 3.4).
    """
    if x != x or x in (math.inf, -math.inf):
        return False
    return INT_MIN <= math.trunc(x) <= INT_MAX


def _float_truncate(x: float) -> int:
    if not _float_fits_int(x):
        raise TurkeyPanic(f"{float_to_string(x)} is not representable as an Int")
    return math.trunc(x)


def _float_round(x: float) -> float:
    """Round half away from zero, staying in `Float`.

    Not Python's `round`, which is half-to-even; ties-away is what `round`
    means to everyone who has not read the floating-point standard, and the
    ties-to-even rounding that IEEE mandates applies to the *results of
    arithmetic*, not to this function.
    """
    if x != x or x in (math.inf, -math.inf):
        return x
    return math.copysign(math.floor(abs(x) + 0.5), x)


# --------------------------------------------------------------------- string
#
# A `String` is an immutable, well-formed UTF-8 byte sequence (PRIMITIVES.md
# 4). The evaluator still holds a Python `str`, which is isomorphic to exactly
# that once surrogates are excluded -- and they are, by `Char` being a scalar
# value and by the lexer rejecting surrogate escapes. What is *not* inherited
# is the addressing: every primitive below is defined over the UTF-8 encoding,
# so no operation can observe Python's code-point indexing.


@functools.lru_cache(maxsize=4096)
def _utf8(s: str) -> bytes:
    """The encoding, memoized so that iterating a string stays linear."""
    return s.encode("utf-8")


def _string_byte_length(s: str) -> int:
    return len(_utf8(s))


def _string_byte_at(s: str, i: int) -> int:
    data = _utf8(s)
    if not 0 <= i < len(data):
        raise TurkeyPanic(f"string byte index out of bounds: {i}, length {len(data)}")
    return data[i]


def _utf8_width(lead: int) -> int:
    if lead < 0x80:
        return 1
    if lead >= 0xF0:
        return 4
    if lead >= 0xE0:
        return 3
    return 2


def _string_decode_at(s: str, i: int) -> str:
    """The scalar value beginning at byte offset `i`.

    Offsets come only from `Prim.stringNextIndex` and from searches, so they
    are always boundaries; landing mid-sequence is a library bug and panics
    rather than producing a replacement character.
    """
    data = _utf8(s)
    if not 0 <= i < len(data):
        raise TurkeyPanic(f"string byte index out of bounds: {i}, length {len(data)}")
    if 0x80 <= data[i] < 0xC0:  # a continuation byte is never a boundary
        raise TurkeyPanic(f"byte offset {i} is not a character boundary")
    return data[i : i + _utf8_width(data[i])].decode("utf-8")


def _string_next_index(s: str, i: int) -> int:
    data = _utf8(s)
    if not 0 <= i < len(data):
        raise TurkeyPanic(f"string byte index out of bounds: {i}, length {len(data)}")
    return i + _utf8_width(data[i])


def _string_slice(s: str, start: int, stop: int) -> str:
    data = _utf8(s)
    if not 0 <= start <= stop <= len(data):
        raise TurkeyPanic(f"string slice {start}..{stop} is out of bounds")
    try:
        return data[start:stop].decode("utf-8")
    except UnicodeDecodeError:
        raise TurkeyPanic(
            f"string slice {start}..{stop} does not fall on character boundaries"
        ) from None


def _string_find(haystack: str, needle: str, start: int) -> int:
    """Byte offset of the first occurrence at or after `start`, or -1.

    Searching bytes for bytes cannot land mid-sequence: UTF-8 is
    self-synchronizing, so a well-formed needle only ever matches at a
    boundary. That property is why the search API needs no index type
    (PRIMITIVES.md 4.3).
    """
    return _utf8(haystack).find(_utf8(needle), start)


def _string_rfind(haystack: str, needle: str) -> int:
    return _utf8(haystack).rfind(_utf8(needle))


def _array_of_values(values) -> ConValue:
    """Wrap a Python list as a `Data.Array.Array`."""
    arr = ArrayObj(len(values))
    for index, value in enumerate(values):
        arr.set(index, value)
    storage = RecordObj(
        "Data.Array#ArrayStorage", {"storage": arr, "length": len(values)})
    return ConValue("Data.Array#Array", (storage,), None)


def _string_to_bytes(s: str) -> ConValue:
    return _array_of_values(list(_utf8(s)))


def _array_values(xs: ConValue) -> list:
    storage = xs.args[0]
    data = storage.fields["storage"]
    return [data.get(i) for i in range(storage.fields["length"])]


def _string_concat_all(xs: ConValue) -> str:
    """Join many strings in one pass.

    Without this, building a string means `+` in a loop, which is quadratic
    -- which is exactly what `Data.String.fromChars` and `join` used to be.
    `Data.String.Builder` is this primitive plus an array (PRIMITIVES.md 4.2).
    """
    return "".join(_array_values(xs))


def _array_bytes(xs: ConValue) -> bytes:
    """Read a `Data.Array.Array Byte` back out as Python bytes."""
    return bytes(_array_values(xs))


def _string_is_valid_utf8(xs: ConValue) -> object:
    try:
        _array_bytes(xs).decode("utf-8")
    except UnicodeDecodeError:
        return from_bool(False)
    return from_bool(True)


def _string_from_bytes(xs: ConValue) -> str:
    try:
        return _array_bytes(xs).decode("utf-8")
    except UnicodeDecodeError:
        raise TurkeyPanic("bytes are not well-formed UTF-8") from None


# ----------------------------------------------------------------------- char
#
# A `Char` is a Unicode *scalar value*: 0..10FFFF with the surrogate range
# D800..DFFF excluded. The exclusion is not pedantry -- it is what makes the
# `String` invariant enforceable, since a surrogate `Char` could be written
# into a string that then would not be UTF-8 (PRIMITIVES.md 5).


def _char_from_int(n):
    if not is_scalar_value(n):
        raise TurkeyPanic(f"{n} is not a Unicode scalar value")
    return chr(n)


def _char_to_string(c):
    return c


def _num(name, ty, fn):
    return (mono(TFun([ty, ty], ty)), _bi(name, 2, fn))


def _cmp(name, ty, fn):
    # `fn` answers in Python; a turkey `Bool` is a constructor (M9.4), so the
    # wrapper is where the two representations meet.
    return (mono(TFun([ty, ty], BOOL)), _bi(name, 2, lambda a, b: from_bool(fn(a, b))))


def _pred(name, ty, fn):
    return (mono(TFun([ty], BOOL)), _bi(name, 1, lambda a: from_bool(fn(a))))


def _un(name, arg, ret, fn):
    return (mono(TFun([arg], ret)), _bi(name, 1, fn))


def _bin(name, left, right, ret, fn):
    return (mono(TFun([left, right], ret)), _bi(name, 2, fn))


_PRIM: dict[str, tuple] = {
    # Output. `print` and `write` themselves are prelude functions, one `show`
    # away; these are the two writes underneath.
    "Prim.print": (mono(TFun([STRING], UNIT)), _bi("Prim.print", 1, _print)),
    "Prim.write": (mono(TFun([STRING], UNIT)), _bi("Prim.write", 1, _write)),

    # Section 10: `error` diverges, so it can claim any result type.
    "Prim.error": (_scheme(lambda a: TFun([STRING], a)),
                   _bi("Prim.error", 1, _error)),

    # The outside world. `exit` diverges, so like `error` it claims any result.
    "Prim.stderrWrite": (mono(TFun([STRING], UNIT)),
                         _bi("Prim.stderrWrite", 1, _stderr_write)),
    "Prim.exit": (_scheme(lambda a: TFun([INT], a)), _bi("Prim.exit", 1, _exit)),
    "Prim.args": (mono(TFun([], array_of(STRING))), _bi("Prim.args", 0, _args)),
    "Prim.fileCanRead": _pred("Prim.fileCanRead", STRING, _file_can_read),
    "Prim.readFileBytes": _un(
        "Prim.readFileBytes", STRING, array_of(BYTE), _read_file_bytes),
    "Prim.writeFileBytes": (mono(TFun([STRING, array_of(BYTE)], BOOL)),
                            _bi("Prim.writeFileBytes", 2, _write_file_bytes)),

    # Fixed-length storage. Dynamic length and capacity are `Data.Array` policy.
    "Prim.arrayNew": (_scheme(lambda a: TFun([INT, a], raw_array_of(a))),
                      _bi("Prim.arrayNew", 2, lambda n, value: ArrayObj(n, value))),
    "Prim.arrayNewUninit": (_scheme(lambda a: TFun([INT], raw_array_of(a))),
                            _bi("Prim.arrayNewUninit", 1, lambda n: ArrayObj(n))),
    "Prim.arrayGet": (_scheme(lambda a: TFun([raw_array_of(a), INT], a)),
                      _bi("Prim.arrayGet", 2, lambda xs, i: xs.get(i))),
    "Prim.arraySet": (_scheme(lambda a: TFun([raw_array_of(a), INT, a], UNIT)),
                      _bi("Prim.arraySet", 3, _set)),
    "Prim.arrayLength": (_scheme(lambda a: TFun([raw_array_of(a)], INT)),
                         _bi("Prim.arrayLength", 1, lambda xs: xs.length)),

    # -- strings, addressed by byte -------------------------------------------
    #
    # There is no `Prim.stringLength` and no `Prim.stringChars`. The first was
    # Python's code-point count wearing a name that promised one answer to a
    # three-answer question; the second materialized an `Array Char` per
    # iteration site. Both are replaced by byte addressing plus a decode step,
    # which is what a lazy code-point view is made of (PRIMITIVES.md 4.1, 4.2).
    "Prim.stringConcat": (mono(TFun([STRING, STRING], STRING)),
                          _bi("Prim.stringConcat", 2, lambda a, b: a + b)),
    "Prim.stringConcatAll": _un(
        "Prim.stringConcatAll", array_of(STRING), STRING, _string_concat_all),
    "Prim.stringByteLength": _un(
        "Prim.stringByteLength", STRING, INT, _string_byte_length),
    "Prim.stringByteAt": _bin(
        "Prim.stringByteAt", STRING, INT, BYTE, _string_byte_at),
    "Prim.stringDecodeAt": _bin(
        "Prim.stringDecodeAt", STRING, INT, CHAR, _string_decode_at),
    "Prim.stringNextIndex": _bin(
        "Prim.stringNextIndex", STRING, INT, INT, _string_next_index),
    "Prim.stringSlice": (mono(TFun([STRING, INT, INT], STRING)),
                         _bi("Prim.stringSlice", 3, _string_slice)),
    "Prim.stringFind": (mono(TFun([STRING, STRING, INT], INT)),
                        _bi("Prim.stringFind", 3, _string_find)),
    "Prim.stringRfind": _bin(
        "Prim.stringRfind", STRING, STRING, INT, _string_rfind),
    "Prim.stringToBytes": _un(
        "Prim.stringToBytes", STRING, array_of(BYTE), _string_to_bytes),
    "Prim.stringFromBytes": _un(
        "Prim.stringFromBytes", array_of(BYTE), STRING, _string_from_bytes),
    "Prim.stringIsValidUtf8": (mono(TFun([array_of(BYTE)], BOOL)),
                               _bi("Prim.stringIsValidUtf8", 1,
                                   _string_is_valid_utf8)),

    # -- chars ----------------------------------------------------------------
    "Prim.charFromInt": _un("Prim.charFromInt", INT, CHAR, _char_from_int),
    "Prim.charIsScalar": _pred("Prim.charIsScalar", INT, is_scalar_value),
    "Prim.charToInt": _un("Prim.charToInt", CHAR, INT, ord),
    "Prim.charToString": _un("Prim.charToString", CHAR, STRING, _char_to_string),

    # -- bytes ----------------------------------------------------------------
    #
    # `Byte` has conversions and comparisons and no arithmetic at all. Byte
    # arithmetic goes through `Int`, which sidesteps the whole "does `u8 + u8`
    # wrap, trap, or promote" question (PRIMITIVES.md 2).
    "Prim.byteFromInt": _un("Prim.byteFromInt", INT, BYTE, _byte_from_int),
    "Prim.byteToInt": _un("Prim.byteToInt", BYTE, INT, lambda b: b),
    "Prim.byteEq": _cmp("Prim.byteEq", BYTE, lambda a, b: a == b),
    "Prim.byteLt": _cmp("Prim.byteLt", BYTE, lambda a, b: a < b),

    # -- conversions ----------------------------------------------------------
    "Prim.intToString": _un("Prim.intToString", INT, STRING, str),
    # Exact only to 2^53; past that it rounds to nearest, ties to even, which
    # is what Python's `float(int)` already does (PRIMITIVES.md 3.4).
    "Prim.intToFloat": _un("Prim.intToFloat", INT, FLOAT, float),
    "Prim.floatToString": _un(
        "Prim.floatToString", FLOAT, STRING, float_to_string),
    "Prim.floatParse": _un("Prim.floatParse", STRING, FLOAT, _float_parse),
    "Prim.floatCanParse": _pred("Prim.floatCanParse", STRING, _float_can_parse),
    "Prim.floatTruncate": _un(
        "Prim.floatTruncate", FLOAT, INT, _float_truncate),
    "Prim.floatFitsInt": _pred("Prim.floatFitsInt", FLOAT, _float_fits_int),

    # -- integer arithmetic, trapping -----------------------------------------
    "Prim.intAdd": _num("Prim.intAdd", INT, lambda a, b: _trap("+", a + b)),
    "Prim.intSub": _num("Prim.intSub", INT, lambda a, b: _trap("-", a - b)),
    "Prim.intMul": _num("Prim.intMul", INT, lambda a, b: _trap("*", a * b)),
    "Prim.intDiv": _num("Prim.intDiv", INT, _int_div),
    "Prim.intRem": _num("Prim.intRem", INT, _int_rem),
    "Prim.intNeg": _un("Prim.intNeg", INT, INT, lambda a: _trap("unary -", -a)),

    # -- integer arithmetic, wrapping -----------------------------------------
    #
    # Not a convenience. A hash mixer *must* wrap, and with a trapping `+` the
    # only alternative is to keep the accumulator artificially small, which is
    # what `Algorithm.Hash` used to do (PRIMITIVES.md 1.4).
    "Prim.intAddWrapping": _num(
        "Prim.intAddWrapping", INT, lambda a, b: _wrap(a + b)),
    "Prim.intSubWrapping": _num(
        "Prim.intSubWrapping", INT, lambda a, b: _wrap(a - b)),
    "Prim.intMulWrapping": _num(
        "Prim.intMulWrapping", INT, lambda a, b: _wrap(a * b)),
    "Prim.intNegWrapping": _un(
        "Prim.intNegWrapping", INT, INT, lambda a: _wrap(-a)),

    # -- bitwise --------------------------------------------------------------
    #
    # Functions, not operators: there is no operator budget for
    # `& | ^ ~ << >>`, and `Int.and` reads fine.
    "Prim.intAnd": _num("Prim.intAnd", INT, lambda a, b: a & b),
    "Prim.intOr": _num("Prim.intOr", INT, lambda a, b: a | b),
    "Prim.intXor": _num("Prim.intXor", INT, lambda a, b: a ^ b),
    "Prim.intNot": _un("Prim.intNot", INT, INT, lambda a: ~a),
    "Prim.intShl": _num(
        "Prim.intShl", INT, lambda a, b: _wrap(a << _int_shift_amount(b))),
    # Arithmetic, so the sign bit replicates. Python's `>>` on a signed int is
    # already an arithmetic shift.
    "Prim.intShr": _num(
        "Prim.intShr", INT, lambda a, b: a >> _int_shift_amount(b)),

    "Prim.not": (mono(TFun([BOOL], BOOL)),
                 _bi("Prim.not", 1, lambda a: from_bool(not truth(a)))),
    "Prim.intEq": _cmp("Prim.intEq", INT, lambda a, b: a == b),
    "Prim.intLt": _cmp("Prim.intLt", INT, lambda a, b: a < b),

    # -- float ----------------------------------------------------------------
    "Prim.floatAdd": _num("Prim.floatAdd", FLOAT, lambda a, b: a + b),
    "Prim.floatSub": _num("Prim.floatSub", FLOAT, lambda a, b: a - b),
    "Prim.floatMul": _num("Prim.floatMul", FLOAT, lambda a, b: a * b),
    "Prim.floatDiv": _num("Prim.floatDiv", FLOAT, _float_div),
    # Flips the sign bit, NaN included -- which is IEEE `negate`, and is what
    # Python's unary minus already does.
    "Prim.floatNeg": _un("Prim.floatNeg", FLOAT, FLOAT, lambda a: -a),
    "Prim.floatFmod": _num("Prim.floatFmod", FLOAT, math.fmod),
    "Prim.floatRemainder": _num("Prim.floatRemainder", FLOAT, math.remainder),
    "Prim.floatFloor": _un(
        "Prim.floatFloor", FLOAT, FLOAT,
        lambda x: x if (x != x or math.isinf(x)) else float(math.floor(x))),
    "Prim.floatCeil": _un(
        "Prim.floatCeil", FLOAT, FLOAT,
        lambda x: x if (x != x or math.isinf(x)) else float(math.ceil(x))),
    "Prim.floatRound": _un("Prim.floatRound", FLOAT, FLOAT, _float_round),
    "Prim.floatTrunc": _un(
        "Prim.floatTrunc", FLOAT, FLOAT,
        lambda x: x if (x != x or math.isinf(x)) else float(math.trunc(x))),
    "Prim.floatBits": _un("Prim.floatBits", FLOAT, INT, _float_bits),
    "Prim.floatFromBits": _un(
        "Prim.floatFromBits", INT, FLOAT, _float_from_bits),
    "Prim.floatIsNaN": _pred("Prim.floatIsNaN", FLOAT, lambda x: x != x),

    # All four comparisons are primitive, because `Ord Float` cannot inherit
    # any of the class defaults: `!lt(x, y)` says `gte(NaN, 1.0)` is true,
    # which is neither IEEE nor anything else (PRIMITIVES.md 3.2a).
    "Prim.floatEq": _cmp("Prim.floatEq", FLOAT, lambda a, b: a == b),
    "Prim.floatLt": _cmp("Prim.floatLt", FLOAT, lambda a, b: a < b),
    "Prim.floatLte": _cmp("Prim.floatLte", FLOAT, lambda a, b: a <= b),
    "Prim.floatGt": _cmp("Prim.floatGt", FLOAT, lambda a, b: a > b),
    "Prim.floatGte": _cmp("Prim.floatGte", FLOAT, lambda a, b: a >= b),

    "Prim.stringEq": _cmp("Prim.stringEq", STRING, lambda a, b: a == b),
    # Byte-lexicographic. For well-formed UTF-8 that is exactly
    # code-point-lexicographic, which is why comparing Python strings agrees
    # (PRIMITIVES.md 4.4).
    "Prim.stringLt": _cmp(
        "Prim.stringLt", STRING, lambda a, b: _utf8(a) < _utf8(b)),
    "Prim.charEq": _cmp("Prim.charEq", CHAR, lambda a, b: a == b),
    "Prim.charLt": _cmp("Prim.charLt", CHAR, lambda a, b: a < b),
    "Prim.boolEq": _cmp("Prim.boolEq", BOOL, lambda a, b: a.con == b.con),
    "Prim.boolLt": _cmp(
        "Prim.boolLt", BOOL, lambda a, b: a.con == BOOL_FALSE and b.con == BOOL_TRUE),
}

# The names a library module may write, and no other module may.
PRIM_NAMES = frozenset(_PRIM)


def initial_type_env() -> Env:
    """The environment every module is checked in. Scope is what narrows it."""
    env = Env()
    for name, (scheme, _value) in _PRIM.items():
        env.define(name, Binding(scheme, False))
    return env


def initial_values() -> dict[str, object]:
    return {name: value for name, (_scheme, value) in _PRIM.items()}


def initial_primitives() -> dict[str, object]:
    """Raw primitive callables for compiled code, without evaluator wrappers."""
    return {name: value.fn for name, (_scheme, value) in _PRIM.items()}
