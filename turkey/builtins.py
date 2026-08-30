"""The initial environment: primitives that exist before any user code.

v0 has no module system (SPEC-DELTAS.md entry 9), so what would live in
`Data.Array` and friends is seeded here instead. Each name is registered both
bare-qualified (`Array.push`) and fully qualified (`Data.Array.push`) so that
programs written against section 8.3 keep working once modules land.

Two environments come out of here, not one. `_CORE` is the surface language:
what a program may name. `_PRIM` is the machine underneath it -- integer
addition, the comparison that reads one string against another -- and is in
scope only while `turkey/prelude.py` is being checked, because every one of
those operations now has a name in the surface language already -- an operator,
or `print`. `Prim.intAdd` is what `instance Add Int` is written in terms of and
`Prim.print` is what `print` is, and nothing else is entitled to say either.
"""

from __future__ import annotations

import sys

from .errors import TurkeyPanic
from .constraints import Binding, Env
from .prelude import OPTION, OPTION_NONE, OPTION_SOME
from .types import (
    BOOL, CHAR, FLOAT, INT, STAR, STRING, UNIT, KFun, TCon, TFun, TVar, apply,
    array_of, generalize, mono,
)
from .values import UNIT as UNIT_VALUE, ArrayObj, Builtin, ConValue, from_bool


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
    # Sized exactly, so `capacity` is not misleading to a program that reads it.
    arr = ArrayObj(len(s))
    for ch in s:
        arr.push(ch)
    return arr


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


# name -> (type scheme, runtime value). Aliases are added below.
_CORE: dict[str, tuple] = {
    # Section 10 note: `error` diverges, so it can claim any result type.
    "error": (_scheme(lambda a: TFun([STRING], a)), _bi("error", 1, _error)),

    # Section 8.3, the Data.Array module.
    "Array.new": (
        _scheme(lambda a: TFun([INT], array_of(a))),
        _bi("Array.new", 1, lambda n: ArrayObj(n)),
    ),
    "Array.push": (
        _scheme(lambda a: TFun([array_of(a), a], UNIT)),
        _bi("Array.push", 2, _push),
    ),
    "Array.pop": (
        # `Option` is declared in the prelude, and a `TCon` is compared by
        # name, so naming it here needs no `DeclTable` -- the same trick that
        # lets `BOOL` above mean the prelude's `Bool`.
        _scheme(lambda a: TFun([array_of(a)], apply(TCon(OPTION, KFun(STAR, STAR)), [a]))),
        _bi("Array.pop", 1, _pop),
    ),

    "String.length": (
        mono(TFun([STRING], INT)), _bi("String.length", 1, lambda s: len(s)),
    ),
    "String.chars": (
        mono(TFun([STRING], array_of(CHAR))), _bi("String.chars", 1, _chars),
    ),
    "Char.fromInt": (
        mono(TFun([INT], CHAR)), _bi("Char.fromInt", 1, _char_from_int),
    ),
    "Char.toInt": (mono(TFun([CHAR], INT)), _bi("Char.toInt", 1, lambda c: ord(c))),

    # Conversions. The `Show` instances are written in terms of these, and a
    # program may still call them directly (SPEC-DELTAS.md entry 22).
    "Int.toString": (mono(TFun([INT], STRING)), _bi("Int.toString", 1, lambda n: str(n))),
    "Float.toString": (
        mono(TFun([FLOAT], STRING)), _bi("Float.toString", 1, lambda x: repr(x)),
    ),
    "Bool.toString": (
        mono(TFun([BOOL], STRING)),
        _bi("Bool.toString", 1, lambda b: b.con),
    ),
    "Char.toString": (
        mono(TFun([CHAR], STRING)), _bi("Char.toString", 1, _char_to_string),
    ),
}

def _num(name, ty, fn):
    return (mono(TFun([ty, ty], ty)), _bi(name, 2, fn))


def _cmp(name, ty, fn):
    # `fn` answers in Python; a turkey `Bool` is a constructor (M9.4), so the
    # wrapper is where the two representations meet.
    return (mono(TFun([ty, ty], BOOL)), _bi(name, 2, lambda a, b: from_bool(fn(a, b))))


# The machine operations the prelude's instances are defined in terms of. Not
# part of the surface language: `initial_type_env()` leaves them out, so a
# program that writes `Prim.intAdd` is told the name is not defined. Their
# *values* are in `initial_values()` regardless, because an instance method
# elaborated against the prelude's environment still has to run.
_PRIM: dict[str, tuple] = {
    # Output. `print` and `write` themselves are prelude functions now, one
    # `show` away (`turkey/prelude.py`); these are the two writes underneath.
    "Prim.print": (mono(TFun([STRING], UNIT)), _bi("Prim.print", 1, _print)),
    "Prim.write": (mono(TFun([STRING], UNIT)), _bi("Prim.write", 1, _write)),

    "Prim.intAdd": _num("Prim.intAdd", INT, lambda a, b: a + b),
    "Prim.intSub": _num("Prim.intSub", INT, lambda a, b: a - b),
    "Prim.intMul": _num("Prim.intMul", INT, lambda a, b: a * b),
    "Prim.intDiv": _num("Prim.intDiv", INT, _int_div),
    "Prim.intRem": _num("Prim.intRem", INT, _int_rem),
    "Prim.intNeg": (mono(TFun([INT], INT)), _bi("Prim.intNeg", 1, lambda a: -a)),
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
        "Prim.boolLt", BOOL, lambda a, b: a.con == "False" and b.con == "True"),
}

# design.md writes these as `Data.Array.new` and so on; accept both spellings.
_ALIAS_PREFIXES = {"Array": "Data.Array", "String": "Data.String", "Int": "Data.Int",
                   "Bool": "Data.Bool", "Float": "Data.Float", "Char": "Data.Char"}

BUILTINS: dict[str, tuple] = dict(_CORE)
for _name, _entry in _CORE.items():
    if "." in _name:
        _prefix, _rest = _name.split(".", 1)
        if _prefix in _ALIAS_PREFIXES:
            BUILTINS[f"{_ALIAS_PREFIXES[_prefix]}.{_rest}"] = _entry


# The names a library module may additionally write. Module resolution is what
# enforces that now (`turkey/modules.py`): the environment holds every builtin,
# and a user module's scope simply does not spell these.
PRIM_NAMES = frozenset(_PRIM)


def initial_type_env(prims: bool = False) -> Env:
    """The names a program may use. `prims` is for the prelude alone."""
    env = Env()
    table = {**BUILTINS, **_PRIM} if prims else BUILTINS
    for name, (scheme, _value) in table.items():
        env.define(name, Binding(scheme, False))
    return env


def initial_values() -> dict[str, object]:
    return {name: value
            for name, (_scheme, value) in {**BUILTINS, **_PRIM}.items()}
