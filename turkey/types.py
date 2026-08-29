"""Semantic types and unification (design.md section 4).

Inference uses Algorithm J: type variables are mutable cells, so unification
updates types in place and there is no substitution to thread around. Each
variable carries a `level` (Remy's trick) recording the binder depth at which it
was created, which is what makes generalization a cheap comparison rather than a
scan of the whole environment.

The one departure from textbook Hindley-Milner is the bottom type. Section 4.3
says bottom is absorbed by whatever it meets, so unification treats it as a
no-op. That is not enough on its own -- see `join`.
"""

from __future__ import annotations

from itertools import count

from .errors import Span, TypeError_


class Type:
    pass


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


def instantiate(scheme: Scheme, level: int) -> Type:
    """Replace the scheme's quantified variables with fresh ones."""
    return instantiate_qual(scheme, level)[1]


def instantiate_qual(scheme: Scheme, level: int) -> tuple[list[Pred], Type]:
    """Instantiate a scheme, returning its context alongside its type.

    The context has to be renamed by the same substitution as the body, or its
    predicates would constrain the scheme's original variables rather than this
    use site's fresh ones.
    """
    if not scheme.quantified:
        return list(scheme.preds), scheme.body
    mapping = {v.id: TVar(level) for v in scheme.quantified}

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


def show(t: Type, names: dict[int, str] | None = None, free_prefix: str = "") -> str:
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

    return go(t, 0)


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
    args = " ".join(show(a, names, free_prefix="_") for a in pred.args)
    return f"{pred.name} {args}" if args else pred.name
