"""Runtime values.

Section 6.2 leaves the representation of values to the implementation, so these
are ordinary Python objects. What does matter is section 6.3: single-variant
records and arrays have reference semantics, which falls out of using mutable
objects and never copying them.
"""

from __future__ import annotations

from .errors import TurkeyPanic
from .types import short_name


class Unit:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "()"


UNIT = Unit()


class Cell:
    """A mutable binding: the shared runtime representation of Core's CRef."""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __repr__(self) -> str:
        return f"<cell {self.value!r}>"


class Uninitialized:
    """Occupies an array slot that has been allocated but never written.

    Reading one is undefined behaviour in the surface language; the prototype
    turns it into a panic rather than handing back a value out of nowhere.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<uninitialized>"


UNINIT = Uninitialized()


class ArrayObj:
    """A dynamically sized array with a user-visible length and capacity.

    Section 8.3 exposes both fields for reading *and* writing, so capacity is
    tracked explicitly rather than inferred from the backing list's size.
    """

    __slots__ = ("slots", "length")

    def __init__(self, capacity: int):
        if capacity < 0:
            raise TurkeyPanic(f"array capacity cannot be negative (got {capacity})")
        self.slots: list[object] = [UNINIT] * capacity
        self.length = 0

    @property
    def capacity(self) -> int:
        return len(self.slots)

    def _check(self, index: int, what: str) -> None:
        # SPEC-DELTAS.md entry 8: bounds are the length, not the capacity.
        if not isinstance(index, int):
            raise TurkeyPanic(f"array index must be an Int, got {index!r}")
        if index < 0 or index >= self.length:
            raise TurkeyPanic(
                f"array index out of bounds: {what} index {index}, length {self.length}"
            )

    def get(self, index: int) -> object:
        self._check(index, "read at")
        value = self.slots[index]
        if value is UNINIT:
            raise TurkeyPanic(
                f"read of uninitialized array slot {index}; the length was raised "
                f"past what has actually been written"
            )
        return value

    def set(self, index: int, value: object) -> None:
        self._check(index, "write at")
        self.slots[index] = value

    def push(self, value: object) -> None:
        if self.length == len(self.slots):
            self.set_capacity(max(1, len(self.slots) * 2))
        self.slots[self.length] = value
        self.length += 1

    def pop(self) -> object:
        if self.length == 0:
            raise TurkeyPanic("pop from an empty array")
        self.length -= 1
        value = self.slots[self.length]
        self.slots[self.length] = UNINIT
        if value is UNINIT:
            raise TurkeyPanic(f"pop of uninitialized array slot {self.length}")
        return value

    def set_length(self, new_length: int) -> None:
        if new_length < 0 or new_length > len(self.slots):
            raise TurkeyPanic(
                f"length must be between 0 and the capacity {len(self.slots)}, "
                f"got {new_length}"
            )
        for i in range(new_length, self.length):
            self.slots[i] = UNINIT
        self.length = new_length

    def set_capacity(self, new_capacity: int) -> None:
        if new_capacity < 0:
            raise TurkeyPanic(f"capacity cannot be negative (got {new_capacity})")
        if new_capacity < len(self.slots):
            self.slots = self.slots[:new_capacity]
            self.length = min(self.length, new_capacity)
        else:
            self.slots.extend([UNINIT] * (new_capacity - len(self.slots)))

    def __repr__(self) -> str:
        return "[" + ", ".join(repr(self.slots[i]) for i in range(self.length)) + "]"


class RecordObj:
    """A single-variant record: mutable, with reference semantics (section 6.3)."""

    __slots__ = ("con", "fields")

    def __init__(self, con: str, fields: dict[str, object]):
        self.con = con
        self.fields = fields

    def positional(self) -> tuple:
        """The fields in declaration order.

        Both places a `RecordObj` is built -- `ConstructorFn.build` below and
        `Evaluator._eval_ERecord` -- fill `fields` by walking
        `ConInfo.field_names`, so insertion order *is* declaration order. That
        is what lets a positional pattern match a record variant.
        """
        return tuple(self.fields.values())

    def __repr__(self) -> str:
        inner = ", ".join(f"{k} = {v!r}" for k, v in self.fields.items())
        return f"{short_name(self.con)} {{ {inner} }}"


class ConValue:
    """A multi-variant constructor's value. Immutable (section 4.5)."""

    __slots__ = ("con", "args", "field_names")

    def __init__(self, con: str, args: tuple, field_names: list[str] | None = None):
        self.con = con
        self.args = args
        self.field_names = field_names

    def __repr__(self) -> str:
        # A value prints under the name the author wrote, not the qualified one
        # resolution gave the constructor (delta 43).
        name = short_name(self.con)
        if not self.args:
            return name
        if self.field_names:
            inner = ", ".join(
                f"{n} = {v!r}" for n, v in zip(self.field_names, self.args)
            )
            return f"{name} {{ {inner} }}"
        return f"{name}(" + ", ".join(repr(a) for a in self.args) + ")"


