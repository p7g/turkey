"""Dictionary-passing elaboration (design.md section 6; SPEC-DELTAS.md 30).

A class predicate is the only constraint form of the domain X that survives
into the running program. `HasField` is discharged by a declaration lookup and
`OneOf` by a decision, so both are *erased*; `Semigroup a` is not, because the
program still has to know which `combine` to call. What it needs is evidence,
and evidence is a dictionary -- Wadler & Blott's translation ("How to make
ad-hoc polymorphism less ad hoc", POPL 1989), in the shape Jones gives it for
qualified types ("A theory of qualified types", ESOP 1992).

Three pieces, and they are separate on purpose:

* **Where the evidence comes from** is decided *after* solving, by `Elaborator`.
  It has to be: `Semigroup a` at a use site is only resolvable once the solver
  has decided what `a` is, and the whole point of HM(X) is that generation does
  not know. So generation leaves a `Use` behind at each site, solving fills in
  the predicates and the scopes that were open there, and this module turns
  each one into an `Evidence` tree.
* **What abstracts over evidence** is decided by the solver, at the one place
  it already decides what a scheme retains. A binding that retains *n* class
  predicates takes *n* leading dictionaries, and the `Abstraction` beside its
  `CLet` records their runtime names. Nothing else in the pipeline has to agree
  about an order, because the scheme's own predicate list is the order.
* **What a dictionary is** is settled downstream: `turkey/lower.py` turns
  each `Evidence` into an ordinary term over a record type, and
  `turkey/coretc.py` checks it (delta 49). This module decides *which*
  instance covers a predicate; it no longer decides what that costs at
  run time, because a dictionary is now just a value.

The predicates a binding retains are shared across its whole group rather than
computed per name. For the single-name group that is every real case the two
agree; for a mutually recursive group they must, since one member's body may
call another and would otherwise need a dictionary its own signature never
promised. Haskell 98 shares a group's context for exactly this reason.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from . import ast
from .classes import ClassTable, InstInfo, MethodInfo, match
from .decls import substitute
from .errors import Span, TypeError_
from .types import Pred, Scheme, TBottom, Type, show_pred

_counter = itertools.count()


def dict_name(hint: str) -> str:
    """A runtime name for one dictionary parameter.

    `%` cannot start an identifier, so a generated name can never be shadowed
    by, or shadow, one the program wrote.
    """
    return f"%d{next(_counter)}.{hint}"


# ---------------------------------------------------------------- evidence


class Evidence:
    pass


@dataclass
class FromDict(Evidence):
    """A dictionary already in scope, reached through zero or more superclasses.

    `path` is the chain of superclass names to walk: `Semigroup a` from a
    `Monoid a` parameter is `FromDict(name, ("Semigroup",))`. A superclass
    edge is a *selection*, not a lookup, which is what makes `class Monoid a :
    Semigroup a` mean the dictionary carries its superclass rather than the
    caller passing both.
    """

    name: str
    path: tuple[str, ...] = ()


@dataclass
class FromInstance(Evidence):
    """The dictionary of an instance, given evidence for that instance's context.

    `instance Semigroup (Array a)` is a *function* from a `Semigroup a`
    dictionary to a `Semigroup (Array a)` one, and `args` is what it is applied
    to here.
    """

    inst: InstInfo
    args: list[Evidence] = field(default_factory=list)


@dataclass
class Absent(Evidence):
    """A predicate whose argument is bottom, and so has no instance to find.

    The mirror of the same rule in `Solver._class`: bottom is absorbed by
    whatever it meets, so there is no type to look up and no value that would
    ever ask for a method. Kept in step with the solver deliberately -- if one
    of the two accepts a predicate the other refuses, the disagreement surfaces
    as a crash rather than as a message.
    """

    pred: str


# -------------------------------------------------------- what solving records


@dataclass
class Scope:
    """The dictionaries a binder makes available inside its definition.

    Created empty when the solver descends into a definition and filled in when
    it comes back out, because what a binding retains is not known until its
    body has been solved. A `Use` only holds the object, so filling it late is
    enough.
    """

    givens: list[tuple[str, Pred]] = field(default_factory=list)


@dataclass
class Abstraction:
    """The leading dictionary parameters of one binding group.

    The generator hangs one of these on each declaration of the group and on
    the group's `CLet`; the solver fills it in when it generalizes.
    """

    params: list[str] = field(default_factory=list)
    preds: list[Pred] = field(default_factory=list)
    # The scheme each name in the group ended up with, filled in beside the
    # parameters and by the same loop. A *local* binding's scheme is otherwise
    # unrecoverable: it is defined into a child environment that is popped as
    # soon as the body is solved, so by the time anything lowers the tree there
    # is nowhere left to ask what `fun go()` was generalized to. The lowering
    # needs it for the same reason it needs `params` -- to know what the
    # binding abstracts over, types this time rather than dictionaries.
    schemes: dict[str, Scheme] = field(default_factory=dict)
    # For a group that is one *signature-checked* declaration: which rigid
    # constant stands for which of the signature's variables. A declared type
    # is checked against skolems (delta 38), so the body's recorded types name
    # constants where the scheme names variables. Same need, and same shape, as
    # `MethodImpl.skolems`.
    skolems: dict[int, Type] = field(default_factory=dict)


@dataclass
class Use:
    """One occurrence of a name, and the evidence it turned out to need.

    `type_args` is the other half of the same story, and is filled in beside
    the predicates by the same line of the solver. A use of a polymorphic name
    instantiates its scheme at *something*; the predicates say what evidence
    that costs, and the type arguments say what the instantiation was. Both
    were built by `instantiate_qual` and only the first was kept, because until
    there was a typed Core nothing downstream asked what a name was used at.
    """

    name: str
    span: Span | None = None
    preds: list[Pred] = field(default_factory=list)
    scopes: tuple[Scope, ...] = ()
    evidence: list[Evidence] = field(default_factory=list)
    method: MethodInfo | None = None
    # In the scheme's `quantified` order, which is the order a `CTyLam` at the
    # definition binds them in -- so the two need no separate agreement, the
    # same way `Abstraction.preds` is the order of the dictionary parameters.
    type_args: list[Type] = field(default_factory=list)


# ------------------------------------------------------- what a method becomes


@dataclass
class MethodImpl:
    """One method body, and the names its dictionaries arrive under.

    `self_name` is the dictionary the body belongs to -- the instance's own, or
    for a default the one the class is being defaulted into. It is bound when
    the dictionary is built, which is why a default method may call another
    method of its own class without anything being passed.

    `dict_params` are the method's *own* context (`foldMap[Monoid m]`), which
    is per call rather than per instance and so arrives as arguments.
    """

    decl: ast.FunDecl
    self_name: str
    dict_params: list[str] = field(default_factory=list)
    # Which rigid constant stands for which quantified variable, while this
    # body is being checked. A method body is checked against skolems (M10c) --
    # the instance's variables *and* the method's own -- so every type recorded
    # inside it names constants, while the type the class declares names
    # variables. They are the same types, and this is the only record of which
    # constant is which variable. A lowering that ignored it would put `l`
    # where the dictionary's binder belongs.
    skolems: dict[int, Type] = field(default_factory=dict)


@dataclass
class InstancePlan:
    """Everything the evaluator needs to build one instance's dictionary."""

    params: list[str] = field(default_factory=list)  # the instance context
    methods: dict[str, MethodImpl] = field(default_factory=dict)
    supers: dict[str, Evidence] = field(default_factory=dict)


