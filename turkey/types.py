"""Semantic types and unification (design.md section 4).

Unification is Algorithm J's: type variables are mutable cells, so it updates
types in place and there is no substitution to thread around. Each variable
carries a `level` -- its rank, in Remy's sense -- recording the binder depth it
was created under, which makes generalization a cheap comparison rather than a
scan of the whole environment. Ranks are assigned by the solver as it descends
(`turkey/constraints.py`), never by the code that builds the types; nothing
here decides what a variable's rank should be.

The one departure from textbook Hindley-Milner is the bottom type. Section 4.3
says bottom is absorbed by whatever it meets, so unification treats it as a
no-op. That is not enough on its own -- see `join`.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import count

from .errors import Span, TypeError_


class Type:
    pass


# A source of fresh unification variables. Whoever hands one of these out is
# also responsible for recording the variables it makes, so that generalization
# can later find them.
Fresh = Callable[[], "TVar"]


class TVar(Type):
    """A unification variable. `ref` is None while unbound."""

    _ids = count()

    __slots__ = ("id", "level", "ref")

    def __init__(self, level: int):
        self.id = next(TVar._ids)
        self.level = level
        self.ref: Type | None = None

    def __repr__(self) -> str:
        return f"TVar({self.id}, lvl={self.level}, ref={self.ref!r})"


class TCon(Type):
    """A type constructor applied to arguments: `Int`, `Array a`, `Stack a`."""

    __slots__ = ("name", "args")

    def __init__(self, name: str, args: list[Type] | None = None):
        self.name = name
        self.args: list[Type] = args or []

    def __repr__(self) -> str:
        return f"TCon({self.name}, {self.args!r})"


class TFun(Type):
    """`fun(t1, ..., tn) -> t`. Uncurried, fixed arity (section 4.1)."""

    __slots__ = ("params", "ret")

    def __init__(self, params: list[Type], ret: Type):
        self.params = params
        self.ret = ret

    def __repr__(self) -> str:
        return f"TFun({self.params!r} -> {self.ret!r})"


class TTuple(Type):
    __slots__ = ("elems",)

    def __init__(self, elems: list[Type]):
        self.elems = elems

    def __repr__(self) -> str:
        return f"TTuple({self.elems!r})"


class TLabel(Type):
    """A field name lifted into the type language.

    Legal only in the first argument of `HasField`, and deliberately not kinded
    -- it exists so one predicate former can name any field, rather than the
    domain growing a former per label. It is rigid (two labels are equal only
    when they are the same string) and contains no variables, so every walk
    over types passes it through untouched.
    """

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"TLabel({self.name!r})"


class TSet(Type):
    """A closed set of type constructor names, lifted into the type language.

    Legal only in the second argument of `OneOf`, and the counterpart of
    `TLabel`: it lets one predicate former name any set of candidates rather
    than the domain growing a former per set. Rigid, variable-free, and passed
    through every walk over types untouched.
    """

    __slots__ = ("names",)

    def __init__(self, names):
        self.names: frozenset[str] = frozenset(names)

    def __repr__(self) -> str:
        return f"TSet({sorted(self.names)!r})"


class TBottom(Type):
    """The type of an expression that never yields a value (section 4.1)."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "TBottom"


BOTTOM = TBottom()

INT = TCon("Int")
FLOAT = TCon("Float")
STRING = TCon("String")
CHAR = TCon("Char")
BOOL = TCon("Bool")
UNIT = TCon("Unit")

PRIMITIVES = {"Int": INT, "Float": FLOAT, "String": STRING, "Char": CHAR,
              "Bool": BOOL, "Unit": UNIT}


