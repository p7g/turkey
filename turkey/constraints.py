"""The constraint language, and the solver that settles it.

Inference is split the way HM(X) splits it (Odersky, Sulzmann & Wehr, "Type
Inference with Constrained Types", TAPOS 1999; Pottier & Remy, "The Essence of
ML Type Inference", ATTAPL ch. 10). `infer.py` walks the AST and *builds* a
constraint; it decides nothing and never looks a name up. This module solves
that constraint, and owns the two things solving needs: the type environment,
and the stack of pools that decides what generalizes.

The constraint forms are ATTAPL's, with one generalization -- a binder may bind
several names at once, since `let (a, b) = e` and a mutually recursive `fun`
group both need it:

    t1 ~ t2            CEq         equality
    P t                CPred       a predicate of the domain X
    C1 and C2          CAnd        conjunction
    exists a. C        CExists     variables generation invented
    def x : t in C     CDef        monomorphic binding
    let x = C1 in C2   CLet        generalize C1, bind the schemes, solve C2
    x <= t             CInstance   a use site

**Ranks live here, not in the generator.** A variable's rank is the depth of
the binder it was created under, and `CLet` is where that depth changes -- so
the solver assigns ranks as it descends, and the generator never mentions them.
The pools are Remy's (INRIA RR-1766, 1992; Kiselyov, "Efficient and Insightful
Generalization", 2013): one list of variables per rank, so that leaving a rank
is a walk over the variables born there rather than a scan of the environment.
Leaving rank r partitions its pool -- variables still at r are quantified, and
variables unification has lowered are *adopted* by the parent pool. That
adoption is the whole of what an earlier version of this checker had to do by
hand, and forgetting it was a soundness hole.

`HasField` is the `r \\ l` predicate of Gaster & Jones, "A Polymorphic Type
System for Extensible Records and Variants" (NOTTCS-TR-96-3, 1996) with the
rows removed; in that shape it is GHC's `HasField x r a | x r -> a` (Gundry's
`OverloadedRecordFields`, in `GHC.Records` since 8.2). Records here are
nominal, so entailment is a declaration lookup. See `improve` for what the
missing rows cost.

`OneOf` is the other predicate: the set of types a numeric literal could have.
It is closed -- membership is decided by a built-in table, not by anything a
program can declare -- so it needs no evidence at runtime, only a decision.
Three rules settle it, in `_one_of` and `improve_numeric`: a singleton set is
an equation, two sets over the same variable intersect, and a set that survives
to a point where nothing can ever narrow it further is *defaulted*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .decls import DeclTable
from .errors import Span, TypeError_
from .types import (
    INT, Pred, Scheme, TBottom, TCon, TLabel, TSet, TVar, Type, generalize,
    instantiate_qual, mono, numeric_order, numeric_type, prune, show, show_pred,
    sort_numeric, type_key, unify, vars_of,
)

HAS_FIELD = "HasField"
ONE_OF = "OneOf"


# ------------------------------------------------------------------ the tree


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

    `context` is the verb to use if it turns out to be unsatisfiable -- "read"
    or "mutate" for a `HasField`, which is the one thing about a field access
    the predicate itself does not record.
    """

    pred: Pred
    span: Span | None = None
    context: str = ""


@dataclass
class CAnd(Constraint):
    parts: list[Constraint] = field(default_factory=list)


@dataclass
class CExists(Constraint):
    """`exists vars. body` -- the variables generation invented under a binder.

    This is how the generator hands the solver a set of variables without
    knowing what rank they belong to. The solver stamps them on the way in.
    """

    vars: list[TVar]
    body: Constraint


@dataclass
class CDef(Constraint):
    """Bind names to *types* -- monomorphically -- while solving `body`.

    Function parameters, `match` arm binders, and the placeholders of a
    recursive group. Nothing is generalized, so nothing changes rank.
    """

    binds: list[tuple[str, Type]]
    body: Constraint
    top_level: bool = False


@dataclass
class CLet(Constraint):
    """Solve `defn` one rank deeper, generalize, bind the schemes, solve `body`.

    The single place generalization happens. Everything that used to be spread
    across the generator -- lowering escaped variables, settling predicates
    before deciding what to keep, splitting a context between the schemes that
    can carry it -- happens here, once.
    """

    binds: list[tuple[str, Type]]
    defn: Constraint
    body: Constraint
    span: Span | None = None
    top_level: bool = False


@dataclass
class CInstance(Constraint):
    """`name <= t`: instantiate the scheme bound to `name` at `t`.

    The generator emits this instead of looking the name up, which is what lets
    it run without a type environment at all.
    """

    name: str
    type: Type
    span: Span | None = None


# ------------------------------------------------------------- the environment