# `Bool` is a declared type now (`type Bool = False | True`, in the prelude),
# so a boolean is an ordinary nullary constructor and not a Python `bool`. The
# evaluator registers its own `ConValue`s for the two names like any other
# constructor; these singletons are what code *outside* the evaluator -- the
# comparison builtins, mostly -- answers with. Nothing compares them by
# identity: `con` is the tag, here as everywhere.
# The tags are the internal names `Data.Bool` gives its constructors, since a
# `ConValue`'s tag is what a pattern is matched against (`turkey/modules.py`).
TRUE = ConValue("Data.Bool#True", ())
FALSE = ConValue("Data.Bool#False", ())


def from_bool(b: bool) -> ConValue:
    return TRUE if b else FALSE


def truth(value) -> bool:
    """Read a turkey `Bool` back as a Python one, for `if` and friends."""
    return value.con == TRUE.con


class Closure:
    """A function value. `params` are *names*, not patterns (M13c).

    They used to be patterns, so calling meant matching, and a call that had
    typechecked could still fail at run time. The lowering turns a destructured
    parameter into a plain binder and a `match`, so what is left is a
    positional bind.
    """

    __slots__ = ("params", "body", "env", "name")

    def __init__(self, params, body, env, name: str = "<anonymous>"):
        self.params = params
        self.body = body
        self.env = env
        self.name = name

    def __repr__(self) -> str:
        return f"<fun {self.name}/{len(self.params)}>"


class Builtin:
    __slots__ = ("name", "arity", "fn")

    def __init__(self, name: str, arity: int, fn):
        self.name = name
        self.arity = arity
        self.fn = fn

    def __repr__(self) -> str:
        return f"<builtin {self.name}/{self.arity}>"


class ConstructorFn:
    """A constructor used as a function, e.g. `Some(1)` or `Point(1, 2)`."""

    __slots__ = ("con", "arity", "field_names", "mutable")

    def __init__(self, con: str, arity: int, field_names: list[str] | None, mutable: bool):
        self.con = con
        self.arity = arity
        self.field_names = field_names
        self.mutable = mutable

    def build(self, args: tuple) -> object:
        if self.mutable:
            return RecordObj(self.con, dict(zip(self.field_names, args)))
        return ConValue(self.con, args, self.field_names)

    def __repr__(self) -> str:
        return f"<constructor {self.con}/{self.arity}>"


def get_field(obj, name: str):
    """Read a field with the one dynamic case Core permits: an Array field."""
    if isinstance(obj, ArrayObj):
        return obj.length if name == "length" else obj.capacity
    return obj.fields[name]


def set_field(obj, name: str, value) -> None:
    """Write a mutable field, preserving Array's length/capacity invariants."""
    if isinstance(obj, ArrayObj):
        if name == "length":
            obj.set_length(value)
        else:
            obj.set_capacity(value)
        return
    obj.fields[name] = value
