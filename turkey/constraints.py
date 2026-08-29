"""The constraint layer: what inference emits, and what settles it.

Inference used to call `unify` directly as it walked the AST, which made the
result depend on traversal order -- a decision taken at `r.f` could only see
whatever the walk happened to have resolved by then (SPEC-DELTAS.md entry 7).
The fix is the HM(X) shape: generation produces *constraints* and decides
nothing, and a solver settles them afterwards. Generation is order-independent
because it makes no choices at all.

The solver's policy is currently eager -- `emit` drains the queue immediately --
so it reproduces Algorithm J exactly and no program changes meaning. The queue
is what will let a constraint be *deferred* instead: put back unsolved, retried
as the solver learns more, and settled at the end of the binding group. That is
a change to `drain`, not a rewrite.

References: Odersky, Sulzmann & Wehr, "Type Inference with Constrained Types"
(TAPOS 1999); Pottier & Remy, "The Essence of ML Type Inference" (ATTAPL
ch. 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import Span, TypeError_
from .types import Pred, Type, join, unify


class Constraint:
    pass


@dataclass
class CEq(Constraint):
    """`left ~ right`. The equality fragment of the domain."""

    left: Type
    right: Type
    span: Span | None = None
    context: str = ""


@dataclass
class CPred(Constraint):
    """A predicate that must hold, settled against the domain's entailment."""

    pred: Pred
    span: Span | None = None
    context: str = ""


@dataclass
class Solver:
    """Settles the constraints generation emits.

    `queue` is the worklist and `deferred` holds what could not be settled yet.
    Nothing defers while equality is the only constraint form -- an equation is
    always either solvable or an error -- so `deferred` stays empty until a
    predicate that can get stuck arrives.
    """

    queue: list[Constraint] = field(default_factory=list)
    deferred: list[CPred] = field(default_factory=list)

    # -- generation-facing API ---------------------------------------------

    def emit(self, c: Constraint) -> None:
        self.queue.append(c)
        self.drain()

    def eq(self, a: Type, b: Type, span: Span | None = None, context: str = "") -> None:
        self.emit(CEq(a, b, span, context))

    def join(self, a: Type, b: Type, span: Span | None = None, context: str = "") -> Type:
        """Equate two types and return the one that survives.

        Bottom is absorbed by whatever it meets (design.md section 4.3), so a
        bare equation cannot tell the caller which type it ends up with. Every
        place two branches must agree goes through this instead. It stays a
        direct call rather than a constraint because bottom is produced
        *syntactically* -- by `return`, `break` and `continue` -- and is never
        discovered by solving, so there is nothing here for the solver to learn
        later and nothing that could depend on when it runs.
        """
        return join(a, b, span, context)

    # -- solving -----------------------------------------------------------

    def drain(self) -> None:
        while self.queue:
            self.solve(self.queue.pop(0))

    def solve(self, c: Constraint) -> None:
        if isinstance(c, CEq):
            unify(c.left, c.right, c.span, c.context)
            return
        if isinstance(c, CPred):
            raise TypeError_(f"no rule for predicate '{c.pred.name}'", c.span)
        raise AssertionError(f"unhandled constraint {type(c).__name__}")

    # -- generalization ----------------------------------------------------

    def retained(self, level: int) -> list[Pred]:
        """Deferred predicates general enough to be quantified at `level`.

        The test is the one `generalize` applies to type variables: a predicate
        tied to a variable created deeper than the binder travels in the scheme;
        one tied to an outer variable stays behind for an enclosing binding to
        settle. Predicates that travel are removed from `deferred`, since this
        binding has taken responsibility for them.
        """
        keep, retained = [], []
        for c in self.deferred:
            (retained if c.pred.level() > level else keep).append(c)
        self.deferred = keep
        return [c.pred for c in retained]