@dataclass
class Binding:
    scheme: Scheme
    mutable: bool  # declared with `var`, so assignable


class Env:
    """A chain of scopes. Lookup walks outward; definition always writes here."""

    def __init__(self, parent: Env | None = None):
        self.parent = parent
        self.names: dict[str, Binding] = {}

    def child(self) -> Env:
        return Env(self)

    def define(self, name: str, binding: Binding) -> None:
        self.names[name] = binding

    def lookup(self, name: str) -> Binding | None:
        env: Env | None = self
        while env is not None:
            if name in env.names:
                return env.names[name]
            env = env.parent
        return None


# ------------------------------------------------------------------ the solver


class Solver:
    """Settles a constraint, and owns the rank discipline while doing it.

    `pools[r]` holds the variables born at rank r. `deferred` holds predicates
    that could not be settled yet; an equation never lands there, since it is
    always either solvable or an error.
    """

    def __init__(self, decls: DeclTable, env: Env):
        self.decls = decls
        self.env = env
        self.pools: list[list[TVar]] = [[]]
        self.deferred: list[CPred] = []
        # Schemes of every name a `CLet` bound, for the `types` command.
        self.top_level: Env = env

    # -- ranks -------------------------------------------------------------

    @property
    def rank(self) -> int:
        return len(self.pools) - 1

    def fresh(self) -> TVar:
        """A variable at the current rank, recorded in the current pool."""
        var = TVar(self.rank)
        self.pools[-1].append(var)
        return var

    def adopt(self, vars: list[TVar]) -> None:
        """Take responsibility for variables generation invented.

        They were created without a rank -- generation has no idea what rank it
        is under -- so they are stamped and pooled here.
        """
        for var in vars:
            var.level = self.rank
            self.pools[-1].append(var)

    # -- solving -----------------------------------------------------------

    def run(self, c: Constraint) -> None:
        self.solve(c)
        self.settle()
        # The outermost boundary: nothing further can narrow what is left, so
        # anything still open that has a default takes it, and the rest is an
        # error.
        while self.deferred and self.apply_defaults(list(self.deferred)):
            self.settle()
        self.reject_stranded(self.deferred)

    def solve(self, c: Constraint) -> None:
        if isinstance(c, CEq):
            unify(c.left, c.right, c.span, c.context)
            return
        if isinstance(c, CPred):
            if not self.entail(c):
                self.deferred.append(c)
            return
        if isinstance(c, CAnd):
            for part in c.parts:
                self.solve(part)
            return
        if isinstance(c, CExists):
            self.adopt(c.vars)
            self.solve(c.body)
            return
        if isinstance(c, CDef):
            with self.scope():
                for name, ty in c.binds:
                    binding = Binding(mono(ty), c.top_level)
                    self.env.define(name, binding)
                    if c.top_level:
                        self.top_level.define(name, binding)
                self.solve(c.body)
            return
        if isinstance(c, CLet):
            self.solve_let(c)
            return
        if isinstance(c, CInstance):
            self.solve_instance(c)
            return
        raise AssertionError(f"unhandled constraint {type(c).__name__}")

    def scope(self):
        return _Scope(self)

    def solve_instance(self, c: CInstance) -> None:
        """`name <= t`: instantiate, and re-emit the scheme's obligations.

        A scheme's predicates are demands on whoever instantiates it, so they
        come back as constraints over this site's fresh variables. The verb is
        "read": a scheme records which field it needs, not whether the access
        that produced the demand was a read or an assignment.
        """
        binding = self.env.lookup(c.name)
        assert binding is not None, f"generation let '{c.name}' through unbound"
        preds, ty = instantiate_qual(binding.scheme, self.fresh)
        unify(ty, c.type, c.span, "")
        for p in preds:
            self.solve(CPred(p, c.span, "read"))

    def solve_let(self, c: CLet) -> None:
        """Solve the definition one rank in, generalize, then solve the body.

        The order matters and is the reason this is one function. Predicates
        are settled *before* the split, so that anything the definition learned
        has been applied; the split then uses the same test generalization uses
        for type variables, and the pool is emptied either into the schemes or
        into the parent's pool.
        """
        self.pools.append([])
        self.solve(c.defn)
        self.settle()
        retained = self.split([ty for _, ty in c.binds])
        self.discharge_pool(self.pools.pop())

        with self.scope():
            for name, ty in c.binds:
                binding = Binding(generalize(ty, self.rank, _constrain(retained, ty)), False)
                self.env.define(name, binding)
                if c.top_level:
                    self.top_level.define(name, binding)
            self.solve(c.body)

    def split(self, types: list[Type]) -> list[CPred]:
        """The predicates the schemes about to be built will carry.

        Defaulting sits between splitting and reporting, because ambiguity is
        precisely the condition that licenses it: a predicate no scheme can
        carry mentions a variable that appears in no type being generalized, so
        no use site will ever pin it. Choosing for one can unblock another, so
        the split is redone until it stops moving.
        """
        while True:
            retained = self.retained(self.rank - 1)
            stranded = _unattributed(retained, types)
            if not stranded or not self.apply_defaults(stranded):
                self.reject_stranded(stranded)
                return retained
            # Something was decided. Put the split back and settle again, so
            # the choice is visible to everything that was waiting on it.
            self.deferred.extend(retained)
            self.settle()

    def discharge_pool(self, young: list[TVar]) -> None:
        """Hand every variable born at the rank just left to its new owner.

        One that is still at that rank belongs to the scheme about to be built,
        and `generalize` will quantify it. One unification has lowered escaped
        into an enclosing binding, and the parent pool adopts it so that the
        *next* binder sees it as its own. Doing this structurally is the point
        of the pools: there is no place left to forget it.
        """
        left = self.rank + 1
        for var in young:
            pruned = prune(var)
            if not isinstance(pruned, TVar):
                continue  # bound during solving; unify adjusted what it points at
            if pruned.level < left:
                self.pools[-1].append(pruned)

    # -- predicates --------------------------------------------------------

    def settle(self) -> None:
        """Retry deferred predicates until no further one can be discharged.

        Each round re-solves what is left; a predicate that is still stuck goes
        straight back on `deferred`. Discharging one can bind variables another
        was waiting on, which is why this iterates -- and why it may stop only
        when a round discharges nothing, since `deferred` shrinks monotonically
        and cannot grow.
        """
        while self.deferred:
            self.improve()
            pending, self.deferred = self.deferred, []
            for c in pending:
                self.solve(c)
            if len(self.deferred) >= len(pending):
                return

    def improve(self) -> None:
        """Equate the results of demands for one field of one receiver.

        `HasField` is a function of its receiver: a record type has exactly one
        type for a given field. So two stuck demands `HasField l r a` and
        `HasField l r b` force `a ~ b`, even though neither can be discharged
        while `r` is unknown.

        In Gaster & Jones this rule does not exist, because it is not a rule:
        with rows, the field's type sits *inside* the receiver's type
        (`Rec (l:a | r)`), and two accesses agree by ordinary unification.
        Nominal records have no row to put it in, so the field type lives in
        the predicate's third argument and equating two of them has to be said
        out loud. That makes this the functional dependency of GHC's
        `HasField x r a | x r -> a`, and an improvement rule in the sense of
        Jones, "Simplifying and Improving Qualified Types" (FPCA 1995): it adds
        no solutions, it only commits to ones that every solution shares.

        (Ohori's kinded records, TOPLAS 1995, would give it back for free --
        the constraint rides on the variable and kind unification does the
        merging. That is declined deliberately: it puts record knowledge inside
        `unify`, which is the coupling HM(X) is chosen to avoid.)
        """
        self.improve_numeric()
        seen: dict[tuple, Type] = {}
        for c in self.deferred:
            if c.pred.name != HAS_FIELD:
                continue
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
        if c.pred.name == ONE_OF:
            return self._one_of(c)
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

    def _one_of(self, c: CPred) -> bool:
        """`OneOf t {...}`: `t` must be one of a closed set of built-in types.

        A **singleton set is an equation** and is discharged as one, right
        here, at the moment the constraint is reached. That is not an
        optimization: deferring it would let a later unification bind `t` to
        something else first, and the mismatch would then be reported against
        the literal rather than against whatever disagreed with it. With both
        of today's sets singletons, this branch is the only one that ever runs,
        and a numeric literal behaves exactly as if it had been given its type
        outright.
        """
        t, candidates = c.pred.args
        assert isinstance(candidates, TSet)
        names = candidates.names

        if not names:
            raise TypeError_(
                "this numeric literal is not representable in any numeric type",
                c.span,
            )
        if len(names) == 1:
            # Same argument order as the equation this stands in for, so a
            # mismatch reads the way it did before literals had sets.
            unify(prune(t), numeric_type(next(iter(names))), c.span, c.context)
            return True

        t = prune(t)
        if isinstance(t, TBottom):
            return True  # absorbed; there is no value to represent
        if isinstance(t, TVar):
            return False  # still open -- improvement or defaulting will decide
        if isinstance(t, TCon) and not t.args and t.name in names:
            return True
        raise TypeError_(
            f"expected one of {', '.join(sort_numeric(names))}, found {show(t)}"
            + (f" in {c.context}" if c.context else ""),
            c.span,
        )

    def improve_numeric(self) -> None:
        """Intersect `OneOf` sets that constrain the same variable.

        Two demands on one variable are one demand on the intersection, and an
        empty intersection is an error rather than a deferral -- nothing later
        can widen a closed set. Like `improve` for `HasField` this adds no
        solutions; it only commits to what every solution shares. Narrowing to
        a singleton is what lets the next round discharge it as an equation.
        """
        merged: dict[tuple, CPred] = {}
        out: list[CPred] = []
        for c in self.deferred:
            if c.pred.name != ONE_OF:
                out.append(c)
                continue
            key = type_key(c.pred.args[0])
            first = merged.get(key)
            if first is None:
                merged[key] = c
                out.append(c)
                continue
            names = first.pred.args[1].names & c.pred.args[1].names
            if not names:
                raise TypeError_(
                    f"no numeric type is both {show(first.pred.args[1])} and "
                    f"{show(c.pred.args[1])}",
                    c.span,
                )
            first.pred.args[1] = TSet(names)
        self.deferred = out

    def apply_defaults(self, stuck: list[CPred]) -> bool:
        """Resolve `OneOf`s that nothing will ever narrow, by picking a member.

        This is Haskell's defaulting, and it applies for the same reason: the
        variable is ambiguous -- it appears in no type being generalized, so no
        use site can pin it and no scheme can carry it. The choice is the first
        member of the set in tower order, which is `Int` for an integral
        literal and, once `Float` is renamed, `Double` for a decimal one.

        Only `OneOf` defaults. A stranded `HasField` is a genuine error: there
        is no preferred record type to guess.
        """
        progress = False
        for c in stuck:
            if c.pred.name != ONE_OF:
                continue
            t, candidates = c.pred.args
            assert isinstance(candidates, TSet)
            order = numeric_order()
            choice = next((n for n in order if n in candidates.names), None)
            if choice is None:
                continue
            unify(prune(t), numeric_type(choice), c.span, c.context)
            progress = True
        return progress

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

    def reject_stranded(self, stranded: list[CPred]) -> None:
        """Report a predicate that can be neither discharged nor quantified.

        A `HasField` whose receiver is still a variable is fine as long as some
        scheme can carry it. One that reaches a generalization point without
        being reachable from any of the types being generalized is stuck for
        good: no later unification can name it, because nothing else mentions
        it.
        """
        if not stranded:
            return
        c = stranded[0]
        if c.pred.name == HAS_FIELD:
            label = c.pred.args[0]
            assert isinstance(label, TLabel)
            raise TypeError_(
                f"cannot determine the type of the value whose field "
                f"'{label.name}' is being accessed. Add a type annotation.",
                c.span,
            )
        raise TypeError_(
            f"cannot determine a type satisfying '{show_pred(c.pred)}'. "
            f"Add a type annotation.",
            c.span,
        )


