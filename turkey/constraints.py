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
    given |- C         CAssume     the obligations a signature already grants

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

A class predicate `C t` is the fourth form. It is discharged from the instance
table (`turkey/classes.py`), or from the assumptions a `CAssume` has in scope:
checking an instance method against its class's signature is the one place a
predicate is *granted* rather than proved, because the signature said so.

It is also the only form that survives into the running program, so solving
leaves two things behind for elaboration (`turkey/evidence.py`). A `CInstance`
records what its use site turned out to demand, and the scopes that were open
around it. A `CLet` records what its schemes retained, as the dictionary
parameters the group takes. Both are written where the decision is already
being made -- there is no second pass over the constraint to find them, and no
second notion of what a scheme carries.

An associated type family makes an *equation* deferrable: `Elem a ~ Int` with
`a` unbound is neither solvable nor false, so `unify` hands it back rather than
deciding. Since delta 39 it comes back as the equality predicate `~`, which is
the fifth form of the domain and the reason there is one queue here rather than
two. An equation and a predicate wait on the same thing, so the binder that can
carry one can carry the other: `retained` puts a stuck `Item a ~ Op` into a
scheme exactly as it puts `Show (Item a)` there, and the caller decides it.
Equalities and predicates still unblock each other -- reducing a family decides
a type a predicate was waiting on, and discharging a predicate binds the
variable a family was applied to -- which is why `settle` iterates.

A `~` needs no evidence: it is not a class, so `is_class` is false for it and
the same filters that erase `HasField` erase it. What it does carry is a
*rewrite*, when it arrives as a given -- see `Solver.reduce`.

