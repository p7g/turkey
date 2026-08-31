"""Constraint generation for turkey-lite (design.md sections 4 and 5).

This module walks the AST and *builds* a constraint (see `turkey/constraints.py`
for the language and the solver). It decides nothing: it never unifies, never
generalizes, and never looks a name up in a type environment. That is what
makes it order-independent -- there is no partially-solved substitution for a
traversal decision to depend on, because there is no substitution here at all.

What it does keep is everything genuinely syntactic:

* **Scopes**, but only of names and their mutability. `cannot assign to 'x'` is
  a property of the binding form, not of a type, so it is settled here. The
  environment mapping names to *schemes* lives in the solver.
* **Bottom.** `return`, `break` and `continue` have type bottom, and bottom is
  produced syntactically -- never discovered by solving. So `join` can pick the
  surviving type while generating and emit a plain equation when neither side
  is bottom.
* **Annotation type variables**, scoped to the enclosing function, so the `a`
  in a signature and the `a` in a body annotation are the same variable
  (SPEC-DELTAS.md entry 13).
* **Field access** emits `HasField "f" r a` and hands back the `a`; whether the
  receiver is known yet does not matter, and need never be true at all.

What generation does *not* decide includes which instance a method call means:
each occurrence of a name is marked with a `Use` for elaboration to fill in
later (`turkey/evidence.py`), because the answer depends on what solving
decides the name's predicates are about. Marking rather than resolving is the
same discipline as everything else here.

Variables invented here have no rank: generation has no idea what binder depth
it is under, which is the point. Each is recorded in the enclosing frame and
becomes part of a `CExists`, and the solver stamps the rank when it gets there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from . import ast, prelude
from .classes import ClassTable, MethodInfo, Skolems
from .constraints import (
    HAS_FIELD, ONE_OF, Binding, CAnd, CAssume, CDef, CEq, CExists, CInstance,
    CBind, CLet, CPred, Constraint, Env, reach,
)
from .decls import DeclTable
from .deps import free_names, pattern_vars, sccs
from .evidence import Abstraction, InstancePlan, MethodImpl, Use, dict_name
from .errors import Span, TypeError_
from .typed import TypeTable
from .types import (
    BOOL, BOTTOM, CHAR, FLOAT, INT, STRING, UNIT, Pred, Scheme, TBottom, TFun,
    TLabel, TSet, TTuple, TVar, Type, apply, array_of, float_literal_set,
    int_literal_set, show, show_pred, vars_of,
)

LITERAL_TYPES = {"Int": INT, "Float": FLOAT, "String": STRING, "Char": CHAR}

# Which literals get a set rather than a type. A string or a char has exactly
# one type and is emitted as one; only the numeric kinds are open.
NUMERIC_KINDS = frozenset({"Int", "Float"})

# What is left of section 8.2's operator table after M8 and delta 44. Every
# operator that names a value is a class method now (`turkey/prelude.py`);
# these two are not, and for a reason no class can express: `&&` and `||`
# short-circuit, which no function call does.
BINARY_OPS: dict[str, tuple[Type, Type, Type]] = {
    "&&": (BOOL, BOOL, BOOL),
    "||": (BOOL, BOOL, BOOL),
}

UNARY_OPS: dict[str, tuple[Type, Type]] = {"!": (BOOL, BOOL)}

T = TypeVar("T")


@dataclass
class LoopCtx:
    kind: str  # loop | while | for
    result: Type
    saw_break: bool = False


@dataclass
class Frame:
    """One `exists` under construction: the variables and constraints so far."""

    vars: list[TVar] = field(default_factory=list)
    parts: list[Constraint] = field(default_factory=list)


class Generator:
    def __init__(self, decls: DeclTable, builtins: Env,
                 classes: ClassTable | None = None, module: str = "",
                 types: TypeTable | None = None):
        self.decls = decls
        # Every expression's type, kept for the lowering rather than dropped
        # once the constraint is built. Shared across modules the way `decls`
        # and `env` are: one program, one table. See turkey/typed.py.
        self.types = TypeTable() if types is None else types
        # Which module is being generated, for the orphan rule (delta 43).
        self.module = module
        # The prelude's classes arrive already registered (M8); the table is
        # shared so that `instance Add Int` is visible here, while the
        # environment is not, so `Prim.intAdd` is not.
        self.classes = ClassTable(decls) if classes is None else classes
        # A recorded type is read back through the instance table, so the table
        # has to know it. Idempotent: every module shares the one `ClassTable`.
        self.types.observe(self.classes)
        # Class methods are bound here rather than by a `CLet`: a method's type
        # comes from its class, not from anything solving discovers, so it is
        # in scope from the first line of the program.
        self.env = builtins
        self.frames: list[Frame] = [Frame()]
        # Name -> mutable. Types are the solver's business; this is only what
        # generation needs to reject an undefined name or a write to a `let`.
        self.scopes: list[dict[str, bool]] = [_names_of(builtins)]
        self.fn_stack: list[Type] = []
        self.loop_stack: list[LoopCtx] = []
        self.tyvar_scopes: list[dict[str, Type]] = [{}]
        self.warnings: list[str] = []
        # Exhaustiveness runs after solving, when scrutinee types are known.
        self.match_sites: list[tuple[ast.EMatch, Type]] = []

    # -- building ----------------------------------------------------------

    def fresh(self) -> TVar:
        """A variable with no rank yet, recorded in the enclosing existential."""
        var = TVar(0)
        self.frames[-1].vars.append(var)
        return var

    def emit(self, c: Constraint) -> None:
        self.frames[-1].parts.append(c)

    def eq(self, a: Type, b: Type, span: Span | None = None, context: str = "") -> None:
        self.emit(CEq(a, b, span, context))

    def push(self) -> None:
        self.frames.append(Frame())

    def pop(self) -> Constraint:
        frame = self.frames.pop()
        return CExists(frame.vars, CAnd(frame.parts))

    def join(self, a: Type, b: Type, span: Span | None = None, context: str = "") -> Type:
        """Equate two types and return the one that survives.

        Bottom is absorbed by whatever it meets (design.md section 4.3), so a
        bare equation cannot tell the caller which type it ends up with. Every
        place two branches must agree goes through this instead. It can be
        decided during generation -- with no solving at all -- because bottom is
        produced *syntactically*, by `return`, `break` and `continue`, and
        unification never binds a variable to it. So a type is bottom here or
        never will be.
        """
        if isinstance(a, TBottom):
            return b
        if isinstance(b, TBottom):
            return a
        self.eq(a, b, span, context)
        return a

    # -- scopes ------------------------------------------------------------

    def bound(self, name: str) -> bool:
        return any(name in scope for scope in reversed(self.scopes))

    def is_mutable(self, name: str) -> bool:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return False

    def type_of(self, te: ast.TypeExpr) -> Type:
        """Translate an annotation, resolving its type variables in the
        innermost function's scope."""
        return self.decls.star(te, self.tyvar_scopes[-1], self.fresh)

    # -- program -----------------------------------------------------------

    def generate(self, program: ast.Program) -> tuple[list[ast.Stmt], Constraint]:
        """Build the whole program's constraint, and its evaluation order.

        The evaluator reuses that order, so a binding is always initialized
        before anything that reads it. The constraint has the same shape: one
        `CLet` per binding group, nested so that later groups are solved in the
        scope of earlier ones.
        """
        type_decls = [d for d in program.decls if isinstance(d, ast.TypeDecl)]
        class_decls = [d for d in program.decls if isinstance(d, ast.ClassDecl)]
        inst_decls = [d for d in program.decls if isinstance(d, ast.InstanceDecl)]
        items = [d for d in program.decls if isinstance(d, ast.Stmt)]
        self.decls.register_all(type_decls)
        self.classes.register_all(class_decls, inst_decls, self.module)
        self.bind_methods()

        # The graph is keyed by item, not by bound name: a single binding may
        # introduce several names (`let (a, b) = ...`), and keying by name would
        # split one item across two components and infer -- and evaluate -- its
        # right-hand side twice.
        keys = {id(item): f"item{i}" for i, item in enumerate(items)}
        by_key = {keys[id(item)]: item for item in items}
        names_of = {keys[id(item)]: self._item_names(item) for item in items}

        owner: dict[str, str] = {}
        for item in items:
            for name in names_of[keys[id(item)]]:
                if name in owner:
                    raise TypeError_(f"'{name}' is declared more than once", item.span)
                if name in self.classes.owner:
                    raise TypeError_(
                        f"'{name}' is already defined: it is a method of class "
                        f"'{self.classes.owner[name]}', and methods share the "
                        f"namespace of ordinary functions",
                        item.span,
                    )
                owner[name] = keys[id(item)]

        graph = {
            keys[id(item)]: {owner[n] for n in self._item_deps(item) if n in owner}
            for item in items
        }

        groups = []
        ordered: list[ast.Stmt] = []
        for component in sccs(graph):
            group = [by_key[key] for key in component]
            bound = sorted(n for key in component for n in names_of[key])
            self._check_group_recursion(group, bound, component, graph)
            groups.append(group)
            ordered.extend(group)

        def nest(index: int) -> None:
            if index < len(groups):
                self.bind_group(groups[index], lambda: nest(index + 1), top_level=True)
            else:
                # Method bodies are generated innermost, so that they may call
                # any top-level binding. Nothing calls *them* by name, so their
                # position in the nesting is otherwise free.
                self.gen_method_bodies(class_decls, inst_decls)

        nest(0)
        return ordered, self.pop()

    def bind_methods(self) -> None:
        """Put every class method in scope, under the scheme its class gives it."""
        for info in self.classes.classes.values():
            for method in info.methods.values():
                if method.name in self.classes.owner and self.bound(method.name):
                    # Already bound by an earlier module: a class method is
                    # global and unqualified, so every module sees the same
                    # one. Binding it again is a no-op, not a collision.
                    continue
                if self.bound(method.name):
                    raise TypeError_(
                        f"'{method.name}' is already defined; a class method "
                        f"shares the namespace of ordinary functions",
                        method.decl.span,
                    )
                self.env.define(method.name, Binding(method.scheme, False))
                self.scopes[0][method.name] = False

    def gen_method_bodies(
        self, classes: list[ast.ClassDecl], instances: list[ast.InstanceDecl]
    ) -> None:
        """Check every defaulted class method and every instance method.

        Each is checked against the type its *class* states, with everything
        the declaration does not fix made rigid -- see `Skolems`. That is the
        whole difference between checking a method and inferring a function: a
        method has a type to live up to, and a fresh unification variable would
        let a body that is less general than the signature pass by narrowing
        the signature to fit.
        """
        for decl in classes:
            info = self.classes.classes[decl.name]
            for method in info.methods.values():
                if method.decl.body is None:
                    continue
                skolems = Skolems()
                skolems.bind(info.var, info.param)
                # One elaboration of a default serves every instance: its own
                # class dictionary arrives under a name that each instance
                # rebinds as its dictionary is built.
                info.defaults[method.name] = self.check_method(
                    method, skolems, [], [],
                    f"the default definition of '{method.name}'")
        for decl in instances:
            inst = next(i for i in self.classes.instances[decl.cls] if i.decl is decl)
            info = self.classes.classes[decl.cls]
            plan = InstancePlan([dict_name(q.name) for q in inst.context])
            inst.plan = plan
            # The instance's own dictionary and its context are named once and
            # shared by every method, because that is how they are bound: once,
            # when the dictionary is built.
            self_name = dict_name(decl.cls)
            for method_decl in decl.methods:
                method = info.methods[method_decl.name]
                skolems = Skolems()
                for var in vars_of(inst.head):
                    skolems.bind(var, inst.names[var.id])
                # The class variable is not made rigid: the instance fixes it.
                skolems.mapping[method.class_var.id] = skolems.apply(inst.head)
                plan.methods[method_decl.name] = self.check_method(
                    method, skolems,
                    [Pred(q.name, [skolems.apply(q.args[0])]) for q in inst.context],
                    plan.params,
                    f"instance '{decl.cls} {show(inst.head)}'",
                    method_decl,
                    self_name,
                )

    def check_method(
        self,
        method: MethodInfo,
        skolems: Skolems,
        context: list[Pred],
        context_names: list[str],
        what: str,
        decl: ast.FunDecl | None = None,
        self_name: str | None = None,
    ) -> MethodImpl:
        for var in method.scheme.quantified:
            if var.id not in skolems.mapping:
                skolems.bind(var, method.names.get(var.id, "a"))
        expected = skolems.apply(method.scheme.body)
        # A method's scheme states its class's predicate first, then its own
        # context. The two are given to the body for different reasons and
        # arrive by different routes: the first is the dictionary the body
        # belongs to, the rest are parameters of every call.
        own = [skolems.apply_pred(p) for p in method.scheme.preds[1:]]
        self_name = self_name if self_name is not None else dict_name(method.cls)
        params = [dict_name(p.name) for p in own]
        givens = (
            [(self_name, skolems.apply_pred(method.scheme.preds[0]))]
            + list(zip(params, own))
            + list(zip(context_names, context))
        )

        decl = decl if decl is not None else method.decl
        self.push()
        inferred = self.gen_function(decl)
        self.eq(inferred, expected, decl.span,
                f"the type class '{method.cls}' declares for '{method.name}'")
        defn = self.pop()
        # A `CLet` for the rank, not for the polymorphism: the expected type is
        # rigid, so nothing generalizes. What the rank buys is that a predicate
        # the body raised is settled here, against the assumptions, instead of
        # escaping to the end of the program.
        self.emit(CAssume(givens, CLet([(f"%{what}.{method.name}", expected)],
                                       defn, CAnd([]), decl.span,
                                       skolems=list(skolems.made))))
        return MethodImpl(decl, self_name, params)

    # -- signatures --------------------------------------------------------

    def declared_scheme(
        self, decl: ast.FunDecl
    ) -> tuple[Scheme, dict[str, Type]] | None:
        """The type a `fun` states outright, or None if it states none.

        A signature is *complete* or it is nothing: every parameter annotated
        and a return type written. The all-or-nothing rule is not a limitation
        of the machinery but the point of it -- a half-stated type has no
        scheme for a recursive call to instantiate, and a reader can tell by
        eye which functions are checked and which are inferred.

        The quantified variables are read off the annotation scope rather than
        found by `generalize`, because generalization is a *solving* step and
        nothing here has a rank yet. It needs no search: SPEC-DELTAS 13 already
        says an annotation's variables are exactly those scoped to the `fun`.
        """
        if decl.body is None or decl.ret is None:
            return None
        annots = []
        for param in decl.params:
            if not isinstance(param, ast.PAnnot):
                return None
            annots.append(param.type_expr)
        tyvars: dict[str, Type] = {}
        params = [self.decls.star(a, tyvars, self.fresh) for a in annots]
        ret = self.decls.star(decl.ret, tyvars, self.fresh)
        preds = self.classes.resolve_context(decl.context, tyvars, self.fresh)
        quantified = [v for v in tyvars.values() if isinstance(v, TVar)]
        body = TFun(params, ret)
        # The ambiguity test the inference path applies at generalization, run
        # here instead, where it is purely syntactic: a written context that
        # constrains a variable the type does not reach can never be decided by
        # a use site, because there is nothing at a use site to decide it with.
        # `reach` is the same closure `split` uses, so `[Container c, Show
        # (Elem c)]` passes -- `Elem c` reaches `c`, and `c` is in the type.
        ids = reach([CPred(p, decl.span, "read") for p in preds], [body])
        # Blamed in the names the signature wrote, not in the letters `show`
        # would invent: the whole point of a declared type is that it is the
        # programmer's text, so a complaint about it should quote that text.
        written_names = {v.id: n for n, v in tyvars.items() if isinstance(v, TVar)}
        for pred, written in zip(preds, decl.context):
            if not any(v.id in ids for v in vars_of(*pred.args)):
                raise TypeError_(
                    f"'{show_pred(pred, written_names)}' constrains a type "
                    f"that the declared type of '{decl.name}' does not "
                    f"mention, so no use of '{decl.name}' could decide it",
                    written.span,
                )
        return Scheme(quantified, body, list(preds)), tyvars

    def check_signature(
        self, decl: ast.FunDecl, scheme: Scheme, tyvars: dict[str, Type]
    ) -> Constraint:
        """Check a body against the type its `fun` declared.

        The same procedure as `check_method`, for the same reason: a stated
        type has to be lived up to, and a fresh unification variable would let
        a body that is *less* general than the signature pass by narrowing the
        signature to fit. That is precisely the defect SPEC-DELTAS 13 recorded
        and deferred -- an annotation was a constraint the body could unify
        away rather than a promise it had to keep.
        """
        skolems = Skolems()
        rigid = {name: skolems.bind(var, name) for name, var in tyvars.items()
                 if isinstance(var, TVar)}
        expected = skolems.apply(scheme.body)
        # *Every* declared predicate is a fact the body may use, so every one
        # becomes a given -- an equality included, since `Solver.reduce` reads
        # the assumptions for the rewrite that makes `Item s` become `Op`.
        # Only a class predicate arrives as a *dictionary*, though; the erased
        # kinds are facts the body never asks for evidence of. So the runtime
        # parameters are the class predicates alone, in the scheme's order,
        # because that is the order a use site instantiates in and
        # `eval.supply` zips the two together positionally.
        givens = [(dict_name(p.name), skolems.apply_pred(p))
                  for p in scheme.preds]
        passed = [(name, p) for (name, _), p in zip(givens, scheme.preds)
                  if self.classes.is_class(p.name)]
        decl.dicts = Abstraction([n for n, _ in passed], [p for _, p in passed])

        self.push()
        inferred = self.gen_function(decl, rigid)
        self.eq(inferred, expected, decl.span,
                f"the declared type of '{decl.name}'")
        defn = self.pop()
        # A `CLet` for the rank, not for the polymorphism -- see `check_method`:
        # the expected type is rigid, so nothing generalizes, and what the rank
        # buys is that a predicate the body raised is settled here, against the
        # assumptions, instead of escaping to the end of the program.
        return CAssume(givens, CLet([(f"%sig.{decl.name}", expected)],
                                    defn, CAnd([]), decl.span,
                                    skolems=list(skolems.made)))

    def check_exhaustiveness(self) -> None:
        """Section 5.1: a non-exhaustive match is a warning, not an error --
        reaching an unhandled case is a runtime panic.

        Runs after solving, since it needs the scrutinee types resolved.
        """
        from .exhaustive import Checker

        checker = Checker(self.decls)
        for match, scrutinee in self.match_sites:
            # Normalized, not merely pruned: the element of a `for ... in` loop
            # is `Item xs`, and a family names a type only once it has reduced.
            missing = checker.check(match, self.classes.normalize(scrutinee))
            if missing is None:
                continue
            # A bare `_` witness means the scrutinee's type has too many values
            # to name one -- an Int, say -- so there is nothing useful to quote.
            detail = (
                "it needs a catch-all arm" if missing == "_"
                else f"'{missing}' is not handled"
            )
            self.warnings.append(
                f"{match.span}: warning: this match is not exhaustive; {detail}"
            )

    @staticmethod
    def _item_names(item: ast.Stmt) -> list[str]:
        if isinstance(item, ast.SFun):
            return [item.decl.name]
        return sorted(pattern_vars(item.pat))

    @staticmethod
    def _item_deps(item: ast.Stmt) -> set[str]:
        if isinstance(item, ast.SFun):
            return free_names(item.decl.body, frozenset(
                n for p in item.decl.params for n in pattern_vars(p)
            ))
        return free_names(item.value)

    @staticmethod
    def _check_group_recursion(
        group: list[ast.Stmt],
        bound: list[str],
        component: list[str],
        graph: dict[str, set[str]],
    ) -> None:
        recursive = len(component) > 1 or any(k in graph[k] for k in component)
        if recursive and not all(isinstance(i, ast.SFun) for i in group):
            names = ", ".join(bound)
            raise TypeError_(
                f"cyclic definition: {names} depend on each other, but only "
                f"functions may be mutually recursive",
                group[0].span,
            )

    # -- binders -----------------------------------------------------------

    def bind_group(
        self, group: list[ast.Stmt], rest: Callable[[], T], top_level: bool = False
    ) -> T:
        """Generate a binding group, scoping it over whatever `rest` generates.

        This is the only place a binder is built, so it is the only place that
        has to get the nesting right. `rest` is a callback rather than a list of
        statements because the scope of a `let` is not always "the remaining
        statements" -- a C-style `for`'s initializer scopes over the condition
        and step too.
        """
        binds, defn, generalizes, mutable, sigs = self.build_definition(group)

        self.scopes.append(
            {name: mutable for name, _ in binds}
            | {name: False for name, _ in sigs}
        )
        self.push()
        try:
            out = rest()
        finally:
            body = self.pop()
            self.scopes.pop()

        if not binds:
            # Every member states its own type, so there is nothing left to
            # generalize and no rank to push for.
            node: Constraint = CAnd([defn, body])
        elif generalizes:
            # One abstraction for the group, hung on each declaration in it so
            # the evaluator can find it, and handed to the `CLet` so the solver
            # can fill it in when it decides what the schemes retain. A member
            # with a signature is not in the group's `binds` and got its own
            # abstraction from `check_signature`, so it is skipped here.
            dicts = Abstraction()
            bound = {name for name, _ in binds}
            for item in group:
                if isinstance(item, ast.SFun):
                    if item.decl.name in bound:
                        item.decl.dicts = dicts
                else:
                    item.dicts = dicts
            node = CLet(binds, defn, body, group[0].span, top_level, dicts)
        else:
            # No generalization means no new rank, so the definition is solved
            # right here and only the names are scoped. Nothing needs its rank
            # lowered afterwards, because nothing ever raised it.
            node = CAnd([defn, CDef(binds, body, top_level)])
        # The declared schemes scope over the whole group *and* over `rest`,
        # which is what makes a recursive occurrence an instantiation.
        if sigs:
            node = CBind(sigs, node, top_level)
        self.emit(node)
        return out

    def build_definition(
        self, group: list[ast.Stmt]
    ) -> tuple[
        list[tuple[str, Type]], Constraint, bool, bool, list[tuple[str, Scheme]]
    ]:
        """The bound names, the definition's constraint, and how it binds."""
        if all(isinstance(item, ast.SFun) for item in group):
            binds, defn, sigs = self.build_fun_group(
                [item.decl for item in group])
            # A `fun` is syntactically a value, so the value restriction never
            # blocks generalization here.
            return binds, defn, True, False, sigs

        assert len(group) == 1, "only functions may share a binding group"
        stmt = group[0]
        assert isinstance(stmt, (ast.SLet, ast.SVar))
        self.push()
        value = self.gen_expr(stmt.value)
        binds = list(self.match_pattern(stmt.pat, value).items())
        defn = self.pop()
        # Section 4.4: only a `let` bound to a syntactic value generalizes.
        generalizes = isinstance(stmt, ast.SLet) and self.is_nonexpansive(stmt.value)
        return binds, defn, generalizes, isinstance(stmt, ast.SVar), []

    def build_fun_group(
        self, decls: list[ast.FunDecl]
    ) -> tuple[list[tuple[str, Type]], Constraint, list[tuple[str, Scheme]]]:
        """Section 5.2 step 3: one strongly-connected group at a time.

        A group splits by whether a member states its own type. An inferred
        member's placeholder is bound *monomorphically* inside the definition,
        by the inner `CDef`, which is what keeps polymorphic recursion out of
        inference, where it is undecidable; the enclosing `CLet` generalizes it
        only once every body has been solved. A member with a signature is
        bound to that signature by the `CBind` `bind_group` wraps around the
        whole group, so a recursive use of it instantiates instead.

        Mixing the two in one group is allowed and behaves as it does in
        Haskell: an inferred member calling an annotated one instantiates the
        declared scheme, and an annotated member calling an inferred one sees
        the one monomorphic placeholder, since there is nothing more to see.
        """
        sigs: list[tuple[str, Scheme]] = []
        checks: list[tuple[ast.FunDecl, Scheme, dict[str, Type]]] = []
        inferred: list[ast.FunDecl] = []
        for decl in decls:
            declared = self.declared_scheme(decl)
            if declared is None:
                inferred.append(decl)
            else:
                scheme, tyvars = declared
                sigs.append((decl.name, scheme))
                checks.append((decl, scheme, tyvars))

        self.push()
        binds = [(decl.name, self.fresh()) for decl in inferred]
        self.scopes.append(
            {name: False for name, _ in binds}
            | {name: False for name, _ in sigs}
        )
        self.push()
        for decl, scheme, tyvars in checks:
            self.emit(self.check_signature(decl, scheme, tyvars))
        for decl, (_, placeholder) in zip(inferred, binds):
            self.eq(placeholder, self.gen_function(decl), decl.span)
        inner = self.pop()
        self.scopes.pop()
        self.emit(CDef(binds, inner))
        return binds, self.pop(), sigs

    def gen_function(
        self, decl: ast.FunDecl | ast.ELambda, rigid: dict[str, Type] | None = None
    ) -> TFun:
        """A function's own constraint. Parameters bind monomorphically.

        No rank is pushed: a lambda is not a generalization point, so its
        parameters live at whatever rank the enclosing binder established.

        `rigid` is the annotation scope to work in, and passing one is what
        distinguishes checking from inferring. It maps each name the signature
        quantifies to a skolem constant, so every annotation in the body reads
        the *same* rigid type the signature promised -- and, since the context
        is then a fact rather than an obligation, it is not emitted here.
        """
        self.tyvar_scopes.append({} if rigid is None else dict(rigid))
        self.push()

        param_types: list[Type] = []
        binds: dict[str, Type] = {}
        for param in decl.params:
            tv = self.fresh()
            self._merge(binds, self.match_pattern(param, tv), param.span)
            param_types.append(tv)

        ret = self.fresh()
        if decl.ret is not None:
            self.eq(ret, self.type_of(decl.ret), decl.span, "the return type annotation")

        # On the inference path a declared context is emitted as an ordinary
        # demand rather than attached to the scheme by hand. It then travels
        # the same road as one the body raised -- deferred while its variable
        # is open, retained by the binder that quantifies it, and rejected as
        # ambiguous if no type in the group mentions it. Asking for a context
        # is not the same as being granted one, and a `fun` with an incomplete
        # annotation has no signature to be granted anything by.
        #
        # A complete one does. There the context has already become a *given*
        # (`check_signature`), so re-emitting it here would demand of the body
        # exactly what the caller has been made to promise.
        if rigid is None:
            for pred in self.classes.resolve_context(
                getattr(decl, "context", []), self.tyvar_scopes[-1], self.fresh
            ):
                self.emit(CPred(pred, decl.span, "read"))

        # Parameters are reassignable (`fun gcd(a, b) { a = b ... }`). `CDef`
        # binds them monomorphically, so the value restriction that makes a
        # `var` binding special has nothing to say about them; reassigning one
        # rebinds this local slot and does not write through to the caller.
        self.scopes.append({name: True for name in binds})
        self.push()
        self.fn_stack.append(ret)
        loops, self.loop_stack = self.loop_stack, []  # `break` cannot cross a function
        body = self.gen_expr(decl.body)
        self.loop_stack = loops
        self.fn_stack.pop()
        # A body of type bottom leaves `ret` to be fixed by the `return`s.
        self.eq(ret, body, decl.span, "the function body")
        inner = self.pop()
        self.scopes.pop()

        self.emit(CDef(list(binds.items()), inner))
        self.emit(self.pop())
        self.tyvar_scopes.pop()
        return TFun(param_types, ret)

    # -- patterns ----------------------------------------------------------

    def match_pattern(self, pat: ast.Pattern, t: Type) -> dict[str, Type]:
        """Constrain a pattern against the type it scrutinizes, and name its
        binders. Binding them is the caller's job, since only the caller knows
        whether they are monomorphic."""
        if isinstance(pat, ast.PWild):
            return {}
        if isinstance(pat, ast.PVar):
            return {pat.name: t}
        if isinstance(pat, ast.PLit):
            if pat.kind in NUMERIC_KINDS:
                self.eq(t, self.numeric(pat.kind, pat.value, pat.span,
                                        "a literal pattern"),
                        pat.span, "a literal pattern")
            else:
                self.eq(t, LITERAL_TYPES[pat.kind], pat.span, "a literal pattern")
            return {}
        if isinstance(pat, ast.PAnnot):
            self.eq(t, self.type_of(pat.type_expr), pat.span, "a pattern annotation")
            return self.match_pattern(pat.pat, t)
        if isinstance(pat, ast.PTuple):
            elems = [self.fresh() for _ in pat.elems]
            self.eq(t, TTuple(elems), pat.span, "a tuple pattern")
            out: dict[str, Type] = {}
            for sub, ty in zip(pat.elems, elems):
                self._merge(out, self.match_pattern(sub, ty), pat.span)
            return out
        if isinstance(pat, ast.PCon):
            # A record variant matches positionally too: `con.params` is in
            # declaration order for both forms, so nothing here cares which
            # form declared it. Only the record form may omit fields, which
            # is why the arity check below stays unconditional.
            con = self.decls.instantiate_con(pat.name, self.fresh, pat.span)
            if len(pat.args) != len(con.params):
                raise TypeError_(
                    f"constructor '{pat.name}' takes {len(con.params)} argument(s), "
                    f"but the pattern supplies {len(pat.args)}",
                    pat.span,
                )
            self.eq(t, con.ret, pat.span, f"the pattern '{pat.name}'")
            out = {}
            for sub, ty in zip(pat.args, con.params):
                self._merge(out, self.match_pattern(sub, ty), pat.span)
            return out
        if isinstance(pat, ast.PRecord):
            con = self.decls.instantiate_con(pat.name, self.fresh, pat.span)
            info = self.decls.con(pat.name)
            if not info.is_record:
                raise TypeError_(
                    f"constructor '{pat.name}' has positional arguments, not fields",
                    pat.span,
                )
            self.eq(t, con.ret, pat.span, f"the pattern '{pat.name}'")
            out = {}
            for label, sub in pat.fields:
                if label not in info.field_names:
                    raise TypeError_(
                        f"'{pat.name}' has no field '{label}'", pat.span
                    )
                index = info.field_names.index(label)
                self._merge(out, self.match_pattern(sub, con.params[index]), pat.span)
            return out
        raise AssertionError(f"unhandled pattern {type(pat).__name__}")

    @staticmethod
    def _merge(into: dict[str, Type], new: dict[str, Type], span: Span) -> None:
        for name, ty in new.items():
            if name in into:
                raise TypeError_(f"'{name}' is bound twice in the same pattern", span)
            into[name] = ty

    # -- statements --------------------------------------------------------

    def gen_sequence(self, stmts: list[ast.Stmt]) -> Type:
        """Section 6.8: a block's value is its last statement's, and a
        declaration contributes Unit.

        A binder scopes over the statements that follow it, so it is generated
        as a binder *around* them rather than as one more statement in a list.
        """
        if not stmts:
            return UNIT
        stmt, rest = stmts[0], stmts[1:]
        if isinstance(stmt, (ast.SLet, ast.SVar, ast.SFun)):
            return self.bind_group([stmt], lambda: self.gen_sequence(rest))
        value = self.gen_stmt(stmt)
        return self.gen_sequence(rest) if rest else value

    def gen_stmt(self, stmt: ast.Stmt) -> Type:
        """A statement that binds nothing. Binders go through `bind_group`."""
        if isinstance(stmt, ast.SExpr):
            return self.gen_expr(stmt.expr)
        if isinstance(stmt, ast.SAssign):
            self.gen_assign(stmt)
            return UNIT
        raise AssertionError(f"unhandled statement {type(stmt).__name__}")

    def gen_assign(self, stmt: ast.SAssign) -> None:
        target, span = stmt.target, stmt.span

        if isinstance(target, ast.EVar):
            if not self.bound(target.name):
                raise TypeError_(f"'{target.name}' is not defined", span)
            if not self.is_mutable(target.name):
                raise TypeError_(
                    f"cannot assign to '{target.name}': it was bound with 'let'. "
                    f"Use 'var' to make it reassignable.",
                    span,
                )
            self.eq(
                self.use(target.name, span),
                self.gen_expr(stmt.value),
                span,
                f"the assignment to '{target.name}'",
            )
            return

        if isinstance(target, ast.EField):
            field_type = self.gen_field(target, assigning=True)
            self.eq(field_type, self.gen_expr(stmt.value), span, "the assigned value")
            return

        if isinstance(target, ast.EIndex):
            element = self.fresh()
            self.eq(
                self.gen_expr(target.arr), array_of(element), span,
                "an indexed assignment",
            )
            self.eq(self.gen_expr(target.index), INT, span, "an array index")
            self.eq(element, self.gen_expr(stmt.value), span, "the assigned value")
            return

        raise AssertionError("parser should have rejected this assignment target")

    # -- expressions -------------------------------------------------------

    def gen_expr(self, e: ast.Expr) -> Type:
        method = getattr(self, "_gen_" + type(e).__name__, None)
        if method is None:
            raise AssertionError(f"unhandled expression {type(e).__name__}")
        ty = method(e)
        # One place, so no case can forget. What is recorded is the variable
        # itself, not a snapshot of it: solving fills it in later, and reading
        # it back is `TypeTable.resolve`'s job. See turkey/typed.py.
        self.types.record(e, ty)
        return ty

    def use(self, name: str, span: Span | None, node: ast.EVar | None = None) -> Type:
        """`name <= t` for a fresh `t`. No lookup: that is the solver's job.

        A `Use` rides along when there is an occurrence to attach it to, so that
        elaboration has somewhere to put the evidence this site turns out to
        need. What that is cannot be known here -- it depends on what the solver
        decides the name's predicates are about -- which is why the site is
        marked rather than resolved.
        """
        t = self.fresh()
        marker = Use(name, span) if node is not None else None
        if node is not None:
            node.use = marker
        self.emit(CInstance(name, t, span, marker))
        return t

    def _gen_ELit(self, e: ast.ELit) -> Type:
        if e.kind in NUMERIC_KINDS:
            return self.numeric(e.kind, e.value, e.span, "")
        return LITERAL_TYPES[e.kind]

    def numeric(self, kind: str, value, span: Span | None, context: str) -> Type:
        """A fresh variable, plus the set of types the literal could have.

        A numeric literal is not given a type here. Which type it has is a
        decision, and decisions belong to the solver -- the set is all the
        syntax actually determines. `1` is not an `Int` that a later rule might
        widen; it is a numeral whose set happens to contain `Int`.
        """
        t = self.fresh()
        names = int_literal_set(value) if kind == "Int" else float_literal_set()
        self.emit(CPred(Pred(ONE_OF, [t, TSet(names)]), span, context))
        return t

    def _gen_EUnit(self, e: ast.EUnit) -> Type:
        return UNIT

    def _gen_EVar(self, e: ast.EVar) -> Type:
        if not self.bound(e.name):
            raise TypeError_(f"'{e.name}' is not defined", e.span)
        return self.use(e.name, e.span, e)

    def _gen_ECon(self, e: ast.ECon) -> Type:
        con = self.decls.instantiate_con(e.name, self.fresh, e.span)
        # A nullary constructor is a value; anything else is a function.
        return con.ret if not con.params else con

    def _gen_ETuple(self, e: ast.ETuple) -> Type:
        return TTuple([self.gen_expr(x) for x in e.elems])

    def _gen_EArray(self, e: ast.EArray) -> Type:
        element: Type = self.fresh()
        for item in e.elems:
            element = self.join(element, self.gen_expr(item), item.span, "an array literal")
        return array_of(element)

    def _gen_ERecord(self, e: ast.ERecord) -> Type:
        con = self.decls.instantiate_con(e.con, self.fresh, e.span)
        info = self.decls.con(e.con)
        if not info.is_record:
            raise TypeError_(
                f"constructor '{e.con}' takes positional arguments; write "
                f"'{e.con}(...)'",
                e.span,
            )
        given = [label for label, _ in e.fields]
        missing = [f for f in info.field_names if f not in given]
        unknown = [f for f in given if f not in info.field_names]
        if unknown:
            raise TypeError_(f"'{e.con}' has no field '{unknown[0]}'", e.span)
        if missing:
            raise TypeError_(
                f"'{e.con}' is missing field(s): {', '.join(missing)}", e.span
            )
        if len(given) != len(set(given)):
            raise TypeError_(f"a field is initialized twice in '{e.con}'", e.span)
        # Section 6.1: fields evaluate left to right, so check them in that order.
        for label, value in e.fields:
            index = info.field_names.index(label)
            self.eq(
                con.params[index], self.gen_expr(value), value.span,
                f"field '{label}' of '{e.con}'",
            )
        return con.ret

    def _gen_ELambda(self, e: ast.ELambda) -> Type:
        return self.gen_function(e)

    def _gen_ECall(self, e: ast.ECall) -> Type:
        fn = self.gen_expr(e.fn)
        args = [self.gen_expr(a) for a in e.args]
        result = self.fresh()
        self.eq(fn, TFun(args, result), e.span, "a function call")
        return result

    def _gen_EIndex(self, e: ast.EIndex) -> Type:
        element = self.fresh()
        self.eq(self.gen_expr(e.arr), array_of(element), e.span, "an index")
        self.eq(self.gen_expr(e.index), INT, e.index.span, "an array index")
        return element

    def _gen_EField(self, e: ast.EField) -> Type:
        return self.gen_field(e, assigning=False)

    def gen_field(self, e: ast.EField, assigning: bool) -> Type:
        """Emit `HasField "f" r a` for `r.f` and hand back the `a`.

        Nothing is decided here. If the receiver turns out to be known the
        solver discharges the predicate and unifies the result with the declared
        field type; if not, the predicate waits, and may end up travelling in a
        scheme. That is the whole of SPEC-DELTAS.md entry 7: the old code pruned
        the receiver and demanded a record on the spot, which made `a.length`
        mean different things depending on whether the walk had reached `a[0]`
        yet.
        """
        receiver = self.gen_expr(e.obj)
        result = self.fresh()
        self.emit(CPred(
            Pred(HAS_FIELD, [TLabel(e.name), receiver, result]),
            e.span,
            "mutate" if assigning else "read",
        ))
        return result

    def _gen_EUnary(self, e: ast.EUnary) -> Type:
        if e.fn is not None:
            return self.gen_operator(e.fn, [e.operand], e.op, e.span)
        operand, result = UNARY_OPS[e.op]
        self.eq(self.gen_expr(e.operand), operand, e.span, f"the operand of '{e.op}'")
        return result

    def _gen_EBinary(self, e: ast.EBinary) -> Type:
        if e.fn is not None:
            return self.gen_operator(e.fn, [e.left, e.right], e.op, e.span)
        left, right, result = BINARY_OPS[e.op]
        self.eq(self.gen_expr(e.left), left, e.left.span, f"the left operand of '{e.op}'")
        self.eq(self.gen_expr(e.right), right, e.right.span, f"the right operand of '{e.op}'")
        return result

    def gen_operator(
        self, fn: ast.EVar, operands: list[ast.Expr], op: str, span: Span
    ) -> Type:
        """An operator is a call to the method it desugars to (M8).

        Nothing here knows that `add` is special. The method's own scheme --
        `[Add a] fun(a, a) -> a` -- is what makes both operands agree and what
        raises the class predicate, so an operator on a user's own type needs
        no case anywhere: it needs an instance.
        """
        method = self.gen_expr(fn)
        args = [self.gen_expr(x) for x in operands]
        result = self.fresh()
        self.eq(method, TFun(args, result), span,
                f"the operands of '{op}'" if len(args) > 1 else f"the operand of '{op}'")
        return result

    def _gen_EAnnot(self, e: ast.EAnnot) -> Type:
        annotated = self.type_of(e.type_expr)
        return self.join(self.gen_expr(e.expr), annotated, e.span, "a type annotation")

    def _gen_EBlock(self, e: ast.EBlock) -> Type:
        return self.gen_sequence(e.stmts)

    def _gen_EIf(self, e: ast.EIf) -> Type:
        self.eq(self.gen_expr(e.cond), BOOL, e.cond.span, "an 'if' condition")
        then = self.gen_expr(e.then)
        if e.otherwise is None:
            return UNIT  # section 6.7: statement-style `if` has no value
        return self.join(then, self.gen_expr(e.otherwise), e.span, "the branches of an 'if'")

    def _gen_EWhile(self, e: ast.EWhile) -> Type:
        self.eq(self.gen_expr(e.cond), BOOL, e.cond.span, "a 'while' condition")
        self.loop_stack.append(LoopCtx("while", UNIT))
        self.gen_expr(e.body)
        self.loop_stack.pop()
        return UNIT

    def _gen_EForIn(self, e: ast.EForIn) -> Type:
        """`for x in xs` walks a cursor, and `x` is `Item xs` (M8, amended).

        `iter` makes the cursor and `next` advances it; both are generated as
        ordinary uses, so the loop demands `Iterator` the same way any call
        does and elaboration hands it a dictionary with no case of its own.
        Both the cursor and the element are family applications, and are left
        to reduce like every other one.
        """
        sequence = self.gen_expr(e.iterable)
        cursor = self.fresh()
        element = self.fresh()
        where = "the sequence of a 'for ... in' loop"
        self.eq(self.gen_expr(e.iter_fn), TFun([sequence], cursor),
                e.iterable.span, where)
        self.eq(
            self.gen_expr(e.next_fn),
            TFun([sequence, cursor], apply(self.decls.heads[prelude.OPTION], [element])),
            e.iterable.span, where,
        )
        # Section 6.5: `x` is a fresh immutable binding each iteration.
        binds = self.match_pattern(e.pat, element)
        self.scopes.append({name: False for name in binds})
        self.push()
        self.loop_stack.append(LoopCtx("for", UNIT))
        self.gen_expr(e.body)
        self.loop_stack.pop()
        body = self.pop()
        self.scopes.pop()
        self.emit(CDef(list(binds.items()), body))
        return UNIT

    def _gen_EForC(self, e: ast.EForC) -> Type:
        def rest() -> Type:
            self.eq(self.gen_expr(e.cond), BOOL, e.cond.span, "a 'for' condition")
            self.loop_stack.append(LoopCtx("for", UNIT))
            self.gen_expr(e.body)
            if e.step is not None:
                self.gen_stmt(e.step)
            self.loop_stack.pop()
            return UNIT

        if isinstance(e.init, (ast.SLet, ast.SVar)):
            # The initializer's scope is the condition, body and step -- not a
            # list of following statements, which is why `bind_group` takes a
            # callback.
            return self.bind_group([e.init], rest)
        if e.init is not None:
            self.gen_stmt(e.init)
        return rest()

    def _gen_ELoop(self, e: ast.ELoop) -> Type:
        ctx = LoopCtx("loop", self.fresh())
        self.loop_stack.append(ctx)
        self.gen_expr(e.body)
        self.loop_stack.pop()
        # Section 6.7: a `loop` never falls through, so its value comes only
        # from its breaks. With no break at all it never produces one.
        return ctx.result if ctx.saw_break else BOTTOM

    def _gen_EMatch(self, e: ast.EMatch) -> Type:
        scrutinee = self.gen_expr(e.scrutinee)
        self.match_sites.append((e, scrutinee))
        result: Type = BOTTOM
        for arm in e.arms:
            bindings = self.match_pattern(arm.patterns[0], scrutinee)
            for alt in arm.patterns[1:]:
                other = self.match_pattern(alt, scrutinee)
                if set(other) != set(bindings):
                    raise TypeError_(
                        "every alternative in a match arm must bind the same "
                        "variables",
                        alt.span,
                    )
                for name, ty in other.items():
                    self.eq(bindings[name], ty, alt.span, f"the binding '{name}'")
            self.scopes.append({name: False for name in bindings})
            self.push()
            body = self.gen_expr(arm.body)
            arm_c = self.pop()
            self.scopes.pop()
            self.emit(CDef(list(bindings.items()), arm_c))
            result = self.join(result, body, arm.span, "the arms of a 'match'")
        return result

    def _gen_EReturn(self, e: ast.EReturn) -> Type:
        if not self.fn_stack:
            raise TypeError_("'return' is only valid inside a function", e.span)
        value = self.gen_expr(e.value) if e.value is not None else UNIT
        self.eq(self.fn_stack[-1], value, e.span, "a 'return'")
        return BOTTOM

    def _gen_EBreak(self, e: ast.EBreak) -> Type:
        if not self.loop_stack:
            raise TypeError_("'break' is only valid inside a loop", e.span)
        ctx = self.loop_stack[-1]
        ctx.saw_break = True
        if e.value is None:
            self.eq(ctx.result, UNIT, e.span, "a valueless 'break'")
        elif ctx.kind != "loop":
            raise TypeError_(
                f"'break' cannot carry a value out of a '{ctx.kind}' loop; only "
                f"'loop' produces one",
                e.span,
            )
        else:
            ctx.result = self.join(ctx.result, self.gen_expr(e.value), e.span, "a 'break'")
        return BOTTOM

    def _gen_EContinue(self, e: ast.EContinue) -> Type:
        if not self.loop_stack:
            raise TypeError_("'continue' is only valid inside a loop", e.span)
        return BOTTOM

    # -- value restriction --------------------------------------------------

    def is_nonexpansive(self, e: ast.Expr) -> bool:
        """Section 4.4. Only a syntactic value may be generalized."""
        if isinstance(e, (ast.ELit, ast.EUnit, ast.EVar, ast.ELambda)):
            return True
        if isinstance(e, ast.ECon):
            return True
        if isinstance(e, ast.EAnnot):
            return self.is_nonexpansive(e.expr)
        if isinstance(e, ast.ETuple):
            return all(self.is_nonexpansive(x) for x in e.elems)
        if isinstance(e, ast.ECall):
            # `C(v1, ..., vn)` for an immutable constructor is still a value.
            if isinstance(e.fn, ast.ECon) and self._immutable_con(e.fn.name):
                return all(self.is_nonexpansive(a) for a in e.args)
            return False
        if isinstance(e, ast.ERecord):
            return self._immutable_con(e.con) and all(
                self.is_nonexpansive(v) for _, v in e.fields
            )
        return False

    def _immutable_con(self, name: str) -> bool:
        info = self.decls.con(name)
        if info is None:
            return False
        return not self.decls.tycons[info.tycon].is_mutable_record


def _names_of(env: Env) -> dict[str, bool]:
    """Every name an environment holds, with its mutability."""
    out: dict[str, bool] = {}
    scope: Env | None = env
    while scope is not None:
        for name, binding in scope.names.items():
            out.setdefault(name, binding.mutable)
        scope = scope.parent
    return out


# Re-exported for `builtins.py`, which builds the initial environment.
__all__ = ["Binding", "Env", "Generator", "BINARY_OPS", "UNARY_OPS"]
