"""The machine operations, and nothing else.

Everything a program can name is written in the language now: the classes and
`print` in `turkey/lib/Prelude.gob`, and `Array.push`, `Int.toString` and the
rest in `turkey/lib/Data/*.gob` (SPEC-DELTAS.md entry 42). What is left here is
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

import math
import struct

from . import foreign
from .errors import TurkeyPanic
from .constraints import Binding, Env
from .prelude import BOOL_FALSE, BOOL_TRUE
from .types import (
    BOOL, BYTE, BYTE_MAX, BYTE_MIN, CHAR, FLOAT, INT, INT_MAX, INT_MIN,
    RAW_PTR, STRING, UNIT, TFun, TVar, float_to_string, generalize,
    is_scalar_value, mono, raw_array_of,
)
from .values import (
    RAW_HEAP, UNIT as UNIT_VALUE, ArrayObj, Builtin,
    from_bool, make_string, string_text, truth,
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


def _error(message):
    raise TurkeyPanic(string_text(message))


# ------------------------------------------------------------- the outside world
#
# `print`, `write` and `error` were once the whole of the language's contact
# with anything outside itself, which meant a Turkey program could not read a
# file and so could not be a compiler (plan.txt item 9). Arguments, `exit`, the
# error stream and two file doors followed, each written a second time in the C
# runtime, so the cost of a primitive was paid twice.
#
# TIX-65 wrote the streams and the file doors in Turkey (`lib/System/IO.gob`)
# over `open`, `read`, `write` and `close`, so what reaches a file or a stream
# now reaches it through `turkey/foreign.py`'s models and nothing here is
# written twice. What is left is `exit` and the arguments, whose state lives
# where the host starts the program.

_ARGS: list[str] = []


def set_args(args) -> None:
    """Record the arguments a program will see through `System.Env.args`.

    And clear the raw heap, which is the other thing a run starts with. It is
    reset here rather than in `driver.run` because this is already the
    once-per-run hook and `tests/test_native.py` builds its reference in
    process: without it, an address would depend on which programs ran first.
    """
    _ARGS[:] = list(args)
    RAW_HEAP.reset()
    foreign.reset()


def program_args() -> list[str]:
    """What `set_args` recorded, for a host that runs the program natively.

    The native backend cannot read `_ARGS` the way `turkey/foreign.py` does -- it hands
    the bytes to the runtime before the program starts -- so the two hosts
    share the setter and this is the other half of it.
    """
    return list(_ARGS)


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


_U64 = (1 << 64) - 1


def _signed64(value: int) -> int:
    """The `Int` an address or a difference of addresses is seen as.

    Two's complement, because `Int` is signed 64-bit and the native side does
    nothing at all here -- the bits are the bits.
    """
    value &= _U64
    return value - (1 << 64) if value >= (1 << 63) else value


def _raw_char(code: int):
    if not is_scalar_value(code):
        raise TurkeyPanic(
            f"raw pointer: {code} is not a Unicode scalar value")
    return chr(code)


def _raw_load(address, offset, width, signed=False):
    return RAW_HEAP.load(address + offset, width, signed=signed)


def _raw_store_prim(name, ty, width, encode):
    """One `Prim.store*`. The encoder is what turns the language's value into
    the bits the native backend would have stored."""
    def store(address, offset, value):
        RAW_HEAP.store(address + offset, width, encode(value))
        return UNIT_VALUE
    return (mono(TFun([RAW_PTR, INT, ty], UNIT)), _bi(name, 3, store))


_PRIM: dict[str, tuple] = {
    # Section 10: `error` diverges, so it can claim any result type.
    "Prim.error": (_scheme(lambda a: TFun([STRING], a)),
                   _bi("Prim.error", 1, _error)),

    # The outside world. `exit` diverges, so like `error` it claims any result.
    "Prim.exit": (_scheme(lambda a: TFun([INT], a)), _bi("Prim.exit", 1, _exit)),

    # Fixed-length storage. Dynamic length and capacity are `Data.Array` policy.
    "Prim.arrayNew": (_scheme(lambda a: TFun([INT, a], raw_array_of(a))),
                      _bi("Prim.arrayNew", 2, lambda n, value: ArrayObj(n, value))),
    "Prim.arrayNewUninit": (_scheme(lambda a: TFun([INT], raw_array_of(a))),
                            _bi("Prim.arrayNewUninit", 1, lambda n: ArrayObj(n))),
    # The conversion half of a checked cast (ERRORS.md step 5). Total, and
    # unchecked on purpose: `Data.Error.cast` compares the two type reps first
    # and calls this only when they are equal, which is the predicate-plus-
    # total-primitive split PRIMITIVES.md 7.2 already uses for `floatParse` and
    # `charFromInt`. It is spellable only from a library module, like every
    # other `Prim.` name, so no unchecked coercion reaches ordinary Turkey.
    #
    # Here it is the identity: the evaluator's values carry their own tags, so
    # there is no representation to change. The native backend is where this
    # does work, converting to the layout the result type is held at.
    "Prim.castAs": (generalize(TFun([TVar(1)], TVar(1)), 0),
                    _bi("Prim.castAs", 1, lambda x: x)),
    "Prim.arrayGet": (_scheme(lambda a: TFun([raw_array_of(a), INT], a)),
                      _bi("Prim.arrayGet", 2, lambda xs, i: xs.get(i))),
    "Prim.arraySet": (_scheme(lambda a: TFun([raw_array_of(a), INT, a], UNIT)),
                      _bi("Prim.arraySet", 3, _set)),
    "Prim.arrayLength": (_scheme(lambda a: TFun([raw_array_of(a)], INT)),
                         _bi("Prim.arrayLength", 1, lambda xs: xs.length)),

    # -- chars ----------------------------------------------------------------
    "Prim.charFromInt": _un("Prim.charFromInt", INT, CHAR, _char_from_int),
    "Prim.charIsScalar": _pred("Prim.charIsScalar", INT, is_scalar_value),
    "Prim.charToInt": _un("Prim.charToInt", CHAR, INT, ord),

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
    # Exact only to 2^53; past that it rounds to nearest, ties to even, which
    # is what Python's `float(int)` already does (PRIMITIVES.md 3.4).
    "Prim.intToFloat": _un("Prim.intToFloat", INT, FLOAT, float),
    # Host-side until TIX-75 writes them in Turkey: the one place left where
    # a `String` crosses into Python and back.
    "Prim.floatToString": _un(
        "Prim.floatToString", FLOAT, STRING,
        lambda x: make_string(float_to_string(x))),
    "Prim.floatParse": _un(
        "Prim.floatParse", STRING, FLOAT, lambda s: _float_parse(string_text(s))),
    "Prim.floatCanParse": _pred(
        "Prim.floatCanParse", STRING, lambda s: _float_can_parse(string_text(s))),
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

    "Prim.charEq": _cmp("Prim.charEq", CHAR, lambda a, b: a == b),
    "Prim.charLt": _cmp("Prim.charLt", CHAR, lambda a, b: a < b),
    "Prim.boolEq": _cmp("Prim.boolEq", BOOL, lambda a, b: a.con == b.con),
    "Prim.boolLt": _cmp(
        "Prim.boolLt", BOOL, lambda a, b: a.con == BOOL_FALSE and b.con == BOOL_TRUE),

    # Raw memory (TIX-61). `malloc` and `free` were primitives here too,
    # behind C wrappers written only because there was no way to declare them;
    # TIX-62 declares them in `lib/Unsafe/Libc.gob` and they are gone.

    # Pointer arithmetic. Wrapping at 64 bits, not trapping: an address near
    # the top of the space is not an overflow, and the language's checked `+`
    # is not what these spell. The integer an address maps to is unspecified
    # beyond round-tripping, so nothing may print one.
    "Prim.ptrAdd": _bin("Prim.ptrAdd", RAW_PTR, INT, RAW_PTR,
                        lambda p, n: (p + n) & _U64),
    "Prim.ptrDiff": _bin("Prim.ptrDiff", RAW_PTR, RAW_PTR, INT,
                         lambda a, b: _signed64(a - b)),
    "Prim.ptrNull": (mono(TFun([], RAW_PTR)), _bi("Prim.ptrNull", 0, lambda: 0)),
    "Prim.ptrIsNull": _pred("Prim.ptrIsNull", RAW_PTR, lambda p: p == 0),
    "Prim.ptrEq": _cmp("Prim.ptrEq", RAW_PTR, lambda a, b: a == b),
    "Prim.ptrToInt": _un("Prim.ptrToInt", RAW_PTR, INT, _signed64),
    "Prim.ptrFromInt": _un("Prim.ptrFromInt", INT, RAW_PTR, lambda n: n & _U64),

    # Raw load and store, one name per representation, because the width has
    # to be known where the instruction is emitted. One language type per
    # class, which is forced: there is exactly one of each.
    #
    # Little-endian and two's complement, which is arm64 and LLVM on the only
    # target. `i1` touches one byte, as LLVM's `load i1` does -- not the eight
    # an `Array Bool` element gets, because raw memory is someone else's
    # layout and an array slot is this compiler's.
    "Prim.loadI1": _bin("Prim.loadI1", RAW_PTR, INT, BOOL,
                        lambda p, o: from_bool(_raw_load(p, o, 1) != 0)),
    "Prim.loadI8": _bin("Prim.loadI8", RAW_PTR, INT, BYTE,
                        lambda p, o: _raw_load(p, o, 1)),
    # A `Char` is a one-character `str` on this host and a scalar value in
    # the language, so the bits go through `chr`/`ord`. A stored pattern that
    # is not a scalar value is undefined natively and panics here.
    "Prim.loadI32": _bin("Prim.loadI32", RAW_PTR, INT, CHAR,
                         lambda p, o: _raw_char(_raw_load(p, o, 4))),
    "Prim.loadI64": _bin("Prim.loadI64", RAW_PTR, INT, INT,
                         lambda p, o: _raw_load(p, o, 8, signed=True)),
    "Prim.loadF64": _bin("Prim.loadF64", RAW_PTR, INT, FLOAT,
                         lambda p, o: struct.unpack(
                             "<d", _raw_load(p, o, 8).to_bytes(8, "little"))[0]),
    "Prim.loadPtr": _bin("Prim.loadPtr", RAW_PTR, INT, RAW_PTR,
                         lambda p, o: _raw_load(p, o, 8)),

    "Prim.storeI1": _raw_store_prim("Prim.storeI1", BOOL, 1,
                                    lambda v: 1 if truth(v) else 0),
    "Prim.storeI8": _raw_store_prim("Prim.storeI8", BYTE, 1, lambda v: v),
    "Prim.storeI32": _raw_store_prim("Prim.storeI32", CHAR, 4, ord),
    "Prim.storeI64": _raw_store_prim("Prim.storeI64", INT, 8, lambda v: v),
    "Prim.storeF64": _raw_store_prim(
        "Prim.storeF64", FLOAT, 8,
        lambda v: int.from_bytes(struct.pack("<d", v), "little")),
    "Prim.storePtr": _raw_store_prim("Prim.storePtr", RAW_PTR, 8, lambda v: v),
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