# ------------------------------------------------------------- the numeric tower
#
# A numeric literal does not have a type; it has a *set* of types it could
# have, and `OneOf` (turkey/constraints.py) is what carries that set until
# something decides. The set is closed and built in -- there is no way to add a
# member from source -- so the two tables below are the whole of it.
#
# Both are written as tables so that a sized-integer tower drops in by editing
# them, with no change to the generator or the solver. A width of `None` means
# unbounded, which is what `Int` is (a Python int); a mantissa is how many bits
# of significand a float type has, which is what decides whether an integer
# literal is exactly representable in it.
#
# Order is meaning: defaulting takes the first member of a set in this order,
# and printing renders a set in it. So `Int` leads the integral types and, once
# the tower lands and `Float` is renamed, `Double` leads the decimal ones --
# which is exactly the "integral defaults to Int, decimal defaults to Double"
# rule, obtained from one mechanism rather than two.
INTEGRAL_WIDTHS: dict[str, int | None] = {"Int": None}
DECIMAL_MANTISSAS: dict[str, int] = {"Float": 53}  # f64


def numeric_order() -> list[str]:
    """The tower, most-preferred first. Read at each call, not cached, so that
    a test can extend the tables and see the whole pipeline follow."""
    return list(INTEGRAL_WIDTHS) + list(DECIMAL_MANTISSAS)


def int_literal_set(value: int) -> frozenset[str]:
    """The types an integer literal could have: everything that holds `value`.

    **Including the float types.** An integer literal is not an integer-typed
    expression; it is a written numeral, and `1` denotes a perfectly good
    `Float`. Only the reverse is unsafe, which is why `float_literal_set` is
    the float types alone. This is the `Num`/`Fractional` split, and it is what
    lets `1 +. 2.0` mean what it reads as.

    One rule decides membership for the whole tower: can the type hold this
    value exactly? For an integral type that is its width; for a float type it
    is its mantissa, so a literal past 2^53 is an `Int` and not a `Float`
    rather than silently rounding.
    """
    magnitude = abs(value)
    return frozenset(
        [name for name, width in INTEGRAL_WIDTHS.items()
         if width is None or -(1 << (width - 1)) <= value < (1 << (width - 1))]
        + [name for name, mantissa in DECIMAL_MANTISSAS.items()
           if magnitude < (1 << mantissa)]
    )


def float_literal_set() -> frozenset[str]:
    """The types a decimal literal could have: the float types, and only those.

    Not narrowed by the value the way `int_literal_set` is. `0.1` is
    inexact in every binary float, so representability would reject every
    member and say nothing useful; what a decimal literal picks out is the
    *kind* of type, and precision is the programmer's business from there.
    """
    return frozenset(DECIMAL_MANTISSAS)


def numeric_type(name: str) -> TCon:
    """The type a tower member names.

    Every member is a nullary constructor, so its type is a `TCon` of that
    name. `PRIMITIVES` is consulted first only to reuse the shared `INT` and
    `FLOAT` instances; the tables above are the source of truth for which names
    are legal, and a name not in them can never reach here.
    """
    return PRIMITIVES.get(name) or TCon(name)


def sort_numeric(names) -> list[str]:
    order = numeric_order()
    return sorted(names, key=lambda n: (order.index(n) if n in order else len(order), n))


class Pred:
    """An atomic predicate: `HasField l r a`, `OneOf t {...}`, `Eq a`.

    Predicates live here rather than beside the solver because a scheme can
    carry them -- they are part of the type language, not of the machinery that
    discharges them.
    """

    __slots__ = ("name", "args")

    def __init__(self, name: str, args: list[Type]):
        self.name = name
        self.args = args

    def key(self) -> tuple:
        """A hashable identity, for dropping a context's duplicate predicates.

        `r.n = r.n + 1` demands the same field twice, and there is no reason
        for the scheme to say so twice.
        """
        return (self.name,) + tuple(type_key(a) for a in self.args)

    def level(self) -> int:
        """The binder depth this predicate is tied to.

        A predicate is no more general than its most-constrained variable, so
        its level is the minimum over the variables it mentions, and
        generalization compares against it exactly as it does for a type.
        Keeping the two tests identical is what stops a predicate from
        outliving, or being stranded by, the type it constrains. A ground
        predicate mentions no variables and can be settled anywhere.
        """
        levels = [v.level for v in vars_of(*self.args)]
        return min(levels) if levels else GROUND

    def __repr__(self) -> str:
        return f"Pred({self.name}, {self.args!r})"


