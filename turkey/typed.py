"""What inference discovered about each expression, kept rather than dropped.

Generation gives every expression a type and then throws it away: the type is
a means of building the constraint, and once the constraint is solved nothing
asks again. That was fine while the evaluator walked the surface tree. It is
not fine for a typed Core, which needs the type of every node it lowers.

So the generator records what it already computed, into one table per program.
Two things make this cheap rather than a second pass:

* **`Type` is mutable union-find.** A `TVar` stashed during generation is not
  a snapshot; solving fills in `ref`, and `prune` reads the answer out later.
  Nothing has to be re-derived or copied back. `Generator.match_sites` has
  relied on exactly this since M7 -- this table is the same trick, applied to
  every expression instead of to `match` scrutinees.
* **Nothing is checked here.** The table is written during generation and read
  after solving. In between it holds variables that mean nothing yet, which is
  why `resolve` and not `record` is where a type has to be a type.

The table is deliberately *not* a field on `ast.Expr`. Every expression node
declares non-default fields, so a defaulted field on the base class is a
`TypeError` at class creation -- but the real reason is that M13's whole point
is to stop hanging side channels off the surface tree. Adding a fourth one on
the way to deleting the other three would be moving backwards. Nodes are keyed
by identity, which is already the house idiom (`ast.py`'s "identity equality:
the type checker annotates some of them in place").
"""

from __future__ import annotations

from . import ast
from .types import (
    Families, TApp, TFam, TFun, TSet, TTuple, Type, normalize, prune,
)


class _Reducer:
    """`types.Families`, backed by the instance table alone. See `observe`."""

    def __init__(self, classes) -> None:
        self.classes = classes

    def reduce(self, t: TFam) -> Type | None:
        return self.classes.reduce_fam(t)

    def defer(self, a: Type, b: Type, span=None, context: str = "") -> None:
        """Nothing is being solved here, so there is nothing to defer to."""
        raise AssertionError("a type table does not solve equations")


class TypeTable:
    """Every expression's type, keyed by node identity.

    The node is kept beside its type, and not only for debugging: `id()` is
    only unique among live objects, and holding a reference is what guarantees
    a node cannot be collected and its address handed to a different one.
    """

    def __init__(self) -> None:
        self._exprs: dict[int, tuple[ast.Expr, Type]] = {}
        self._decls: dict[int, tuple[object, Type]] = {}
        self._pats: dict[int, tuple[object, dict[str, Type]]] = {}
        self._fams: Families | None = None

    def observe(self, classes) -> None:
        """Take the family reducer to read recorded types back with.

        Not the solver's. `Solver.reduce` consults the *assumptions* in scope
        first, and those are per-body and gone by the time anything reads this
        table -- but so is the need for them: an assumption's rule was applied
        during solving, in place, on these very type objects. What can still be
        reduced afterwards is what the instance table decides, which is what
        `ClassTable` holds and keeps.
        """
        self._fams = _Reducer(classes)

    def record(self, e: ast.Expr, ty: Type) -> None:
        self._exprs[id(e)] = (e, ty)

    def record_decl(self, decl, ty: Type) -> None:
        """The type a `fun` declaration was bound at.

        Needed for the *local* case and only there. A top-level name's type is
        in the environment afterwards, but a local `fun`'s is not: it is
        defined into a child scope that is popped. A group that generalizes
        records its schemes instead (`evidence.Abstraction.schemes`); this is
        the other case, a group that does not.
        """
        self._decls[id(decl)] = (decl, ty)

    def of_decl(self, decl) -> Type | None:
        found = self._decls.get(id(decl))
        return None if found is None else self.resolve(found[1])

    def record_pattern(self, pat, binds: dict[str, Type]) -> None:
        """What one pattern binds, and at what types."""
        if binds:
            self._pats[id(pat)] = (pat, dict(binds))

    def of_pattern(self, pat) -> dict[str, Type]:
        found = self._pats.get(id(pat))
        if found is None:
            return {}
        return {n: self.resolve(t) for n, t in found[1].items()}

    def of(self, e: ast.Expr) -> Type:
        """The resolved type of one expression. Only meaningful after solving."""
        found = self._exprs.get(id(e))
        assert found is not None, f"no type recorded for {type(e).__name__}"
        return self.resolve(found[1])

    def resolve(self, ty: Type) -> Type:
        """Read a recorded type out, once solving has decided what it is.

        Two things have to be gone by now or the result is not a type in any
        useful sense, and both are the solver's job rather than this table's:

        * a `TSet` -- a numeric literal whose type was still a *decision*
          (delta 32). Defaulting settles it at generalization.
        * a `TFam` -- a family application that never reduced. Solving either
          reduces it or keeps it in a scheme's context, where it is a rigid
          argument rather than an unknown.

        A `TFam` under a signature's own context is legitimate and stays: it is
        a family over a rigid argument, and `Item c` where `c` is bound by the
        enclosing type abstraction is as much a type as `Int` is.

        The reduction is *deep*, and `types.normalize` is not. That is not an
        oversight there -- it says so: "Only the head is reduced. A family
        buried inside an argument is reduced when something compares it, which
        is the only moment its value can matter." Unification only ever
        compares heads, so head-only is exactly enough for it. A lowering is
        the case that module did not have: it reads the whole type and writes
        it into an IR, and `Option (Item (Array a))` buried under a `->` has to
        become `Option a` there or the Core term is annotated with a type that
        was never reduced and will not check.
        """
        return reduce_deep(ty, self._fams)

    def __len__(self) -> int:
        return len(self._exprs)

    def __contains__(self, e: object) -> bool:
        return id(e) in self._exprs

    def unresolved(self) -> list[tuple[ast.Expr, Type]]:
        """Every expression whose type is still a decision or still stuck.

        The M13a self-check: after solving, this must be empty. It is a
        diagnostic rather than an assertion because the two hazards it looks
        for -- `TSet` and `TFam` -- are exactly the ones whose absence a Core
        lowering will assume without saying so.
        """
        out = []
        for node, ty in self._exprs.values():
            resolved = self.resolve(ty)
            if _stuck(resolved):
                out.append((node, resolved))
        return out


