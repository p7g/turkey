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

Type application is *curried* even though the function type is not: `Array Int`
is `TApp(TCon("Array"), INT)`, so a variable can stand in head position and
`Functor f` has something to quantify over. `TFun` stays a separate uncurried
node because it is the language's fixed-arity function type (section 4.1), not
a constructor that happens to take two arguments. Kinds keep the two apart and
make application decomposition sound -- see the kind section below.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import count
from typing import Protocol

from .errors import Span, TypeError_


# ----------------------------------------------------------------------- kinds
#
# A kind is the type of a type. `Int :: *`, `Array :: * -> *`, and once classes
# arrive `Functor f` demands `f :: * -> *`. Kinds exist here for exactly one
# reason: with application curried, `spine`-based arity is no longer syntactic,
# and something has to say that `Array` alone is not a type while `Array Int`
# is.
#
# Kind variables are inferred the same way type variables are -- mutable cells,
# union-find, occurs check -- because a type declaration's parameter kinds are
# not written down and have to be discovered from the bodies. Kinds are not
# polymorphic: whatever is still unresolved once the declarations are read is
# defaulted to `*` (`decls.settle_kinds`), which is Haskell 98's rule and keeps
# a kind a first-order term.


class Kind:
    pass


class KStar(Kind):
    """`*`, the kind of types that classify values."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "*"


STAR = KStar()


class KFun(Kind):
    """`k1 -> k2`, the kind of a constructor awaiting an argument."""

    __slots__ = ("arg", "res")

    def __init__(self, arg: Kind, res: Kind):
        self.arg = arg
        self.res = res

    def __repr__(self) -> str:
        return f"KFun({self.arg!r}, {self.res!r})"


class KVar(Kind):
    """A kind not yet decided. `ref` is None while unbound."""

    _ids = count()

    __slots__ = ("id", "ref")

    def __init__(self) -> None:
        self.id = next(KVar._ids)
        self.ref: Kind | None = None

    def __repr__(self) -> str:
        return f"KVar({self.id}, ref={self.ref!r})"


def kprune(k: Kind) -> Kind:
    if isinstance(k, KVar) and k.ref is not None:
        k.ref = kprune(k.ref)
        return k.ref
    return k


def _koccurs(var: KVar, k: Kind) -> bool:
    k = kprune(k)
    if isinstance(k, KVar):
        return k is var
    if isinstance(k, KFun):
        return _koccurs(var, k.arg) or _koccurs(var, k.res)
    return False


def unify_kinds(a: Kind, b: Kind) -> bool:
    """Match two kinds, binding variables. False on mismatch -- the caller has
    the span and the words, so nothing is raised from here."""
    a, b = kprune(a), kprune(b)
    if a is b:
        return True
    if isinstance(a, KVar):
        if _koccurs(a, b):
            return False
        a.ref = b
        return True
    if isinstance(b, KVar):
        return unify_kinds(b, a)
    if isinstance(a, KFun) and isinstance(b, KFun):
        return unify_kinds(a.arg, b.arg) and unify_kinds(a.res, b.res)
    return isinstance(a, KStar) and isinstance(b, KStar)


def default_kind(k: Kind) -> Kind:
    """Bind every variable still unresolved in `k` to `*`, and return it."""
    k = kprune(k)
    if isinstance(k, KVar):
        k.ref = STAR
        return STAR
    if isinstance(k, KFun):
        default_kind(k.arg)
        default_kind(k.res)
    return k


def kind_arrow(n: int, result: Kind = STAR) -> Kind:
    """`* -> ... -> result`, with `n` arrows over fresh argument kinds."""
    k = result
    for _ in range(n):
        k = KFun(KVar(), k)
    return k


def show_kind(k: Kind) -> str:
    k = kprune(k)
    if isinstance(k, KFun):
        left = show_kind(k.arg)
        if isinstance(kprune(k.arg), KFun):
            left = f"({left})"
        return f"{left} -> {show_kind(k.res)}"
    return "*"  # an undecided kind prints as the `*` it will default to


class Type:
    pass


# A source of fresh unification variables. Whoever hands one of these out is
# also responsible for recording the variables it makes, so that generalization
# can later find them.
Fresh = Callable[[], "TVar"]


class TVar(Type):
    """A unification variable. `ref` is None while unbound.

    Its kind is a fresh variable by default rather than `*`, because a variable
    written in an annotation may turn out to stand for a constructor -- the `f`
    of `Wrap f a` is discovered to be `* -> *` only by seeing it applied.
    """

    _ids = count()

    __slots__ = ("id", "level", "ref", "kind")

    def __init__(self, level: int, kind: Kind | None = None):
        self.id = next(TVar._ids)
        self.level = level
        self.ref: Type | None = None
        self.kind: Kind = kind if kind is not None else KVar()

    def __repr__(self) -> str:
        return f"TVar({self.id}, lvl={self.level}, ref={self.ref!r})"


class TCon(Type):
    """A type constructor, *unapplied*: `Int`, `Array`, `Stack`.

    Arguments are no longer carried here -- `Array Int` is `TApp(TCon("Array"),
    INT)`. A constructor is therefore rigid and variable-free, which is what
    lets `instantiate` and `substitute` return it untouched, and what makes
    decomposing an application sound (see `unify`).

    The kind is the constructor's declared one and is shared by every
    occurrence: `decls.DeclTable` is the single place user constructors are
    built, so two `TCon("Stack")`s cannot disagree.
    """

    __slots__ = ("name", "kind")

    def __init__(self, name: str, kind: Kind | None = None):
        self.name = name
        self.kind: Kind = STAR if kind is None else kind

    def __repr__(self) -> str:
        return f"TCon({self.name})"


class TApp(Type):
    """`f a` -- one type applied to one argument.

    Curried, so `Array Int` is a one-deep spine and `Either l r` a two-deep
    one, and a *variable* can sit at the head of either. That is the whole
    point: `Functor f` needs an `f` to abstract over, and a saturated
    `TCon(name, args)` has no sub-term for it to be.

    `kind` is the kind of the application itself, computed once by `apply`
    (which is also where the argument's kind is checked), so `kind_of` stays a
    pure lookup rather than a re-derivation.
    """

    __slots__ = ("fn", "arg", "kind")

    def __init__(self, fn: Type, arg: Type, kind: Kind):
        self.fn = fn
        self.arg = arg
        self.kind = kind

    def __repr__(self) -> str:
        return f"TApp({self.fn!r}, {self.arg!r})"


class TFam(Type):
    """`F t` -- an associated type family applied to its class's parameter.

    A family is a *function* on types: `Elem (Array a)` is `a` once the
    instance is known, and nothing at all until then. That is why it cannot be
    a `TCon` at the head of a `TApp`. Decomposing `f a ~ g b` pointwise is
    sound only because every head is rigid, and a family head is precisely the
    one that is not -- the same reason type aliases must be saturated before
    expansion, and the same reason there are no type-level lambdas.

    It is saturated by construction: a family is declared in a class and takes
    that class's parameter, so its arity is one and there is no partial
    application to represent. `kind` is the family's declared result kind,
    shared by every occurrence (`decls.FamilyInfo`), so a family may return a
    constructor as readily as a type.

    Families are **not injective**: `Elem i ~ Int` says nothing about `i`. So
    two family applications unify only when they are syntactically the same
    application -- that is reflexivity, not decomposition -- and everything
    else waits (see `unify`).
    """

    __slots__ = ("name", "arg", "kind")

    def __init__(self, name: str, arg: Type, kind: Kind):
        self.name = name
        self.arg = arg
        self.kind = kind

    def __repr__(self) -> str:
        return f"TFam({self.name}, {self.arg!r})"


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

ARRAY = TCon("Array", KFun(STAR, STAR))


# ------------------------------------------------------- application and spines


def kind_of(t: Type) -> Kind:
    """The kind of a type. Everything but a constructor, a variable and an
    application classifies values, so it is `*`."""
    t = prune(t)
    if isinstance(t, (TVar, TCon, TApp, TFam)):
        return t.kind
    return STAR


def apply(head: Type, args: list[Type], span: Span | None = None) -> Type:
    """`head a1 ... an`, checking each application against `head`'s kind.

    This is the only place a `TApp` is built, so it is also the only place a
    kind can be got wrong. An over-applied constructor is caught here rather
    than by a separate arity check: `Array Int Bool` fails because `*` is not
    an arrow, which is the same rule that rejects `Int Bool`.
    """
    t = head
    for arg in args:
        k = kprune(kind_of(t))
        result = KVar()
        if not unify_kinds(k, KFun(kind_of(arg), result)):
            # A kind still undecided can only have failed the occurs check,
            # since a variable unifies with anything else: `f f` would need
            # `f`'s kind to contain itself.
            if isinstance(kprune(k), KVar):
                raise TypeError_(
                    f"'{show(t)}' cannot be applied to '{show(arg)}': that "
                    f"would need a kind that contains itself",
                    span,
                )
            raise TypeError_(
                f"'{show(t)}' has kind {show_kind(k)}, so it cannot be applied "
                f"to '{show(arg)}'",
                span,
            )
        t = TApp(t, arg, result)
    return t


def spine(t: Type) -> tuple[Type, list[Type]]:
    """Decompose `f a1 ... an` into its head and its arguments.

    Most of the checker wants to ask "is this an `Array`?", which is a question
    about the head; asking it in spine terms keeps those places written the way
    they were before application was curried.
    """
    args: list[Type] = []
    t = prune(t)
    while isinstance(t, TApp):
        args.append(t.arg)
        t = prune(t.fn)
    args.reverse()
    return t, args


def head_con(t: Type) -> TCon | None:
    """The constructor at the head of `t`, if it is one."""
    head, _ = spine(t)
    return head if isinstance(head, TCon) else None


def array_of(element: Type) -> Type:
    return TApp(ARRAY, element, STAR)


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


class Families(Protocol):
    """What unification needs from the solver once families exist.

    Two operations, and deliberately no more: reduce a family application if
    the instance table decides it, and take an equation that cannot yet be
    decided. Stating it as a protocol rather than importing the solver keeps
    the dependency pointing the way it already does -- the solver knows about
    types, types know nothing about the solver.
    """

    def reduce(self, t: "TFam") -> "Type | None":
        """`t`'s definition, or None if no instance decides it yet."""

    def defer(self, a: "Type", b: "Type", span: Span | None, context: str) -> None:
        """Put `a ~ b` back on the queue: neither solvable nor false, yet."""


def normalize(t: Type, fams: Families | None) -> Type:
    """Prune, then reduce family applications at the head until one sticks.

    `fams` is the solver, which is the only thing that knows the instance
    table; passing it in rather than reaching for a global is what keeps this
    module ignorant of classes. `None` means "no reducer available" -- the
    post-solving passes, where every family that was going to reduce already
    has.

    Only the head is reduced. A family buried inside an argument is reduced
    when something compares it, which is the only moment its value can matter.
    """
    t = prune(t)
    while isinstance(t, TFam) and fams is not None:
        reduced = fams.reduce(t)
        if reduced is None:
            return t
        t = prune(reduced)
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
    if isinstance(t, TApp):
        return occurs_and_adjust(var, t.fn) or occurs_and_adjust(var, t.arg)
    if isinstance(t, TFam):
        # Over-strict, strictly speaking: a family is not injective, so `a ~
        # Elem a` is not obviously a cycle. Rejecting it is the standard
        # choice and the safe one -- admitting it would need the family's
        # definition to prove the recursion bottoms out.
        return occurs_and_adjust(var, t.arg)
    if isinstance(t, TFun):
        return any(occurs_and_adjust(var, p) for p in t.params) or occurs_and_adjust(var, t.ret)
    if isinstance(t, TTuple):
        return any(occurs_and_adjust(var, e) for e in t.elems)
    return False


def unify(a: Type, b: Type, span: Span | None = None, context: str = "",
          fams: Families | None = None) -> None:
    """Equate two types, or -- since M7 -- report that it cannot be decided yet.

    A family application whose argument is still open is neither solvable nor
    an error: `Elem a ~ Int` becomes true or false depending on what `a` turns
    out to be. That is the third outcome, and `fams.defer` is where it goes --
    back on the solver's queue, to be retried when something has been learnt.
    Without a `fams` to defer to there is no queue, so it is a mismatch.
    """
    a, b = normalize(a, fams), normalize(b, fams)

    if a is b:
        return

    # Section 4.3: bottom is absorbed by anything it meets. Note this is a
    # no-op, not a binding -- `join` is what recovers the surviving type.
    if isinstance(a, TBottom) or isinstance(b, TBottom):
        return

    if isinstance(a, TVar):
        if occurs_and_adjust(a, b):
            raise TypeError_(f"cannot construct the infinite type {show(a)} = {show(b)}", span)
        if not unify_kinds(a.kind, kind_of(b)):
            raise _kind_mismatch(a, b, span)
        a.ref = b
        return
    if isinstance(b, TVar):
        return unify(b, a, span, context, fams)

    if isinstance(a, TCon) and isinstance(b, TCon):
        if a.name != b.name:
            raise _mismatch(a, b, span, context)
        return

    # Decomposing an application is sound only because there are no type-level
    # lambdas: every head is rigid, or a variable that will be bound to a rigid
    # head, so `f a ~ g b` cannot be satisfied any way but pointwise. Aliases
    # are expanded before they reach here (`decls.to_type`), which is what
    # keeps that true.
    if isinstance(a, TApp) and isinstance(b, TApp):
        unify(a.fn, b.fn, span, context, fams)
        unify(a.arg, b.arg, span, context, fams)
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
            unify(x, y, span, context, fams)
        unify(a.ret, b.ret, span, context, fams)
        return

    if isinstance(a, TLabel) and isinstance(b, TLabel):
        if a.name != b.name:
            raise _mismatch(a, b, span, context)
        return

    if isinstance(a, TTuple) and isinstance(b, TTuple):
        if len(a.elems) != len(b.elems):
            raise _mismatch(a, b, span, context)
        for x, y in zip(a.elems, b.elems):
            unify(x, y, span, context, fams)
        return

    # A family that did not reduce. Two of them are equal when they are the
    # *same* application -- `Elem i ~ Elem i` is reflexivity, and is as far as
    # it goes, because a family is not injective and `Elem i ~ Elem j` must not
    # conclude `i ~ j`. Anything else waits for the argument to be decided.
    if isinstance(a, TFam) or isinstance(b, TFam):
        if (isinstance(a, TFam) and isinstance(b, TFam)
                and a.name == b.name and type_key(a.arg) == type_key(b.arg)):
            return
        if fams is None:
            raise _mismatch(a, b, span, context)
        fams.defer(a, b, span, context)
        return

    raise _mismatch(a, b, span, context)


def _kind_mismatch(a: Type, b: Type, span: Span | None) -> TypeError_:
    return TypeError_(
        f"'{show(b)}' has kind {show_kind(kind_of(b))}, but a type of kind "
        f"{show_kind(kind_of(a))} was expected here",
        span,
    )


def _mismatch(a: Type, b: Type, span: Span | None, context: str) -> TypeError_:
    where = f" in {context}" if context else ""
    return TypeError_(f"expected {show(a)}, found {show(b)}{where}", span)


def join(a: Type, b: Type, span: Span | None = None, context: str = "",
         fams: Families | None = None) -> Type:
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
    unify(a, b, span, context, fams)
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
        elif isinstance(ty, TApp):
            walk(ty.fn)
            walk(ty.arg)
        elif isinstance(ty, TFam):
            walk(ty.arg)
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
    # A fresh variable stands for a quantified one, so it inherits its kind:
    # instantiating `Wrap f a` must not turn the `f` into something of kind `*`.
    mapping = {}
    for v in scheme.quantified:
        replacement = fresh()
        unify_kinds(replacement.kind, v.kind)
        mapping[v.id] = replacement

    def walk(ty: Type) -> Type:
        ty = prune(ty)
        if isinstance(ty, TVar):
            return mapping.get(ty.id, ty)
        if isinstance(ty, TApp):
            return TApp(walk(ty.fn), walk(ty.arg), ty.kind)
        if isinstance(ty, TFam):
            return TFam(ty.name, walk(ty.arg), ty.kind)
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
        return ("con", t.name)
    if isinstance(t, TApp):
        return ("app", type_key(t.fn), type_key(t.arg))
    if isinstance(t, TFam):
        return ("fam", t.name, type_key(t.arg))
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
        elif isinstance(ty, TApp):
            walk(ty.fn)
            walk(ty.arg)
        elif isinstance(ty, TFam):
            walk(ty.arg)
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
    elif isinstance(t, TApp):
        free_vars(t.fn, acc)
        free_vars(t.arg, acc)
    elif isinstance(t, TFam):
        free_vars(t.arg, acc)
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
            return ty.name
        if isinstance(ty, TFam):
            out = f"{ty.name} {go(ty.arg, 2)}"
            return f"({out})" if prec > 1 else out
        if isinstance(ty, TApp):
            head, args = spine(ty)
            rendered = " ".join(go(a, 2) for a in args)
            out = f"{go(head, 2)} {rendered}"
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


def show_pred(pred: Pred, names: dict[int, str] | None = None,
              free_prefix: str = "_") -> str:
    # A predicate is itself an application, so its arguments are rendered at
    # argument precedence: `HasField "d" a (Array Int)`, not `... a Array Int`.
    # The `_` marks a variable no scheme quantified, which is the point when a
    # context is being printed and noise when a lone predicate is being blamed.
    args = " ".join(show(a, names, free_prefix=free_prefix, prec=2) for a in pred.args)
    return f"{pred.name} {args}" if args else pred.name