GROUND = 1 << 30


class Scheme:
    """`forall q1 ... qn . preds => body`.

    An empty `quantified` means monomorphic; an empty `preds` means unqualified.
    """

    __slots__ = ("quantified", "body", "preds")

    def __init__(self, quantified: list[TVar], body: Type, preds: list[Pred] | None = None):
        self.quantified = quantified
        self.body = body
        self.preds: list[Pred] = preds or []

    def __repr__(self) -> str:
        return f"Scheme({len(self.quantified)}, {self.preds!r}, {self.body!r})"


def mono(t: Type) -> Scheme:
    return Scheme([], t)


# ------------------------------------------------------------------ operations


def prune(t: Type) -> Type:
    """Follow bound variables to the type they stand for, path-compressing."""
    if isinstance(t, TVar) and t.ref is not None:
        t.ref = prune(t.ref)
        return t.ref
    return t


def occurs_and_adjust(var: TVar, t: Type) -> bool:
    """Occurs check, lowering levels along the way.

    A variable reachable from `var`'s new value must not outlive `var`, so its
    level is capped at `var`'s. Doing both walks at once keeps them in step.
    """
    t = prune(t)
    if isinstance(t, TVar):
        if t is var:
            return True
        t.level = min(t.level, var.level)
        return False
    if isinstance(t, TCon):
        return any(occurs_and_adjust(var, a) for a in t.args)
    if isinstance(t, TFun):
        return any(occurs_and_adjust(var, p) for p in t.params) or occurs_and_adjust(var, t.ret)
    if isinstance(t, TTuple):
        return any(occurs_and_adjust(var, e) for e in t.elems)
    return False


def unify(a: Type, b: Type, span: Span | None = None, context: str = "") -> None:
    a, b = prune(a), prune(b)

    if a is b:
        return

    # Section 4.3: bottom is absorbed by anything it meets. Note this is a
    # no-op, not a binding -- `join` is what recovers the surviving type.
    if isinstance(a, TBottom) or isinstance(b, TBottom):
        return

    if isinstance(a, TVar):
        if occurs_and_adjust(a, b):
            raise TypeError_(f"cannot construct the infinite type {show(a)} = {show(b)}", span)
        a.ref = b
        return
    if isinstance(b, TVar):
        return unify(b, a, span, context)

    if isinstance(a, TCon) and isinstance(b, TCon):
        if a.name != b.name or len(a.args) != len(b.args):
            raise _mismatch(a, b, span, context)
        for x, y in zip(a.args, b.args):
            unify(x, y, span, context)
        return

    if isinstance(a, TFun) and isinstance(b, TFun):
        if len(a.params) != len(b.params):
            want, got = len(a.params), len(b.params)
            raise TypeError_(
                f"this function takes {want} argument{'' if want == 1 else 's'} "
                f"but {got} {'was' if got == 1 else 'were'} supplied",
                span,
            )
        for x, y in zip(a.params, b.params):
            unify(x, y, span, context)
        unify(a.ret, b.ret, span, context)
        return

    if isinstance(a, TLabel) and isinstance(b, TLabel):
        if a.name != b.name:
            raise _mismatch(a, b, span, context)
        return

    if isinstance(a, TTuple) and isinstance(b, TTuple):
        if len(a.elems) != len(b.elems):
            raise _mismatch(a, b, span, context)
        for x, y in zip(a.elems, b.elems):
            unify(x, y, span, context)
        return

    raise _mismatch(a, b, span, context)


def _mismatch(a: Type, b: Type, span: Span | None, context: str) -> TypeError_:
    where = f" in {context}" if context else ""
    return TypeError_(f"expected {show(a)}, found {show(b)}{where}", span)


