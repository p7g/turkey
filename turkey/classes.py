"""Single-parameter type classes: declaration, the instance table, entailment.

The shape is Jones's, from "Typing Haskell in Haskell" (Haskell Workshop 1999):
`by_super` walks a predicate's superclass closure, `by_inst` matches it against
the instance table and returns that instance's own obligations, and `entail`
puts the two together. `Solver` calls into it for the fourth constraint form of
the domain X, exactly as it calls `_has_field` and `_one_of` for the other two.

Three restrictions hold it up, and each buys something:

* **One parameter.** With a single class variable, ambiguity is the plain
  free-variable test -- a quantified variable that a predicate mentions and the
  type does not is unresolvable, full stop. Multi-parameter classes need
  functional dependencies before that test says anything useful, and an
  associated type family makes the second parameter unnecessary: `Elem c` is
  the element type of a container, computed rather than quantified.
* **An instance head is a constructor applied to distinct type variables**
  (Haskell 98's rule). Matching is then a one-way structural walk that cannot
  fail to terminate, and two instances of a class overlap exactly when they
  name the same constructor -- so the check is a dictionary lookup rather than
  a unification test.
* **A superclass constrains the class variable itself.** `class Monoid a :
  Semigroup a` may not say `Semigroup (Array a)`, so `by_super` substitutes by
  simply carrying the predicate's argument across.

**Skolems are nullary constructors.** Checking an instance method against the
class's signature requires the method to be polymorphic in everything the
instance does not fix, and a fresh unification variable would happily be
narrowed by a body that is *less* general than it claims. So the quantified
variables are replaced by rigid nullary `TCon`s named after them -- `unify`
already treats a `TCon` as rigid and compares by name, so this needs no new
machinery, and the name means an error message about one reads as though it
were still the type variable the author wrote.

**A family is `by_inst` for types.** `reduce_fam` matches a family application
against the same instance table, by the same one-way match, and returns what
that instance said the family is -- a family is a function defined by cases on
the instance head, so there is nothing else it could be. Failing to match is
*stuck*, never false: the class predicate that travels beside every family
application is what reports a missing instance, with the better message.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ast
from .decls import DeclTable, FamilyInfo, substitute
from .errors import Span, TypeError_
from .types import (
    EQUALS, KVar, Kind, Pred, Scheme, TApp, TCon, TFam, TFun, TTuple, TVar,
    Type, default_kind, generalize, kind_of, prune, show, show_kind, show_pred,
    spine, subterms, type_key, unify_kinds,
)


@dataclass
class MethodInfo:
    """One method of one class, as a scheme any use site can instantiate.

    The scheme's first predicate is the class's own -- `combine` is
    `[Semigroup a] fun(a, a) -> a` -- which is what makes a method call demand
    an instance without the caller doing anything special.
    """

    name: str
    cls: str
    scheme: Scheme
    decl: ast.FunDecl
    class_var: TVar
    names: dict[int, str]  # quantified variable id -> the name it was written as

    @property
    def has_default(self) -> bool:
        return self.decl.body is not None


@dataclass
class ClassInfo:
    name: str
    param: str
    var: TVar  # the class variable, shared by every method's scheme
    supers: list[Pred] = field(default_factory=list)
    methods: dict[str, MethodInfo] = field(default_factory=dict)
    span: Span | None = None
    module: str = ""  # which module declared it, for the orphan rule
    # Family name -> the parameter it was written over. The types themselves
    # live in `DeclTable.families`, because a family is a name in type
    # position and one table decides what those mean.
    families: dict[str, str] = field(default_factory=dict)
    # Method name -> `evidence.MethodImpl` for its default body, elaborated
    # once and shared by every instance that does not override it. Filled in by
    # the generator, which is where method bodies are checked.
    defaults: dict = field(default_factory=dict)

    @property
    def kind(self) -> Kind:
        return kind_of(self.var)


@dataclass
class InstInfo:
    cls: str
    head: Type
    context: list[Pred]
    decl: ast.InstanceDecl
    names: dict[int, str]
    # Family name -> what it is at this head, over the head's own variables.
    families: dict[str, Type] = field(default_factory=dict)
    # `evidence.InstancePlan`: what the evaluator needs to build this
    # dictionary. Set by the generator once the method bodies are checked.
    plan: object = None
    # The module that declared it, for the orphan rule (delta 43).
    module: str = ""

    @property
    def con(self) -> str:
        head, _ = spine(self.head)
        if isinstance(head, TCon):
            return head.name
        if isinstance(head, TTuple):
            return f"Tuple{len(head.elems)}"
        raise AssertionError("validated instance head has no stable key")

    @property
    def head_key(self) -> tuple[str, object]:
        head, _ = spine(self.head)
        if isinstance(head, TCon):
            return ("con", head.name)
        assert isinstance(head, TTuple)
        return ("tuple", len(head.elems))


class ClassTable:
    def __init__(self, decls: DeclTable) -> None:
        # Set by `register_all` for the module it is registering.
        self.module = ""
        self.decls = decls
        self.classes: dict[str, ClassInfo] = {}
        self.instances: dict[str, list[InstInfo]] = {}
        # Method name -> owning class. Methods share the value namespace with
        # ordinary functions, so this is also what detects a collision.
        self.owner: dict[str, str] = {}

    # -- registration ------------------------------------------------------

    def register_all(
        self, classes: list[ast.ClassDecl], instances: list[ast.InstanceDecl],
        module: str = "",
    ) -> None:
        """Declare every class before resolving any signature.

        A method's context may name a class declared further down the file --
        `foldMap[Monoid m]` above `class Monoid` -- and a superclass may too, so
        names come first and bodies second. Kinds ride along: the class variable
        starts as an undecided kind and every signature that applies it narrows
        it, which is how `Foldable t` discovers `t :: * -> *`.
        """
        for d in classes:
            if d.name in self.classes:
                raise TypeError_(f"class '{d.name}' is declared more than once", d.span)
            self.classes[d.name] = ClassInfo(d.name, d.param, TVar(1), span=d.span,
                                             module=module)
            self._declare_families(d)

        for d in classes:
            self._resolve_class(d)
        for d in classes:
            self._check_super_acyclic(d.name, ())
        for fam in self.decls.families.values():
            default_kind(fam.res_kind)
        for info in self.classes.values():
            default_kind(info.var.kind)
            for method in info.methods.values():
                for var in method.scheme.quantified:
                    default_kind(var.kind)

        registered = [self._resolve_instance(d, module) for d in instances]
        # Coverage is checked only once every instance is in the table, since
        # `instance Monoid (Array a)` is entitled to be satisfied by a
        # `Semigroup (Array a)` written further down the file.
        for inst in registered:
            self._check_instance(inst)

    def _declare_families(self, d: ast.ClassDecl) -> None:
        """Register a class's families before any signature is read.

        They go in `DeclTable`, because that is what `to_type` consults, and a
        family shares the type namespace with constructors and aliases -- `Elem`
        cannot also be a data type. The argument kind is the class variable's
        own kind cell, so a family applied in a signature constrains the class
        exactly as a method's use of the parameter does; the result kind starts
        undecided and is settled by use.
        """
        info = self.classes[d.name]
        for fam in d.families:
            if fam.param != d.param:
                raise TypeError_(
                    f"'{fam.name}' must be a family over '{d.param}', the "
                    f"parameter of class '{d.name}': a family is determined by "
                    f"the class variable and there is nothing else in scope",
                    fam.span,
                )
            if fam.name in self.decls.families:
                other = self.decls.families[fam.name].cls
                raise TypeError_(
                    f"'{fam.name}' is already a type family of class '{other}'",
                    fam.span,
                )
            if fam.name in self.decls.tycons:
                raise TypeError_(
                    f"'{fam.name}' is already a type; a type family cannot "
                    f"share its name",
                    fam.span,
                )
            self.decls.families[fam.name] = FamilyInfo(
                fam.name, d.name, info.var.kind, KVar(), fam.span
            )
            info.families[fam.name] = fam.param

    def _check_super_acyclic(self, name: str, seen: tuple[str, ...]) -> None:
        if name in seen:
            raise TypeError_(
                f"class '{name}' is its own superclass",
                self.classes[name].span,
            )
        for sup in self.classes[name].supers:
            self._check_super_acyclic(sup.name, seen + (name,))

    def _resolve_class(self, d: ast.ClassDecl) -> None:
        info = self.classes[d.name]
        for sup in d.supers:
            if sup.name not in self.classes:
                raise TypeError_(f"unknown class '{sup.name}'", sup.span)
            if not (isinstance(sup.arg, ast.TEVar) and sup.arg.name == d.param):
                raise TypeError_(
                    f"the superclasses of '{d.name}' must constrain its own "
                    f"parameter '{d.param}'",
                    sup.span,
                )
            if not unify_kinds(kind_of(self.classes[sup.name].var), info.var.kind):
                raise TypeError_(
                    f"'{d.param}' cannot be both a '{sup.name}' and a "
                    f"'{d.name}': the two disagree about its kind",
                    sup.span,
                )
            info.supers.append(Pred(sup.name, [info.var]))

        for decl in d.methods:
            if decl.name in self.owner:
                raise TypeError_(
                    f"'{decl.name}' is already a method of class "
                    f"'{self.owner[decl.name]}'",
                    decl.span,
                )
            tyvars: dict[str, TVar] = {d.param: info.var}
            body = self._method_type(decl, tyvars)
            preds = [Pred(d.name, [info.var])] + self.resolve_context(
                decl.context, tyvars
            )
            scheme = generalize(body, 0, preds)
            names = {var.id: name for name, var in tyvars.items()}
            info.methods[decl.name] = MethodInfo(
                decl.name, d.name, scheme, decl, info.var, names
            )
            self.owner[decl.name] = d.name

    def _method_type(self, decl: ast.FunDecl, tyvars: dict[str, TVar]) -> TFun:
        """A method's declared type, read off its annotations.

        A signature and a defaulted method are read the same way, because a
        signature's parameters are stored as anonymous annotated binders. Both
        must be fully annotated: a class fixes its methods' types, so there is
        nothing here for inference to discover.
        """
        fresh = lambda: TVar(1)  # noqa: E731 -- one expression, used twice below
        params: list[Type] = []
        for p in decl.params:
            if not isinstance(p, ast.PAnnot):
                raise TypeError_(
                    f"parameter of method '{decl.name}' needs a type: a class "
                    f"fixes its methods' types, so none is inferred",
                    p.span,
                )
            params.append(self.decls.star(p.type_expr, tyvars, fresh))
        if decl.ret is None:
            raise TypeError_(
                f"method '{decl.name}' must state a return type", decl.span
            )
        return TFun(params, self.decls.star(decl.ret, tyvars, fresh))

    def resolve_context(
        self,
        context: list[ast.ClassPred | ast.EqPred],
        tyvars: dict[str, Type],
        fresh=None,
    ) -> list[Pred]:
        """Translate a written `[C a, D b]` into predicates over `tyvars`.

        `tyvars` is the enclosing declaration's annotation scope, so the `a` in
        `[Ord a]` and the `a` in `xs : Array a` are the same variable
        (SPEC-DELTAS.md 13) -- a context constrains, it does not bind.
        """
        if fresh is None:
            fresh = lambda: TVar(1)  # noqa: E731
        out: list[Pred] = []
        answers: dict[tuple, ast.EqPred] = {}
        for pred in context:
            if isinstance(pred, ast.EqPred):
                equality = self._resolve_equality(pred, tyvars, fresh)
                # A family is a function of its argument, so a context may give
                # it one answer. Two are caught here rather than by `improve`,
                # which only sees the *deferred* queue: a written equality
                # becomes an assumption and never joins it.
                key = type_key(equality.args[0])
                if key in answers:
                    raise TypeError_(
                        f"'{show(equality.args[0])}' is already required to be "
                        f"'{show(answers[key].args[1])}' here, and a family "
                        f"has one answer for one argument",
                        pred.span,
                    )
                answers[key] = equality
                out.append(equality)
                continue
            info = self.classes.get(pred.name)
            if info is None:
                raise TypeError_(f"unknown class '{pred.name}'", pred.span)
            arg = self.decls.to_type(pred.arg, tyvars, fresh)
            if not unify_kinds(kind_of(arg), kind_of(info.var)):
                raise TypeError_(
                    f"'{show(arg)}' has kind {show_kind(kind_of(arg))}, but "
                    f"'{pred.name}' constrains a type of kind "
                    f"{show_kind(kind_of(info.var))}",
                    pred.span,
                )
            out.append(Pred(pred.name, [arg]))
        return out

    def _resolve_equality(self, pred: ast.EqPred, tyvars, fresh) -> Pred:
        """Translate a written `Item c ~ Op` (delta 39).

        The left side must be a family application, and that restriction is
        what keeps the rule sound as a *given*. A given equality is used by
        rewriting -- `Item c` has to actually become `Op` for a `match` on the
        element to find its constructors -- so each one is read as a reduction
        rule for the family it names. Requiring the family on the left gives
        every rule a single, syntactically evident left-hand side; requiring
        the right side not to mention it keeps rewriting terminating.

        A general equality between two arbitrary types would be neither. It is
        also not needed: `Int ~ a` says nothing a plain annotation cannot.
        """
        left = self.decls.to_type(pred.left, tyvars, fresh)
        right = self.decls.to_type(pred.right, tyvars, fresh)
        if not isinstance(left, TFam):
            raise TypeError_(
                f"'{show(left)}' is not a type family, and the left side of "
                f"a '~' must be one -- an equality says what a family "
                f"answers, not that two types happen to agree",
                pred.span,
            )
        if any(type_key(t) == type_key(left) for t in subterms(right)):
            raise TypeError_(
                f"'{show(right)}' mentions '{show(left)}', so the equality "
                f"defines it in terms of itself",
                pred.span,
            )
        if not unify_kinds(kind_of(left), kind_of(right)):
            raise TypeError_(
                f"'{show(left)}' has kind {show_kind(kind_of(left))}, but "
                f"'{show(right)}' has kind {show_kind(kind_of(right))}",
                pred.span,
            )
        return Pred(EQUALS, [left, right])

    def _resolve_instance(self, d: ast.InstanceDecl, module: str = "") -> InstInfo:
        info = self.classes.get(d.cls)
        if info is None:
            raise TypeError_(f"unknown class '{d.cls}'", d.span)
        tyvars: dict[str, TVar] = {}
        fresh = lambda: TVar(1)  # noqa: E731
        head = self.decls.to_type(d.head, tyvars, fresh)
        if not unify_kinds(kind_of(head), kind_of(info.var)):
            raise TypeError_(
                f"'{show(head)}' has kind {show_kind(kind_of(head))}, but "
                f"'{d.cls}' constrains a type of kind "
                f"{show_kind(kind_of(info.var))}",
                d.span,
            )
        self._check_head_shape(d, head)

        key = _instance_head_key(head)
        for other in self.instances.get(d.cls, []):
            if other.head_key == key:
                raise TypeError_(
                    f"overlapping instances: '{d.cls} {show(head)}' and "
                    f"'{other.cls} {show(other.head)}' both apply",
                    d.span,
                )
        context = self.resolve_context(d.context, tyvars)
        families = self._resolve_families(d, info, head, tyvars, fresh)
        for var in tyvars.values():
            default_kind(var.kind)
        names = {var.id: name for name, var in tyvars.items()}
        inst = InstInfo(d.cls, head, context, d, names, families, module=module)
        self._check_orphan(inst, d)
        self.instances.setdefault(d.cls, []).append(inst)
        return inst

    def _check_orphan(self, inst: InstInfo, d: ast.InstanceDecl) -> None:
        """An instance belongs to its class's module or its head's (delta 43).

        Instances are global -- every module sees every one, because a
        predicate has to mean the same thing wherever it is solved. Global and
        *unrestricted* is what would make coherence a matter of luck: two
        libraries could each write `instance Show Point` over someone else's
        `Show` and someone else's `Point`, and whichever loaded second would be
        the error, in a file neither author wrote. The orphan rule makes the
        overlap check above a local obligation instead: an instance can only
        clash with one its author was in a position to see.
        """
        home = self.classes[inst.cls].module
        head_module = _module_of(inst.con)
        # A public facade owns types declared in its private child module
        # (`Data.Bool.Type` -> `Data.Bool`) as well as types declared directly.
        head_facade = head_module.rpartition(".")[0]
        builtin_home = {
            "Int": "Data.Int", "Float": "Data.Float",
            "String": "Data.String", "Char": "Data.Char",
        }.get(inst.con)
        tuple_home = "Data.Tuple" if inst.con.startswith("Tuple") else None
        if inst.module in (
            home, head_module, head_facade, builtin_home, tuple_home,
        ):
            return
        raise TypeError_(
            f"orphan instance: '{d.cls} {show(inst.head)}' is declared in "
            f"'{inst.module}', but '{d.cls}' belongs to "
            f"'{home or 'the library'}' and '{show(inst.head)}' to "
            f"'{head_module or 'the language itself'}'. An instance must live "
            f"with its class or with its type, so that no two modules can each "
            f"claim it.",
            d.span,
        )

    def _resolve_families(
        self, d: ast.InstanceDecl, info: ClassInfo, head: Type,
        tyvars: dict[str, TVar], fresh
    ) -> dict[str, Type]:
        """`type Elem = a`: what each of the class's families is, at this head.

        The right-hand side is read in the *head's* scope, so its variables are
        the head's and nothing else may appear -- an unbound one would be a
        type the instance never fixed, which is a family with no definition
        rather than a polymorphic one.
        """
        bound: dict[str, Type] = {}
        for fb in d.families:
            resolved = _member_named(info.families, fb.name)
            if resolved is None:
                raise TypeError_(
                    f"'{fb.name}' is not a type family of class '{d.cls}'",
                    fb.span,
                )
            fb.name = resolved
            if resolved in bound:
                raise TypeError_(
                    f"'{fb.name}' is defined twice in this instance", fb.span
                )
            known = set(tyvars)
            body = self.decls.to_type(fb.body, tyvars, fresh)
            escaped = sorted(set(tyvars) - known)
            if escaped:
                raise TypeError_(
                    f"'{escaped[0]}' is not bound by the instance head "
                    f"'{show(head)}'",
                    fb.span,
                )
            fam = self.decls.families[resolved]
            if not unify_kinds(kind_of(body), fam.res_kind):
                raise TypeError_(
                    f"'{show(body)}' has kind {show_kind(kind_of(body))}, but "
                    f"'{fb.name}' produces a type of kind "
                    f"{show_kind(fam.res_kind)}",
                    fb.span,
                )
            _check_fam_decreasing(fb, body)
            bound[resolved] = body
        missing = [n for n in info.families if n not in bound]
        if missing:
            raise TypeError_(
                f"instance '{d.cls} {show(head)}' does not define "
                f"{', '.join(sorted(_surface_member(n) for n in missing))}",
                d.span,
            )
        return bound

    # -- reduction ---------------------------------------------------------

    def reduce_fam(self, t: TFam) -> Type | None:
        """`t`'s definition, or None if no instance decides it yet.

        This is `by_inst` for types instead of predicates, and it is the same
        one-way match against the same instance table -- a family is a function
        defined by cases on the instance head, so there is nothing else it
        could be. None is *stuck*, never failure: an argument no instance
        covers is a missing instance, and the class predicate that travels
        beside every family application is what reports it, with the better
        message and the right span.
        """
        arg = self.normalize(t.arg)
        for inst in self.instances.get(self.decls.families[t.name].cls, []):
            mapping = match(inst.head, arg)
            if mapping is not None:
                return substitute(inst.families[t.name], mapping)
        return None

    def normalize(self, t: Type) -> Type:
        """Reduce family applications at the head of `t` until one sticks."""
        t = prune(t)
        while isinstance(t, TFam):
            reduced = self.reduce_fam(t)
            if reduced is None:
                return t
            t = prune(reduced)
        return t

    def uncovered(self, t: TFam) -> Pred | None:
        """The instance a stuck `t` is missing, if its argument is rigid enough.

        A family over a variable is waiting; one over a constructor that no
        instance names will wait forever, and saying so where the equation was
        written beats a stranded-predicate report at the end of the group.
        """
        arg = self.normalize(t.arg)
        head, _ = spine(arg)
        if not isinstance(head, TCon):
            return None
        cls = self.decls.families[t.name].cls
        pred = Pred(cls, [arg])
        return None if self.by_inst(pred) is not None else pred

    @staticmethod
    def _check_head_shape(d: ast.InstanceDecl, head: Type) -> None:
        """Haskell 98: a constructor applied to distinct type variables.

        `instance Functor (Either Int)` is rejected here, not because it could
        not be given a meaning, but because allowing it makes instance
        selection depend on how much of a type is known yet -- and that is what
        turns a missing instance into a silently different one.
        """
        con, args = spine(head)
        if isinstance(con, TTuple):
            args = list(con.elems)
        elif not isinstance(con, TCon):
            raise TypeError_(
                f"'{show(head)}' cannot be an instance head: it must be a type "
                f"constructor applied to distinct type variables",
                d.head.span,
            )
        seen: set[int] = set()
        for arg in args:
            arg = prune(arg)
            if not isinstance(arg, TVar) or arg.id in seen:
                raise TypeError_(
                    f"'{show(head)}' cannot be an instance head: '{d.cls}' must "
                    f"be given a type constructor applied to distinct type "
                    f"variables",
                    d.head.span,
                )
            seen.add(arg.id)

    def _check_instance(self, inst: InstInfo) -> None:
        """Superclass coverage, and the method roster."""
        info = self.classes[inst.cls]
        for sup in info.supers:
            demand = Pred(sup.name, [inst.head])
            if not self.entail(inst.context, demand):
                raise TypeError_(
                    f"'{show_pred(demand)}' is required by '{inst.cls}', but "
                    f"there is no such instance",
                    inst.decl.span,
                )
        defined: set[str] = set()
        for method in inst.decl.methods:
            resolved = _member_named(info.methods, method.name)
            if resolved is None:
                raise TypeError_(
                    f"'{method.name}' is not a method of class '{inst.cls}'",
                    method.span,
                )
            method.name = resolved
            if resolved in defined:
                raise TypeError_(
                    f"'{method.name}' is defined twice in this instance",
                    method.span,
                )
            if method.body is None:
                raise TypeError_(
                    f"'{method.name}' needs a body: an instance method's type "
                    f"comes from the class, so it does not restate it",
                    method.span,
                )
            defined.add(resolved)
        missing = [
            name for name, m in info.methods.items()
            if name not in defined and not m.has_default
        ]
        if missing:
            raise TypeError_(
                f"instance '{inst.cls} {show(inst.head)}' does not define "
                f"{', '.join(sorted(_surface_member(n) for n in missing))}",
                inst.decl.span,
            )

    # -- entailment --------------------------------------------------------

    def by_super(self, p: Pred) -> list[Pred]:
        """`p` together with everything a class's superclasses imply of it.

        A superclass constrains the class variable itself, so the substitution
        is just carrying `p`'s argument across.
        """
        out = [p]
        info = self.classes.get(p.name)
        for sup in info.supers if info else []:
            out.extend(self.by_super(Pred(sup.name, list(p.args))))
        return out

    def by_inst(self, p: Pred) -> list[Pred] | None:
        """The obligations of the instance that covers `p`, or None if none does."""
        for inst in self.instances.get(p.name, []):
            mapping = match(inst.head, p.args[0])
            if mapping is not None:
                return [Pred(q.name, [substitute(q.args[0], mapping)])
                        for q in inst.context]
        return None

    def entail(self, given: list[Pred], p: Pred) -> bool:
        """Does `given` prove `p`? Superclasses first, then the instance table."""
        key = p.key()
        if any(q.key() == key for a in given for q in self.by_super(a)):
            return True
        qs = self.by_inst(p)
        return qs is not None and all(self.entail(given, q) for q in qs)

    def is_class(self, name: str) -> bool:
        return name in self.classes

    def method(self, name: str) -> MethodInfo | None:
        """The method `name` denotes, if it denotes one rather than a function."""
        cls = self.owner.get(name)
        return self.classes[cls].methods[name] if cls is not None else None

    def simplify(self, preds: list[Pred]) -> list[Pred]:
        """Drop a class predicate that the others already imply.

        `[Ord a, Eq a]` is `[Ord a]` when `Eq` is a superclass of `Ord`. This is
        cosmetic for soundness and load-bearing for legibility: an inferred
        context that restates its own superclasses is noise.
        """
        out: list[Pred] = []
        equalities: set[frozenset[tuple]] = set()
        for i, p in enumerate(preds):
            if not self.is_class(p.name):
                if p.name == EQUALS and len(p.args) == 2:
                    # Equality is symmetric.  Inference can encounter both
                    # directions when two associated families meet through a
                    # mutable local (`Item c ~ IndexItem c` and its reverse).
                    # Carrying both into a scheme creates a cyclic rewrite
                    # system for Core checking, although they state one fact.
                    key = frozenset(type_key(a) for a in p.args)
                    if key in equalities:
                        continue
                    equalities.add(key)
                out.append(p)
                continue
            implied = False
            for j, q in enumerate(preds):
                if i == j or not self.is_class(q.name):
                    continue
                if q.key() == p.key():
                    # A duplicate implies itself; keep the first of the pair.
                    if j < i:
                        implied = True
                        break
                    continue
                if any(r.key() == p.key() for r in self.by_super(q)[1:]):
                    implied = True
                    break
            if not implied:
                out.append(p)
        return out


def _check_fam_decreasing(fb: ast.FamBind, body: Type) -> None:
    """A family's definition may only apply a family to a *variable*.

    That is what makes reduction terminate. An instance head is a constructor
    over distinct variables, so a family applied to one of them is applied to a
    proper subterm of the argument that selected this instance, and every step
    is a strict decrease. `type Elem = Elem (Array a)` is the rule's whole
    point: it would reduce forever.
    """
    def walk(t: Type) -> None:
        t = prune(t)
        if isinstance(t, TFam):
            if not isinstance(prune(t.arg), TVar):
                raise TypeError_(
                    f"'{show(t)}' cannot appear here: a type family definition "
                    f"may apply a family only to a type variable of the "
                    f"instance head, so that reduction terminates",
                    fb.span,
                )
            walk(t.arg)
        elif isinstance(t, TApp):
            walk(t.fn)
            walk(t.arg)
        elif isinstance(t, TFun):
            for p in t.params:
                walk(p)
            walk(t.ret)
        elif isinstance(t, TTuple):
            for e in t.elems:
                walk(e)

    walk(body)


def _module_of(name: str) -> str:
    """Which module an internal name came from; empty for a built-in type."""
    module, sep, _short = name.rpartition("#")
    return module if sep else ""


def _instance_head_key(t: Type) -> tuple[str, object]:
    head, _ = spine(t)
    if isinstance(head, TCon):
        return ("con", head.name)
    if isinstance(head, TTuple):
        return ("tuple", len(head.elems))
    raise AssertionError("instance head was not shape-checked")


def _member_named(members: dict[str, object], written: str) -> str | None:
    """Resolve an instance-body member by its surface short name.

    Instance bodies declare `fun show`, not a qualified binding.  The selected
    class makes that spelling unambiguous even when another imported class has
    a method with the same short name.
    """
    if written in members:
        return written
    found = [name for name in members if _surface_member(name) == written]
    return found[0] if len(found) == 1 else None


def _surface_member(name: str) -> str:
    return name.rpartition(".")[2].rpartition("#")[2] or name


def match(pattern: Type, target: Type) -> dict[int, Type] | None:
    """One-way matching: bind `pattern`'s variables so it becomes `target`.

    Not unification. An instance head is a *pattern*, and letting it bind
    variables in the type being classified would let instance selection invent
    facts about the program rather than discover them.
    """
    mapping: dict[int, Type] = {}

    def go(p: Type, t: Type) -> bool:
        p, t = prune(p), prune(t)
        if isinstance(p, TVar):
            bound = mapping.get(p.id)
            if bound is None:
                mapping[p.id] = t
                return True
            return type_key(bound) == type_key(t)
        if isinstance(p, TCon):
            return isinstance(t, TCon) and p.name == t.name
        if isinstance(p, TApp):
            return isinstance(t, TApp) and go(p.fn, t.fn) and go(p.arg, t.arg)
        if isinstance(p, TFun):
            return (isinstance(t, TFun) and len(p.params) == len(t.params)
                    and all(go(a, b) for a, b in zip(p.params, t.params))
                    and go(p.ret, t.ret))
        if isinstance(p, TTuple):
            return (isinstance(t, TTuple) and len(p.elems) == len(t.elems)
                    and all(go(a, b) for a, b in zip(p.elems, t.elems)))
        return False

    return mapping if go(pattern, target) else None


class Skolems:
    """Turns a scheme's quantified variables into rigid nullary constructors.

    One instance per checking scope, so that a method variable and an instance
    variable that happen to share a name still get distinct constants.
    """

    def __init__(self) -> None:
        self.used: set[str] = set()
        self.mapping: dict[int, Type] = {}
        #: Every constant made here, for the solver to stamp with the rank of
        #: the binder they belong to. Generation cannot do it: ranks are the
        #: solver's, and it is the one that knows how deep this body sits.
        self.made: list[TCon] = []

    def bind(self, var: TVar, name: str) -> Type:
        candidate, n = name, 1
        while candidate in self.used:
            n += 1
            candidate = f"{name}{n}"
        self.used.add(candidate)
        con = TCon(candidate, kind_of(var))
        self.mapping[var.id] = con
        self.made.append(con)
        return con

    def apply(self, t: Type) -> Type:
        return substitute(t, self.mapping)

    def apply_pred(self, p: Pred) -> Pred:
        return Pred(p.name, [self.apply(a) for a in p.args])