`OneOf` is the other predicate: the set of types a numeric literal could have.
It is closed -- membership is decided by a built-in table, not by anything a
program can declare -- so it needs no evidence at runtime, only a decision.
Three rules settle it, in `_one_of` and `improve_numeric`: a singleton set is
an equation, two sets over the same variable intersect, and a set that survives
to a point where nothing can ever narrow it further is *defaulted*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .classes import ClassTable
from .decls import DeclTable
from .evidence import Abstraction, Scope, Use, dict_name
from .errors import Span, TypeError_
from .types import (
    EQUALS, INT, NO_SCOPE, Pred, Scheme, TBottom, TCon, TFam, TLabel, TSet, TVar,
    Type,
    generalize, instantiate_qual, mono, numeric_order, numeric_type, prune, show,
    show_pred, sort_numeric, spine, type_key, unify, vars_of,
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
class CBind(Constraint):
    """Bind names to *schemes* already built, while solving `body`.

    The one binder whose schemes solving does not discover. A `fun` with a
    complete annotation states its own type, so there is nothing to generalize
    and nothing to wait for: the scheme is in scope from the group's first
    line, which is exactly what lets a recursive occurrence *instantiate* it
    rather than share one monomorphic placeholder.

    That is polymorphic recursion, and it is admissible here for the reason it
    is inadmissible in `CLet`. Inferring it is undecidable (Henglein 1993;
    Kfoury, Tiuryn and Urzyczyn 1993, both by reduction with semi-unification),
    but *checking* a recursive use against a type the programmer wrote down is
    an ordinary instantiation. So the rule is the usual one: rejected when the
    type has to be guessed, accepted when it has been stated.
    """

    binds: list[tuple[str, Scheme]]
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
    dicts: Abstraction | None = None
    #: Skolems that live exactly as long as `defn`'s rank -- the constants a
    #: signature's variables became. Carried here for the same reason ranks
    #: are the solver's business at all: this is the one place that knows how
    #: deep the definition sits, so this is where they can be stamped.
    skolems: list[TCon] = field(default_factory=list)


@dataclass
class CAssume(Constraint):
    """Solve `body` with `givens` taken as facts.

    Each given carries the runtime name its dictionary will arrive under, since
    a granted predicate is exactly one the body may need evidence for.

    The only source of assumptions is a *declared* type that a body is being
    checked against -- an instance method, or a class's default method. There
    the class's own predicate, the instance's context and the method's context
    are all facts, not obligations, and the body is entitled to use them. This
    is the local-assumption form, and it is deliberately the whole of it: an
    assumption introduced by a *pattern* is what makes GADTs destroy principal
    types, and none is introduced here.
    """

    givens: list[tuple[str, Pred]]
    body: Constraint

    @property
    def preds(self) -> list[Pred]:
        return [p for _, p in self.givens]


@dataclass
class CInstance(Constraint):
    """`name <= t`: instantiate the scheme bound to `name` at `t`.

    The generator emits this instead of looking the name up, which is what lets
    it run without a type environment at all.
    """

    name: str
    type: Type
    span: Span | None = None
    use: Use | None = None


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

    def __init__(self, decls: DeclTable, env: Env, classes: ClassTable | None = None):
        self.decls = decls
        self.classes = classes if classes is not None else ClassTable(decls)
        self.env = env
        # Predicates a `CAssume` currently grants. A stack in effect, restored
        # on the way out of each one.
        self.assumptions: list[Pred] = []
        # The dictionary scopes open at the point being solved, innermost last.
        # A `Use` keeps the objects rather than their contents, because what a
        # binder makes available is only known once its definition is solved.
        self.scopes: list[Scope] = []
        self.uses: list[Use] = []
        self.pools: list[list[TVar]] = [[]]
        self.deferred: list[CPred] = []
        # Equations `unify` could not decide: one side is a family application
        # whose argument is still open. This is the third outcome M7 gives
        # unification, and it is a queue for the same reason `deferred` is.
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

    # -- families (the `types.Families` protocol) --------------------------

    def reduce(self, t: TFam) -> Type | None:
        """`t`'s definition, from an assumption first and an instance after.

        A *given* equality is a reduction rule for the family it names, and
        this is where it is used. Discharging `Item c ~ Op` is not enough on
        its own: the body's `match op { Inc(n) -> ... }` cannot find its
        constructors unless `Item c` genuinely *becomes* `Op`, and `Item c`
        over a skolem will never reduce through the instance table. So the
        assumptions are consulted first, and the rule they supply is applied
        exactly as an instance's would be.

        `Classes.resolve_equality` guarantees each rule has a family
        application on the left and does not mention it on the right, so a
        family over a given skolem has one rule and rewriting terminates.
        """
        key = type_key(t)
        for pred in self.assumptions:
            if pred.name == EQUALS and type_key(pred.args[0]) == key:
                return pred.args[1]
        return self.classes.reduce_fam(t)

    def defer(self, a: Type, b: Type, span: Span | None, context: str) -> None:
        """Take an equation unification could not decide.

        It becomes an equality *predicate* rather than joining a queue of its
        own. That is the whole of delta 39: a stuck equation and a deferred
        predicate are waiting on the same thing, so the binder that can carry
        one can carry the other, and `retained` now sees both.

        Before queueing it, ask whether it *could* ever be decided: a family
        over a constructor no instance covers is stuck for good, and the
        equation's own span is the best place to say so. A family over a
        variable is merely waiting.
        """
        for t in (a, b):
            if isinstance(t, TFam):
                missing = self.classes.uncovered(t)
                # A skolem is a nullary constructor, so a family over one looks
                # exactly like a family over a type whose instance is missing.
                # It is not: the declaration assumed that instance, and which
                # one it will be is the caller's business. Such an equation is
                # merely stuck, and is reported as stuck.
                if missing is not None and not self.granted(missing):
                    raise TypeError_(
                        f"no instance for '{show_pred(missing)}', so "
                        f"'{show(t)}' has no definition",
                        span,
                    )
        # Oriented family-first, so a carried equality reads the way a written
        # one has to be written (`Classes._resolve_equality`): `Item a ~ Op`,
        # never `Op ~ Item a`. Nothing depends on the order -- `_equals` is
        # symmetric -- but a scheme is read by people.
        if isinstance(prune(b), TFam) and not isinstance(prune(a), TFam):
            a, b = b, a
        self.deferred.append(CPred(Pred(EQUALS, [a, b]), span, context))

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
            unify(c.left, c.right, c.span, c.context, self)
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
        if isinstance(c, CAssume):
            saved = self.assumptions
            self.assumptions = saved + c.preds
            self.scopes.append(Scope(list(c.givens)))
            try:
                self.solve(c.body)
            finally:
                self.assumptions = saved
                self.scopes.pop()
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
        if isinstance(c, CBind):
            with self.scope():
                for name, scheme in c.binds:
                    binding = Binding(scheme, c.top_level)
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
        preds, ty, type_args = instantiate_qual(binding.scheme, self.fresh)
        unify(ty, c.type, c.span, "", self)
        if c.use is not None:
            # Only the class predicates: `HasField` and `OneOf` are discharged
            # by a lookup and a decision, and leave nothing behind to pass.
            c.use.preds = [p for p in preds if self.classes.is_class(p.name)]
            # Every type argument, though, and unfiltered: a type application
            # has to name one per quantified variable or it is not the scheme's
            # instantiation. They are recorded before `unify` has run to
            # completion and are variables at this point, which is correct --
            # what they turn out to be is read back after solving.
            c.use.type_args = type_args
            c.use.scopes = tuple(self.scopes)
            self.uses.append(c.use)
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
        # Now that the rank exists, the constants that belong to it can say so.
        # `escaping` needs nothing else: a variable born shallower than this is
        # a variable no skolem of this binder may bind.
        #
        # And only while the rank exists. `solve_let` is the whole of a
        # skolem's life, so the stamp is lifted on the way out -- not tidiness,
        # but correctness: `check_exhaustiveness` unifies constructor types
        # against a solved scrutinee long afterwards, at no rank at all, and a
        # constant still claiming a rank there would look like an escape.
        for con in c.skolems:
            con.level = self.rank
        try:
            self._let(c)
        finally:
            for con in c.skolems:
                con.level = NO_SCOPE

    def _let(self, c: CLet) -> None:
        scope = Scope()
        self.scopes.append(scope)
        first_use = len(self.uses)
        self.solve(c.defn)
        self.scopes.pop()
        self.settle()
        retained = self.split([ty for _, ty in c.binds])
        # An equation tied to a variable this binder quantifies can never be
        # decided by an enclosing one -- nothing outside mentions the variable
        # -- so this is where it is stuck for good. The test is `retained`'s,
        # for the same reason: an equation and a predicate are both waiting on
        # the same variables.
        self.discharge_pool(self.pools.pop())

        # The class predicates are shared across the group, and the dictionary
        # parameters with them. One member's body may call another's, so a
        # per-name context would leave that call needing a dictionary the
        # caller's own signature never promised. Everything else stays per
        # name: a `HasField` is erased, so nothing has to agree about it.
        shared = self.classes.simplify(
            [p.pred for p in retained if self.classes.is_class(p.pred.name)]
        )
        scope.givens = [(dict_name(p.name), p) for p in shared]
        # A call from one member of the group to another -- or to itself -- was
        # solved against the *monomorphic* placeholder `CDef` bound, so it
        # demanded nothing and would be handed the undischarged binding at run
        # time. It needs the group's own dictionaries, which is exactly what
        # the scope now holds, so the demand is recorded here and resolved like
        # any other: a use inside the definition, of a name the group binds,
        # that asked for nothing.
        group = {name for name, _ in c.binds}
        if shared:
            for use in self.uses[first_use:]:
                if (not use.preds and use.name in group
                        and any(s is scope for s in use.scopes)):
                    use.preds = list(shared)
        if c.dicts is not None:
            c.dicts.params = [n for n, _ in scope.givens]
            c.dicts.preds = shared

        with self.scope():
            for name, ty in c.binds:
                own = [p for p in _constrain(retained, ty)
                       if not self.classes.is_class(p.name)]
                preds = self.classes.simplify(own + shared)
                binding = Binding(generalize(ty, self.rank, preds), False)
                if c.dicts is not None:
                    # Recorded here because here is the only place it exists.
                    # A local binding's scheme goes into an environment that is
                    # popped as soon as `c.body` has been solved, so a later
                    # pass asking what `fun go()` generalized to has nowhere
                    # left to ask. See `evidence.Abstraction.schemes`.
                    c.dicts.schemes[name] = binding.scheme
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
        when a round discharges nothing.

        M7 gave a family application the power to defer an *equation* too, and
        each kind can unblock the other: reducing `Elem (Array Int)` decides a
        type a `Show` predicate was waiting on, and discharging a predicate can
        bind the variable a family was applied to. Since delta 39 an equation
        *is* a predicate, so there is one queue rather than two -- but the
        order within a round still matters, and equalities go first for the
        same reason they used to: a family that has become reducible decides a
        type, and a predicate waiting on that type can then settle in the same
        round.
        """
        while self.deferred:
            self.improve()
            pending, self.deferred = self.deferred, []
            pending.sort(key=lambda c: c.pred.name != EQUALS)
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
        self.improve_families()
        seen: dict[tuple, Type] = {}
        for c in self.deferred:
            if c.pred.name != HAS_FIELD:
                continue
            label, receiver, result = c.pred.args
            assert isinstance(label, TLabel)
            key = (label.name, type_key(receiver))
            if key in seen:
                unify(seen[key], result, c.span, "a field access", self)
            else:
                seen[key] = result

    def improve_families(self) -> None:
        """Equate the answers of two equalities on one family application.

        The same rule as `improve`'s, and for the same reason: a family is a
        *function* of its argument, so `Item c ~ Op` and `Item c ~ Char`
        cannot both hold. Neither is discharged while `Item c` is stuck, so
        without this they would both be carried and the contradiction would
        never be reported at all.
        """
        seen: dict[tuple, tuple[Type, CPred]] = {}
        for c in self.deferred:
            if c.pred.name != EQUALS:
                continue
            left, right = c.pred.args
            if not isinstance(prune(left), TFam):
                continue
            key = type_key(left)
            if key in seen:
                first, at = seen[key]
                unify(first, right, c.span or at.span,
                      f"the answers given for '{show(left, free_prefix='')}'",
                      self)
            else:
                seen[key] = (right, c)

    def entail(self, c: CPred) -> bool:
        """Try to discharge one predicate. False means "not yet" -- defer it.

        An unsatisfiable predicate raises; only genuine lack of information
        returns False, so nothing can be quietly dropped.
        """
        if c.pred.name == EQUALS:
            return self._equals(c)
        if c.pred.name == HAS_FIELD:
            return self._has_field(c)
        if c.pred.name == ONE_OF:
            return self._one_of(c)
        if self.classes.is_class(c.pred.name):
            return self._class(c)
        raise TypeError_(f"no rule for predicate '{c.pred.name}'", c.span)

    def _equals(self, c: CPred) -> bool:
        """`s ~ t`: retry the equation now that something may have moved.

        Normalizing is what makes progress: `reduce` consults the assumptions
        and then the instance table, so a side that has become reducible --
        because a variable was decided, or because a given names it -- reduces
        here. What is left decides the answer. Two identical family
        applications are equal by reflexivity, and a side that is *still* a
        stuck family means the equation is not yet decidable, which is
        `False`: back onto `deferred`, where `retained` may put it in a scheme
        and hand it to the caller. Anything else is an ordinary unification.
        """
        left = self.classes.normalize(c.pred.args[0])
        right = self.classes.normalize(c.pred.args[1])
        c.pred.args = [left, right]
        if type_key(left) == type_key(right):
            return True
        if isinstance(left, TFam) or isinstance(right, TFam):
            return False
        unify(left, right, c.span, c.context, self)
        return True

    def granted(self, pred: Pred) -> bool:
        """Is `pred` a fact the enclosing declaration already assumed?

        Both sides are normalized before they are compared, since an
        assumption is written down and a demand is discovered, and the two may
        name the same type by different routes.
        """
        key = Pred(pred.name, [self.classes.normalize(pred.args[0])]).key()
        return any(
            Pred(q.name, [self.classes.normalize(q.args[0])]).key() == key
            for a in self.assumptions for q in self.classes.by_super(a)
        )

    def _class(self, c: CPred) -> bool:
        """`C t`: an assumption grants it, or an instance covers it.

        A predicate whose argument is still headed by a variable defers -- a
        later unification may yet decide which instance applies. One headed by
        anything rigid is decided here and now, which is what keeps a missing
        instance a local error naming the type that lacks one, rather than a
        stranded predicate reported at the end of a binding group.
        """
        t = self.classes.normalize(c.pred.args[0])
        if isinstance(t, TBottom):
            return True  # absorbed; there is no value to find a method for
        pred = Pred(c.pred.name, [t])
        # Assumptions are consulted before the shape of the argument is, and
        # that order matters: a given may be *about* a family application, as
        # `[Container c, Show (Elem c)]` is, and `Elem c` over a skolem never
        # reduces. Testing the head first would defer such a predicate for
        # ever, and it would be reported as stranded at the end of the group --
        # a demand the declaration had already granted.
        if self.granted(pred):
            return True
        head, _ = spine(t)
        if isinstance(head, (TVar, TFam)):
            return False
        obligations = self.classes.by_inst(pred)
        if obligations is None:
            raise TypeError_(f"no instance for '{show_pred(pred)}'", c.span)
        # The instance's own context becomes this site's, over the types the
        # match supplied: `Eq (Array a)` leaves `Eq a` behind.
        for q in obligations:
            self.solve(CPred(q, c.span, c.context))
        return True

    def _has_field(self, c: CPred) -> bool:
        label, receiver, result = c.pred.args
        assert isinstance(label, TLabel)
        receiver = self.classes.normalize(receiver)

        if isinstance(receiver, (TVar, TFam)):
            return False  # nothing known about the receiver yet

        if isinstance(receiver, TBottom):
            # Bottom is absorbed by whatever it meets, so it satisfies any
            # field demand vacuously -- there is no value to read one from.
            return True

        # The receiver is asked about by its *head*: `Array Int` and
        # `Stack a` are applications now, and what decides whether a field
        # exists is the constructor they are headed by.
        head, _ = spine(receiver)
        if isinstance(head, TCon):
            if head.name == "Array":
                if label.name in ("length", "capacity"):
                    # Section 8.3: both are readable and writable.
                    unify(result, INT, c.span, "a field access", self)
                    return True
                raise TypeError_(
                    f"an Array has no field '{label.name}' "
                    f"(only 'length' and 'capacity')",
                    c.span,
                )
            names = self.decls.record_fields(head.name)
            if names is not None:
                if label.name not in names:
                    raise TypeError_(
                        f"type '{head.name}' has no field '{label.name}' "
                        f"(it has: {', '.join(names)})",
                        c.span,
                    )
                unify(result, self.decls.field_type(receiver, label.name),
                      c.span, "a field access", self)
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
            unify(prune(t), numeric_type(next(iter(names))), c.span, c.context, self)
            return True

        t = self.classes.normalize(t)
        if isinstance(t, TBottom):
            return True  # absorbed; there is no value to represent
        if isinstance(t, (TVar, TFam)):
            return False  # still open -- improvement or defaulting will decide
        if isinstance(t, TCon) and t.name in names:
            return True
        # The span is always the literal's, so name it: "expected one of ..."
        # alone reads as though something at this position asked for a numeric
        # type, when in fact this position *is* the numeral.
        raise TypeError_(
            f"a numeric literal cannot have type '{show(t)}'; it must be one "
            f"of {', '.join(sort_numeric(names))}",
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
            unify(prune(t), numeric_type(choice), c.span, c.context, self)
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
        if c.pred.name == EQUALS:
            # An equality that reaches here is not wrong, it is undecidable,
            # and the blame goes on the family rather than on what it was
            # compared with: what is missing is the knowledge of which instance
            # defines it. A scheme would have carried it happily -- that is the
            # ordinary case since delta 39 -- so getting this far means no type
            # being generalized mentions the family's argument, and no caller
            # will ever supply one.
            left, right = c.pred.args
            fam = left if isinstance(left, TFam) else right
            other = right if fam is left else left
            assert isinstance(fam, TFam)
            cls = self.classes.decls.families[fam.name].cls
            where = f" in {c.context}" if c.context else ""
            # Name the remedy, the way the other two branches below do. Since
            # delta 39 that remedy is no longer "add a type annotation": an
            # equality is something a *context* may state, so the fix is to
            # write in the signature what an inferred scheme would have carried
            # by itself.
            equality = (f"{show(fam, free_prefix='')} ~ "
                        f"{show(other, free_prefix='')}")
            raise TypeError_(
                f"cannot reduce '{show(fam, free_prefix='')}' to "
                f"'{show(other, free_prefix='')}'{where}: nothing says which "
                f"'{cls}' instance defines it. Add '{equality}' to the context.",
                c.span,
            )
        if c.pred.name == HAS_FIELD:
            label = c.pred.args[0]
            assert isinstance(label, TLabel)
            raise TypeError_(
                f"cannot determine the type of the value whose field "
                f"'{label.name}' is being accessed. Add a type annotation.",
                c.span,
            )
        raise TypeError_(
            f"cannot determine a type satisfying '{show_pred(c.pred, free_prefix="")}'. "
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