def reduce_deep(ty: Type, fams: Families | None) -> Type:
    """`types.normalize`, applied at every level rather than at the head.

    Head-only is right for unification, which only ever compares heads, and
    `normalize` says so. It is not enough for anything that reads a type
    *whole* -- a lowering writing it into an IR, or a checker comparing two of
    them -- because `Option (Item (Array Int))` and `Option Int` are the same
    type and only one of them has been reduced.
    """
    ty = normalize(prune(ty), fams)
    if isinstance(ty, TApp):
        return TApp(reduce_deep(ty.fn, fams), reduce_deep(ty.arg, fams), ty.kind)
    if isinstance(ty, TFun):
        return TFun([reduce_deep(p, fams) for p in ty.params],
                    reduce_deep(ty.ret, fams))
    if isinstance(ty, TTuple):
        return TTuple([reduce_deep(e, fams) for e in ty.elems])
    if isinstance(ty, TFam):
        # The head stuck, so reduce the argument and ask once more: what
        # blocked `Item (Elem c)` may have been the `Elem c`, and one retry is
        # all it can take, since the argument is now reduced.
        arg = reduce_deep(ty.arg, fams)
        again = normalize(TFam(ty.name, arg, ty.kind), fams)
        return again if not isinstance(again, TFam) else TFam(ty.name, arg, ty.kind)
    return ty


def _stuck(ty: Type) -> bool:
    """Whether a resolved type still contains an undecided literal set."""
    ty = prune(ty)
    if isinstance(ty, TSet):
        return True
    return any(_stuck(arg) for arg in children(ty))


def children(ty: Type) -> list[Type]:
    """One level of a type's structure. Spelled out rather than reflected: a
    missing case here would silently under-report, which is the one thing a
    check for what must not survive cannot afford."""
    if isinstance(ty, TApp):
        return [ty.fn, ty.arg]
    if isinstance(ty, TFam):
        return [ty.arg]
    if isinstance(ty, TFun):
        return [*ty.params, ty.ret]
    if isinstance(ty, TTuple):
        return list(ty.elems)
    return []


__all__ = ["TypeTable", "children", "reduce_deep"]