class _Scope:
    """Restore `solver.env` when the `with` block ends."""

    def __init__(self, solver: Solver):
        self.solver = solver
        self.previous = solver.env

    def __enter__(self) -> Env:
        self.solver.env = self.previous.child()
        return self.solver.env

    def __exit__(self, *exc) -> bool:
        self.solver.env = self.previous
        return False


# ------------------------------------------------------------ context splitting


def reach(preds: list[CPred], types: list[Type]) -> set[int]:
    """The variables `types` pin, following predicates to a fixed point.

    Membership is transitive because a `HasField` is a *function* of its
    receiver: fix the record and the field type follows. So a variable that
    appears nowhere in the type is still determined, as long as some chain of
    predicates connects it to one that is. `t.data.length` in bf.tl is exactly
    that -- it demands `HasField "length" d n` where `d` is reachable only
    through `HasField "data" t d` -- and it is why this is a closure rather
    than a single intersection.

    That functional reading is also why the usual ambiguity objection to a
    variable appearing only in the context does not apply here.
    """
    ids = {v.id for t in types for v in vars_of(t)}
    changed = True
    while changed:
        changed = False
        for c in preds:
            reachable = vars_of(*c.pred.args)
            if any(v.id in ids for v in reachable):
                for v in reachable:
                    if v.id not in ids:
                        ids.add(v.id)
                        changed = True
    return ids


def _constrain(preds: list[CPred], t: Type) -> list[Pred]:
    """The retained predicates that constrain `t`."""
    ids = reach(preds, [t])
    return [c.pred for c in preds if _touches(c, ids)]


def _unattributed(preds: list[CPred], types: list[Type]) -> list[CPred]:
    """The retained predicates no scheme in the group will carry."""
    ids = reach(preds, types)
    return [c for c in preds if not _touches(c, ids)]


def _touches(c: CPred, ids: set[int]) -> bool:
    return any(v.id in ids for v in vars_of(*c.pred.args))