def join(a: Type, b: Type, span: Span | None = None, context: str = "") -> Type:
    """Unify two types and return the one that survives.

    Plain `unify` cannot answer this. `if c { return 1 } else { 2 }` has arms of
    type bottom and Int; unifying them is a no-op and the caller still has to
    know the result is Int. Every place where two branches must agree -- `if`
    arms, `match` arms, a loop's `break` values -- uses this instead.
    """
    a, b = prune(a), prune(b)
    if isinstance(a, TBottom):
        return b
    if isinstance(b, TBottom):
        return a
    unify(a, b, span, context)
    return a


def generalize(t: Type, level: int, preds: list[Pred] | None = None) -> Scheme:
    """Quantify every variable created deeper than `level` (section 4.4).

    `preds` are the predicates the solver decided this binding retains. They are
    walked too, so a variable a predicate constrains is quantified along with
    the type's own -- a retained predicate must not mention a variable the
    scheme left free.
    """
    quantified: list[TVar] = []
    seen: set[int] = set()

    def walk(ty: Type) -> None:
        ty = prune(ty)
        if isinstance(ty, TVar):
            if ty.level > level and ty.id not in seen:
                seen.add(ty.id)
                quantified.append(ty)
        elif isinstance(ty, TCon):
            for a in ty.args:
                walk(a)
        elif isinstance(ty, TFun):
            for p in ty.params:
                walk(p)
            walk(ty.ret)
        elif isinstance(ty, TTuple):
            for e in ty.elems:
                walk(e)

    walk(t)
    for pred in preds or []:
        for arg in pred.args:
            walk(arg)
    return Scheme(quantified, t, list(preds or []))


def instantiate(scheme: Scheme, fresh: Fresh) -> Type:
    """Replace the scheme's quantified variables with fresh ones."""
    return instantiate_qual(scheme, fresh)[1]


def instantiate_qual(scheme: Scheme, fresh: Fresh) -> tuple[list[Pred], Type]:
    """Instantiate a scheme, returning its context alongside its type.

    `fresh` is supplied by the caller rather than a rank being passed in,
    because a new variable has to be registered wherever its owner tracks them
    -- the solver's pool for the current rank, or the enclosing existential
    while a constraint is still being generated. Handing out `TVar(rank)` here
    would create a variable nothing is keeping track of.

    The context has to be renamed by the same substitution as the body, or its
    predicates would constrain the scheme's original variables rather than this
    use site's fresh ones.
    """
    if not scheme.quantified:
        return list(scheme.preds), scheme.body
    mapping = {v.id: fresh() for v in scheme.quantified}

    def walk(ty: Type) -> Type:
        ty = prune(ty)
        if isinstance(ty, TVar):
            return mapping.get(ty.id, ty)
        if isinstance(ty, TCon):
            return TCon(ty.name, [walk(a) for a in ty.args]) if ty.args else ty
        if isinstance(ty, TFun):
            return TFun([walk(p) for p in ty.params], walk(ty.ret))
        if isinstance(ty, TTuple):
            return TTuple([walk(e) for e in ty.elems])
        return ty

    preds = [Pred(p.name, [walk(a) for a in p.args]) for p in scheme.preds]
    return preds, walk(scheme.body)


def type_key(t: Type) -> tuple:
    """A structural identity for a type, up to the current substitution."""
    t = prune(t)
    if isinstance(t, TVar):
        return ("var", t.id)
    if isinstance(t, TCon):
        return ("con", t.name, tuple(type_key(a) for a in t.args))
    if isinstance(t, TFun):
        return ("fun", tuple(type_key(p) for p in t.params), type_key(t.ret))
    if isinstance(t, TTuple):
        return ("tuple", tuple(type_key(e) for e in t.elems))
    if isinstance(t, TLabel):
        return ("label", t.name)
    if isinstance(t, TSet):
        return ("set", tuple(sorted(t.names)))
    return ("bottom",)