# ------------------------------------------------------------- the elaborator


class Elaborator:
    """Turns every recorded `Use` into evidence, and completes each instance.

    Resolution is `entail` again, but constructive: where M5's version returns
    a boolean, this one returns the derivation that produced it. Assumptions
    first, then the instance table -- the same order, and for the same reason.
    An assumption is evidence the caller supplied, so preferring it is what
    keeps a polymorphic function from silently committing to one instance.
    """

    def __init__(self, classes: ClassTable) -> None:
        self.classes = classes

    def run(self, uses: list[Use]) -> None:
        self.complete_instances()
        for use in uses:
            use.method = self.classes.method(use.name)
            use.evidence = [
                self.resolve(p, use.scopes, use.span) for p in use.preds
            ]

    def complete_instances(self) -> None:
        """Fill in each instance's superclass dictionaries and default methods.

        A default body is elaborated once, by the generator, and shared by every
        instance that does not override it -- the class's own dictionary name is
        rebound per instance when the dictionary is built, which is the whole of
        what makes one body serve them all.
        """
        for insts in self.classes.instances.values():
            for inst in insts:
                info = self.classes.classes[inst.cls]
                plan = inst.plan
                scopes = (Scope(list(zip(plan.params, inst.context))),)
                for sup in info.supers:
                    plan.supers[sup.name] = self.resolve(
                        Pred(sup.name, [inst.head]), scopes, inst.decl.span
                    )
                for name, method in info.methods.items():
                    if name not in plan.methods:
                        plan.methods[name] = info.defaults[name]

    def resolve(
        self, pred: Pred, scopes: tuple[Scope, ...], span: Span | None
    ) -> Evidence:
        # Normalized, not merely pruned: `Show (Elem (Array Int))` names an
        # instance only once the family has been reduced. This is the last
        # place a family can appear -- evidence is over types, and a family is
        # not one until it is.
        target: Type = self.classes.normalize(pred.args[0])
        if isinstance(target, TBottom):
            return Absent(pred.name)
        pred = Pred(pred.name, [target])
        key = pred.key()

        for scope in reversed(scopes):
            for name, given in scope.givens:
                path = self.super_path(given, key)
                if path is not None:
                    return FromDict(name, path)

        for inst in self.classes.instances.get(pred.name, []):
            mapping = match(inst.head, target)
            if mapping is None:
                continue
            return FromInstance(inst, [
                self.resolve(Pred(q.name, [substitute(q.args[0], mapping)]),
                             scopes, span)
                for q in inst.context
            ])

        # Solving proved this predicate; failing to reconstruct *why* would be
        # a bug in one of the two, so say so rather than reporting it as the
        # program's fault.
        raise TypeError_(
            f"internal error: no evidence for '{show_pred(pred)}', which "
            f"solving had already accepted",
            span,
        )

    def super_path(
        self, given: Pred, key: tuple, path: tuple[str, ...] = ()
    ) -> tuple[str, ...] | None:
        """How to reach `key` from `given` by selecting superclasses."""
        if given.key() == key:
            return path
        info = self.classes.classes.get(given.name)
        for sup in info.supers if info else []:
            found = self.super_path(
                Pred(sup.name, list(given.args)), key, path + (sup.name,)
            )
            if found is not None:
                return found
        return None
