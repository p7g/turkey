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
"""

from __future__ import annotations

import sys

from .errors import TurkeyPanic
from .constraints import Binding, Env
from .prelude import BOOL_FALSE, BOOL_TRUE, OPTION, OPTION_NONE, OPTION_SOME
from .types import (
    BOOL, CHAR, FLOAT, INT, STAR, STRING, UNIT, KFun, TCon, TFun, TVar, apply,
    array_of, generalize, mono, raw_array_of,
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


def _push(arr, value):
    arr.push(value)
    return UNIT_VALUE


def _pop(arr):
    """Total, unlike `ArrayObj.pop`: an empty array is an ordinary answer.

    The array's own `pop` still raises, because reading past the length is a
    program bug wherever else it happens; it is only *this* call, the one whose
    empty case a program is expected to handle, that answers with `Option`.
    """
    if arr.length == 0:
        return ConValue(OPTION_NONE, ())
    return ConValue(OPTION_SOME, (arr.pop(),))


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


def _chars(s):
    arr = ArrayObj(len(s))
    for ch in s:
        arr.push(ch)
    return ConValue("Data.Array#Array", (arr,), None)


def _char_from_int(n):
    if n < 0 or n > 0x10FFFF:
        raise TurkeyPanic(f"{n} is not a character code")
    return chr(n)


def _error(message):
    raise TurkeyPanic(message)


def _char_to_string(c):
    return c


def _int_div(a: int, b: int) -> int:
    """Truncating, not flooring (SPEC-DELTAS.md entry 18)."""
    if b == 0:
        raise TurkeyPanic("division by zero")
    return -(-a // b) if (a < 0) != (b < 0) else a // b


def _int_rem(a: int, b: int) -> int:
    if b == 0:
        raise TurkeyPanic("remainder by zero")
    return a - b * _int_div(a, b)


def _float_div(a: float, b: float) -> float:
    if b == 0.0:
        raise TurkeyPanic("division by zero")
    return a / b


def _num(name, ty, fn):
    return (mono(TFun([ty, ty], ty)), _bi(name, 2, fn))


def _cmp(name, ty, fn):
    # `fn` answers in Python; a turkey `Bool` is a constructor (M9.4), so the
    # wrapper is where the two representations meet.
    return (mono(TFun([ty, ty], BOOL)), _bi(name, 2, lambda a, b: from_bool(fn(a, b))))


_PRIM: dict[str, tuple] = {
    # Output. `print` and `write` themselves are prelude functions, one `show`
    # away; these are the two writes underneath.
    "Prim.print": (mono(TFun([STRING], UNIT)), _bi("Prim.print", 1, _print)),
    "Prim.write": (mono(TFun([STRING], UNIT)), _bi("Prim.write", 1, _write)),

    # Section 10: `error` diverges, so it can claim any result type.
    "Prim.error": (_scheme(lambda a: TFun([STRING], a)),
                   _bi("Prim.error", 1, _error)),

    # What `Data.Array` is written in terms of (section 8.3). Only `pop` is
    # more than a rename: it is total where `ArrayObj.pop` is not, because an
    # empty array is an ordinary answer for the one call whose empty case a
    # program is expected to handle (delta 37).
    "Prim.arrayNew": (_scheme(lambda a: TFun([INT], raw_array_of(a))),
                      _bi("Prim.arrayNew", 1, lambda n: ArrayObj(n))),
    "Prim.arrayPush": (_scheme(lambda a: TFun([raw_array_of(a), a], UNIT)),
                       _bi("Prim.arrayPush", 2, _push)),
    "Prim.arrayPop": (
        # `Option` is declared in `Data.Option` and a `TCon` is compared by
        # name, so naming it here needs no `DeclTable` -- the same trick that
        # lets `BOOL` above mean the `Bool` the library declares.
        _scheme(lambda a: TFun([raw_array_of(a)], apply(TCon(OPTION, KFun(STAR, STAR)), [a]))),
        _bi("Prim.arrayPop", 1, _pop),
    ),
    "Prim.arrayGet": (_scheme(lambda a: TFun([raw_array_of(a), INT], a)),
                      _bi("Prim.arrayGet", 2, lambda xs, i: xs.get(i))),
    "Prim.arraySet": (_scheme(lambda a: TFun([raw_array_of(a), INT, a], UNIT)),
                      _bi("Prim.arraySet", 3, _set)),
    "Prim.arrayLength": (_scheme(lambda a: TFun([raw_array_of(a)], INT)),
                         _bi("Prim.arrayLength", 1, lambda xs: xs.length)),

    "Prim.stringConcat": (mono(TFun([STRING, STRING], STRING)),
                          _bi("Prim.stringConcat", 2, lambda a, b: a + b)),
    "Prim.stringLength": (mono(TFun([STRING], INT)),
                          _bi("Prim.stringLength", 1, lambda s: len(s))),
    "Prim.stringChars": (mono(TFun([STRING], array_of(CHAR))),
                         _bi("Prim.stringChars", 1, _chars)),

    "Prim.charFromInt": (mono(TFun([INT], CHAR)),
                         _bi("Prim.charFromInt", 1, _char_from_int)),
    "Prim.charToInt": (mono(TFun([CHAR], INT)),
                       _bi("Prim.charToInt", 1, lambda c: ord(c))),
    "Prim.charToString": (mono(TFun([CHAR], STRING)),
                          _bi("Prim.charToString", 1, _char_to_string)),

    "Prim.intToString": (mono(TFun([INT], STRING)),
                         _bi("Prim.intToString", 1, lambda n: str(n))),
    "Prim.intToFloat": (mono(TFun([INT], FLOAT)),
                         _bi("Prim.intToFloat", 1, lambda n: float(n))),
    "Prim.floatToString": (mono(TFun([FLOAT], STRING)),
                           _bi("Prim.floatToString", 1, lambda x: repr(x))),

    "Prim.intAdd": _num("Prim.intAdd", INT, lambda a, b: a + b),
    "Prim.intSub": _num("Prim.intSub", INT, lambda a, b: a - b),
    "Prim.intMul": _num("Prim.intMul", INT, lambda a, b: a * b),
    "Prim.intDiv": _num("Prim.intDiv", INT, _int_div),
    "Prim.intRem": _num("Prim.intRem", INT, _int_rem),
    "Prim.intNeg": (mono(TFun([INT], INT)), _bi("Prim.intNeg", 1, lambda a: -a)),
    # The one operator that is not a class method (design.md 8.2). It was
    # inlined by the evaluator while the evaluator walked the surface tree;
    # Core has no `!` node, so it is an ordinary call like everything else.
    "Prim.not": (mono(TFun([BOOL], BOOL)),
                 _bi("Prim.not", 1, lambda a: from_bool(not truth(a)))),
    "Prim.intEq": _cmp("Prim.intEq", INT, lambda a, b: a == b),
    "Prim.intLt": _cmp("Prim.intLt", INT, lambda a, b: a < b),

    "Prim.floatAdd": _num("Prim.floatAdd", FLOAT, lambda a, b: a + b),
    "Prim.floatSub": _num("Prim.floatSub", FLOAT, lambda a, b: a - b),
    "Prim.floatMul": _num("Prim.floatMul", FLOAT, lambda a, b: a * b),
    "Prim.floatDiv": _num("Prim.floatDiv", FLOAT, _float_div),
    "Prim.floatNeg": (mono(TFun([FLOAT], FLOAT)), _bi("Prim.floatNeg", 1, lambda a: -a)),
    "Prim.floatEq": _cmp("Prim.floatEq", FLOAT, lambda a, b: a == b),
    "Prim.floatLt": _cmp("Prim.floatLt", FLOAT, lambda a, b: a < b),

    "Prim.stringEq": _cmp("Prim.stringEq", STRING, lambda a, b: a == b),
    "Prim.stringLt": _cmp("Prim.stringLt", STRING, lambda a, b: a < b),
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
