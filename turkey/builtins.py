"""The initial environment: primitives that exist before any user code.

v0 has no module system (SPEC-DELTAS.md entry 9), so what would live in
`Data.Array` and friends is seeded here instead. Each name is registered both
bare-qualified (`Array.push`) and fully qualified (`Data.Array.push`) so that
programs written against section 8.3 keep working once modules land.
"""

from __future__ import annotations

import sys

from .errors import TurkeyPanic
from .constraints import Binding, Env
from .types import (
    BOOL, CHAR, FLOAT, INT, STRING, UNIT, TCon, TFun, TVar, generalize, mono,
)
from .values import UNIT as UNIT_VALUE, ArrayObj, Builtin


def _array_of(element):
    return TCon("Array", [element])


def _scheme(build):
    """Build a type using a fresh variable, then quantify it."""
    var = TVar(1)
    return generalize(build(var), 0)


def _bi(name, arity, fn):
    return Builtin(name, arity, fn)


def _push(arr, value):
    arr.push(value)
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


# name -> (type scheme, runtime value). Aliases are added below.
_CORE: dict[str, tuple] = {
    # Section 10 note: `error` diverges, so it can claim any result type.
    "error": (_scheme(lambda a: TFun([STRING], a)), _bi("error", 1, _error)),

    # Section 8.3, the Data.Array module.
    "Array.new": (
        _scheme(lambda a: TFun([INT], _array_of(a))),
        _bi("Array.new", 1, lambda n: ArrayObj(n)),
    ),
    "Array.push": (
        _scheme(lambda a: TFun([_array_of(a), a], UNIT)),
        _bi("Array.push", 2, _push),
    ),
    "Array.pop": (
        _scheme(lambda a: TFun([_array_of(a)], a)),
        _bi("Array.pop", 1, lambda arr: arr.pop()),
    ),

    # Section 8.2 defers equality and ordering on non-Int types to named
    # functions in each type's module.
    "String.eq": (mono(TFun([STRING, STRING], BOOL)), _bi("String.eq", 2, lambda a, b: a == b)),
    "String.lt": (mono(TFun([STRING, STRING], BOOL)), _bi("String.lt", 2, lambda a, b: a < b)),
    "String.length": (
        mono(TFun([STRING], INT)), _bi("String.length", 1, lambda s: len(s)),
    ),
    "Bool.eq": (mono(TFun([BOOL, BOOL], BOOL)), _bi("Bool.eq", 2, lambda a, b: a == b)),
    "Float.lt": (mono(TFun([FLOAT, FLOAT], BOOL)), _bi("Float.lt", 2, lambda a, b: a < b)),
    "Char.eq": (mono(TFun([CHAR, CHAR], BOOL)), _bi("Char.eq", 2, lambda a, b: a == b)),
    "String.chars": (
        mono(TFun([STRING], _array_of(CHAR))), _bi("String.chars", 1, _chars),
    ),
    "Char.fromInt": (
        mono(TFun([INT], CHAR)), _bi("Char.fromInt", 1, _char_from_int),
    ),
    "Char.toInt": (mono(TFun([CHAR], INT)), _bi("Char.toInt", 1, lambda c: ord(c))),

    # Conversions and output. design.md has no I/O; a prototype that cannot
    # print cannot be tested, so these are an addition (SPEC-DELTAS.md).
    "Int.toString": (mono(TFun([INT], STRING)), _bi("Int.toString", 1, lambda n: str(n))),
    "Float.toString": (
        mono(TFun([FLOAT], STRING)), _bi("Float.toString", 1, lambda x: repr(x)),
    ),
    "Bool.toString": (
        mono(TFun([BOOL], STRING)),
        _bi("Bool.toString", 1, lambda b: "true" if b else "false"),
    ),
    "Char.toString": (
        mono(TFun([CHAR], STRING)), _bi("Char.toString", 1, _char_to_string),
    ),
    "print": (mono(TFun([STRING], UNIT)), _bi("print", 1, _print)),
    "write": (mono(TFun([STRING], UNIT)), _bi("write", 1, _write)),
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


def initial_type_env() -> Env:
    env = Env()
    for name, (scheme, _value) in BUILTINS.items():
        env.define(name, Binding(scheme, False))
    return env


def initial_values() -> dict[str, object]:
    return {name: value for name, (_scheme, value) in BUILTINS.items()}
