"""The constraint layer: what inference emits, and what settles it.

Inference used to call `unify` directly as it walked the AST, which made the
result depend on traversal order -- a decision taken at `r.f` could only see
whatever the walk happened to have resolved by then (SPEC-DELTAS.md entry 7).
The fix is the HM(X) shape: generation produces *constraints* and decides
nothing, and a solver settles them afterwards. Generation is order-independent
because it makes no choices at all.

Equality is settled eagerly -- `emit` drains the queue immediately, so the
equality fragment still reproduces Algorithm J exactly. A *predicate* may
instead be **deferred**: put aside unsolved, retried by `settle` as unification
teaches the solver more, and finally either discharged, carried into a scheme
by `retained`, or reported. That deferral is what makes field access
order-independent, since `a.length` no longer has to guess at `a`.

References: Odersky, Sulzmann & Wehr, "Type Inference with Constrained Types"
(TAPOS 1999); Pottier & Remy, "The Essence of ML Type Inference" (ATTAPL
ch. 10). `HasField` is the `r \\ l` predicate of Gaster & Jones, "A Polymorphic
Type System for Extensible Records and Variants" (NOTTCS-TR-96-3, 1996), minus
the rows: records here are nominal, so its entailment is a declaration lookup
and there is nothing to unify.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .decls import DeclTable
from .errors import Span, TypeError_
from .types import (
    INT, Pred, TBottom, TCon, TLabel, TVar, Type, join, prune, show, type_key,
    unify,
)

HAS_FIELD = "HasField"


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
    """A predicate that must hold, settled against the domain's entailment.

    `context` is the verb to use if the predicate turns out to be unsatisfiable
    -- "read" or "mutate" for a `HasField`, which is the one thing about a
    field access that the predicate itself does not record.
    """

    pred: Pred
    span: Span | None = None
    context: str = ""


@dataclass
class Solver:
    """Settles the constraints generation emits.

    `queue` is the worklist and `deferred` holds the predicates that could not
    be settled yet. An equation never lands in `deferred` -- it is always
    either solvable or an error -- so everything there is a `CPred` waiting on
    a variable some later unification may resolve.
    """

    decls: DeclTable | None = None
    queue: list[Constraint] = field(default_factory=list)
    deferred: list[CPred] = field(default_factory=list)

    # -- generation-facing API ---------------------------------------------

    def emit(self, c: Constraint) -> None:
        self.queue.append(c)
        self.drain()

    def eq(self, a: Type, b: Type, span: Span | None = None, context: str = "") -> None:
        self.emit(CEq(a, b, span, context))

    def pred(self, p: Pred, span: Span | None = None, context: str = "") -> None:
        self.emit(CPred(p, span, context))

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
            if not self.entail(c):
                self.deferred.append(c)
            return
        raise AssertionError(f"unhandled constraint {type(c).__name__}")

    def settle(self) -> None:
        """Retry deferred predicates until no further one can be discharged.

        Each round re-emits what is left; a predicate that is still stuck goes
        straight back on `deferred`. Discharging one can bind variables another
        was waiting on, which is why this iterates rather than making a single
        pass -- and why it may stop only when a round discharges nothing, since
        `deferred` shrinks monotonically and cannot grow.
        """
        while self.deferred:
            self.improve()
            pending, self.deferred = self.deferred, []
            for c in pending:
                self.emit(c)
            if len(self.deferred) >= len(pending):
                return

    def improve(self) -> None:
        """Equate the results of demands for one field of one receiver.

        `HasField` is a function of its receiver: a record type has exactly one
        type for a given field. So two stuck demands `HasField l r a` and
        `HasField l r b` force `a ~ b`, even though neither can be discharged
        while `r` is unknown. Without this the context keeps one predicate per
        syntactic access -- bf.tl's `move` reads `t.data` twice and ended up
        carrying both `HasField "data" a b` and `HasField "data" a (Array Int)`,
        which is redundant and strictly weaker.

        This is an improvement rule in the sense of Jones, "Simplifying and
        Improving Qualified Types" (FPCA 1995): it adds no solutions, it only
        commits to ones that every solution shares. It is exactly the content a
        functional dependency would carry, available without one because
        `HasField` is built in rather than user-declared.
        """
        seen: dict[tuple, Type] = {}
        for c in self.deferred:
            label, receiver, result = c.pred.args
            assert isinstance(label, TLabel)
            key = (label.name, type_key(receiver))
            if key in seen:
                unify(seen[key], result, c.span, "a field access")
            else:
                seen[key] = result

    def entail(self, c: CPred) -> bool:
        """Try to discharge one predicate. False means "not yet" -- defer it.

        An unsatisfiable predicate raises; only genuine lack of information
        returns False, so nothing can be quietly dropped.
        """
        if c.pred.name == HAS_FIELD:
            return self._has_field(c)
        raise TypeError_(f"no rule for predicate '{c.pred.name}'", c.span)

    def _has_field(self, c: CPred) -> bool:
        label, receiver, result = c.pred.args
        assert isinstance(label, TLabel)
        receiver = prune(receiver)

        if isinstance(receiver, TVar):
            return False  # nothing known about the receiver yet

        if isinstance(receiver, TBottom):
            # Bottom is absorbed by whatever it meets, so it satisfies any
            # field demand vacuously -- there is no value to read one from.
            return True

        if isinstance(receiver, TCon):
            if receiver.name == "Array":
                if label.name in ("length", "capacity"):
                    # Section 8.3: both are readable and writable.
                    unify(result, INT, c.span, "a field access")
                    return True
                raise TypeError_(
                    f"an Array has no field '{label.name}' "
                    f"(only 'length' and 'capacity')",
                    c.span,
                )
            names = self.decls.record_fields(receiver)
            if names is not None:
                if label.name not in names:
                    raise TypeError_(
                        f"type '{receiver.name}' has no field '{label.name}' "
                        f"(it has: {', '.join(names)})",
                        c.span,
                    )
                unify(result, self.decls.field_type(receiver, label.name),
                      c.span, "a field access")
                return True

        raise TypeError_(
            f"cannot {c.context} field '{label.name}': '{show(receiver)}' is not "
            f"a single-variant record type. Multi-variant types are immutable "
            f"and are taken apart with 'match'.",
            c.span,
        )

    # -- generalization ----------------------------------------------------

    def retained(self, level: int) -> list[CPred]:
        """Deferred predicates general enough to be quantified at `level`.

        The test is the one `generalize` applies to type variables: a predicate
        tied to a variable created deeper than the binder travels in the scheme;
        one tied to an outer variable stays behind for an enclosing binding to
        settle. Predicates that travel are removed from `deferred`, since this
        binding has taken responsibility for them.
        """
        keep: list[CPred] = []
        retained: list[CPred] = []
        seen: set[tuple] = set()
        for c in self.deferred:
            if c.pred.level() <= level:
                keep.append(c)
                continue
            # Two demands for the same field of the same type are one
            # obligation; the context should not repeat itself.
            key = c.pred.key()
            if key not in seen:
                seen.add(key)
                retained.append(c)
        self.deferred = keep
        return retained
