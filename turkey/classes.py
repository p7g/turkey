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
  functional dependencies before that test says anything useful, and M7's
  associated type families are meant to make the second parameter unnecessary.
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
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import ast
from .decls import DeclTable, substitute
from .errors import Span, TypeError_
from .types import (
    Kind, Pred, Scheme, TApp, TCon, TFun, TTuple, TVar, Type, default_kind,
    generalize, kind_of, prune, show, show_kind, show_pred, spine, type_key,
    unify_kinds,
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
    # `evidence.InstancePlan`: what the evaluator needs to build this
    # dictionary. Set by the generator once the method bodies are checked.
    plan: object = None

    @property
    def con(self) -> str:
        head, _ = spine(self.head)
        assert isinstance(head, TCon)
        return head.name


class ClassTable:
    def __init__(self, decls: DeclTable) -> None:
        self.decls = decls
        self.classes: dict[str, ClassInfo] = {}
        self.instances: dict[str, list[InstInfo]] = {}
        # Method name -> owning class. Methods share the value namespace with
        # ordinary functions, so this is also what detects a collision.
        self.owner: dict[str, str] = {}

    # -- registration ------------------------------------------------------

    def register_all(
        self, classes: list[ast.ClassDecl], instances: list[ast.InstanceDecl]
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
            if d.name in self.decls.tycons:
                raise TypeError_(
                    f"'{d.name}' is already a type; a class cannot share its name",
                    d.span,
                )
            self.classes[d.name] = ClassInfo(d.name, d.param, TVar(1), span=d.span)

        for d in classes:
            self._resolve_class(d)
        for d in classes:
            self._check_super_acyclic(d.name, ())
        for info in self.classes.values():
            default_kind(info.var.kind)
            for method in info.methods.values():
                for var in method.scheme.quantified:
                    default_kind(var.kind)

        registered = [self._resolve_instance(d) for d in instances]
        # Coverage is checked only once every instance is in the table, since
        # `instance Monoid (Array a)` is entitled to be satisfied by a
        # `Semigroup (Array a)` written further down the file.
        for inst in registered:
            self._check_instance(inst)

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
        context: list[ast.ClassPred],
        tyvars: dict[str, TVar],
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
        for pred in context:
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

    def _resolve_instance(self, d: ast.InstanceDecl) -> InstInfo:
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

        for other in self.instances.get(d.cls, []):
            if other.con == _con_of(head).name:
                raise TypeError_(
                    f"overlapping instances: '{d.cls} {show(head)}' and "
                    f"'{other.cls} {show(other.head)}' both apply",
                    d.span,
                )
        context = self.resolve_context(d.context, tyvars)
        for var in tyvars.values():
            default_kind(var.kind)
        names = {var.id: name for name, var in tyvars.items()}
        inst = InstInfo(d.cls, head, context, d, names)
        self.instances.setdefault(d.cls, []).append(inst)
        return inst

    @staticmethod
    def _check_head_shape(d: ast.InstanceDecl, head: Type) -> None:
        """Haskell 98: a constructor applied to distinct type variables.

        `instance Functor (Either Int)` is rejected here, not because it could
        not be given a meaning, but because allowing it makes instance
        selection depend on how much of a type is known yet -- and that is what
        turns a missing instance into a silently different one.
        """
        con, args = spine(head)
        if not isinstance(con, TCon):
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
            if method.name not in info.methods:
                raise TypeError_(
                    f"'{method.name}' is not a method of class '{inst.cls}'",
                    method.span,
                )
            if method.name in defined:
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
            defined.add(method.name)
        missing = [
            name for name, m in info.methods.items()
            if name not in defined and not m.has_default
        ]
        if missing:
            raise TypeError_(
                f"instance '{inst.cls} {show(inst.head)}' does not define "
                f"{', '.join(sorted(missing))}",
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
        for i, p in enumerate(preds):
            if not self.is_class(p.name):
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


def _con_of(t: Type) -> TCon:
    head, _ = spine(t)
    assert isinstance(head, TCon)
    return head


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

    def bind(self, var: TVar, name: str) -> Type:
        candidate, n = name, 1
        while candidate in self.used:
            n += 1
            candidate = f"{name}{n}"
        self.used.add(candidate)
        con = TCon(candidate, kind_of(var))
        self.mapping[var.id] = con
        return con

    def apply(self, t: Type) -> Type:
        return substitute(t, self.mapping)

    def apply_pred(self, p: Pred) -> Pred:
        return Pred(p.name, [self.apply(a) for a in p.args])