def vars_of(*types: Type) -> list[TVar]:
    """The unbound variables reachable from `types`, first occurrence first."""
    seen: dict[int, TVar] = {}

    def walk(ty: Type) -> None:
        ty = prune(ty)
        if isinstance(ty, TVar):
            seen.setdefault(ty.id, ty)
        elif isinstance(ty, TCon):
            for a in ty.args:
                walk(a)
        elif isinstance(ty, TFun):
            for p in ty.params:
                walk(p)
            walk(ty.ret)
        elif isinstance(ty, TTuple):
            for e in ty.elems:
                walk(e)

    for t in types:
        walk(t)
    return list(seen.values())


def free_vars(t: Type, acc: set[int] | None = None) -> set[int]:
    acc = acc if acc is not None else set()
    t = prune(t)
    if isinstance(t, TVar):
        acc.add(t.id)
    elif isinstance(t, TCon):
        for a in t.args:
            free_vars(a, acc)
    elif isinstance(t, TFun):
        for p in t.params:
            free_vars(p, acc)
        free_vars(t.ret, acc)
    elif isinstance(t, TTuple):
        for e in t.elems:
            free_vars(e, acc)
    return acc


# -------------------------------------------------------------------- printing

_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _var_name(index: int) -> str:
    return _LETTERS[index % 26] + ("" if index < 26 else str(index // 26))


def show(t: Type, names: dict[int, str] | None = None, free_prefix: str = "",
         prec: int = 0) -> str:
    """Render a type in surface syntax, naming variables a, b, c, ... in order.

    `free_prefix` is prepended to variables not already present in `names`.
    `show_scheme` uses it to mark the ones a scheme did not quantify, so a
    monomorphic `Array _a` is not mistaken for a polymorphic `Array a`.
    """
    names = names if names is not None else {}
    unnamed = [0]

    def go(ty: Type, prec: int) -> str:
        ty = prune(ty)
        if isinstance(ty, TVar):
            if ty.id not in names:
                names[ty.id] = free_prefix + _var_name(unnamed[0])
                unnamed[0] += 1
            return names[ty.id]
        if isinstance(ty, TBottom):
            return "!"
        if isinstance(ty, TLabel):
            return f'"{ty.name}"'
        if isinstance(ty, TSet):
            return "{" + ", ".join(sort_numeric(ty.names)) + "}"
        if isinstance(ty, TTuple):
            return "(" + ", ".join(go(e, 0) for e in ty.elems) + ")"
        if isinstance(ty, TFun):
            params = ", ".join(go(p, 0) for p in ty.params)
            out = f"fun({params}) -> {go(ty.ret, 0)}"
            return f"({out})" if prec > 0 else out
        if isinstance(ty, TCon):
            if not ty.args:
                return ty.name
            args = " ".join(go(a, 2) for a in ty.args)
            out = f"{ty.name} {args}"
            return f"({out})" if prec > 1 else out
        return repr(ty)

    return go(t, prec)


def show_scheme(scheme: Scheme) -> str:
    """Render a scheme, marking free (monomorphic) variables with a leading `_`.

    A `let` bound to an expansive expression is not generalized (section 4.4),
    so its variables stay free -- `Array _a` rather than `Array a`.

    A qualified scheme prints its context first, in the bracket syntax a `fun`
    declaration writes: `[Ord a] fun(Array a) -> a`. The names have to be
    assigned before the context is rendered so that the `a` in the context and
    the `a` in the body come out as the same letter.
    """
    names = {var.id: _var_name(i) for i, var in enumerate(scheme.quantified)}
    body = show(scheme.body, names, free_prefix="_")
    if not scheme.preds:
        return body
    context = ", ".join(show_pred(p, names) for p in scheme.preds)
    return f"[{context}] {body}"


def show_pred(pred: Pred, names: dict[int, str] | None = None) -> str:
    # A predicate is itself an application, so its arguments are rendered at
    # argument precedence: `HasField "d" a (Array Int)`, not `... a Array Int`.
    args = " ".join(show(a, names, free_prefix="_", prec=2) for a in pred.args)
    return f"{pred.name} {args}" if args else pred.name
