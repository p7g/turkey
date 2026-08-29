"""Type inference for turkey-lite (design.md sections 4 and 5).

Algorithm J: unification variables are mutable, so there is no substitution to
carry around, and generalization is a level comparison rather than a scan of the
environment.

Three things here are specific to this language rather than to Hindley-Milner:

* Bottom. Anything that transfers control has type bottom, which unification
  absorbs. Wherever two branches must agree, `join` is used instead of `unify`
  so the surviving type comes back out (see turkey/types.py).
* Field access. `r.f` emits a `HasField` predicate rather than inspecting
  the receiver, so it does not matter whether the receiver's type is known yet
  -- or ever. An unresolved receiver that is still generalizable travels in the
  scheme, which is how a function may be polymorphic in the record it reads
  from without the language having rows.
* Annotation type variables are scoped to the enclosing function, so the `a` in
  a signature and the `a` in a body annotation are the same variable
  (SPEC-DELTAS.md entry 13).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ast
from .constraints import HAS_FIELD, CPred, Solver
from .decls import DeclTable
from .deps import free_names, pattern_vars, sccs
from .errors import Span, TypeError_
from .types import (
    BOOL, BOTTOM, CHAR, FLOAT, INT, STRING, UNIT, Pred, Scheme, TCon, TFun,
    TLabel, TTuple, TVar, Type, generalize, instantiate_qual, lower_to, mono,
    vars_of,
)

LITERAL_TYPES = {"Int": INT, "Float": FLOAT, "String": STRING, "Char": CHAR, "Bool": BOOL}

# Section 8.2. Without typeclasses every operator is monomorphic.
BINARY_OPS: dict[str, tuple[Type, Type, Type]] = {
    **{op: (INT, INT, INT) for op in ("+", "-", "*", "/", "%")},
    **{op: (FLOAT, FLOAT, FLOAT) for op in ("+.", "-.", "*.", "/.")},
    **{op: (INT, INT, BOOL) for op in ("==", "!=", "<", "<=", ">", ">=")},
    "++": (STRING, STRING, STRING),
    "&&": (BOOL, BOOL, BOOL),
    "||": (BOOL, BOOL, BOOL),
}

UNARY_OPS: dict[str, tuple[Type, Type]] = {"!": (BOOL, BOOL), "-": (INT, INT)}


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


@dataclass
class LoopCtx:
    kind: str  # loop | while | for
    result: Type
    saw_break: bool = False


class Inferencer:
    def __init__(self, decls: DeclTable, env: Env):
        self.decls = decls
        self.env = env
        self.solver = Solver(decls)
        self.level = 0
        self.fn_stack: list[Type] = []
        self.loop_stack: list[LoopCtx] = []
        self.tyvar_scopes: list[dict[str, TVar]] = [{}]
        self.warnings: list[str] = []
        # Exhaustiveness runs after inference, when scrutinee types are known.
        self.match_sites: list[tuple[ast.EMatch, Type]] = []

    # -- small helpers -----------------------------------------------------

    def fresh(self) -> TVar:
        return TVar(self.level)

    def type_of(self, te: ast.TypeExpr) -> Type:
        """Translate an annotation, resolving its type variables in the
        innermost function's scope."""
        return self.decls.to_type(te, self.tyvar_scopes[-1], self.level)

    def use(self, scheme: Scheme, span: Span | None) -> Type:
        """Instantiate a scheme at a use site, re-emitting its context.

        A scheme's predicates are obligations on whoever instantiates it, so
        they come back as constraints over this site's fresh variables. The
        verb is "read": a scheme records which field it needs, not whether the
        access that produced the demand was a read or an assignment.
        """
        preds, ty = instantiate_qual(scheme, self.level)
        for p in preds:
            self.solver.pred(p, span, "read")
        return ty

    def _reject_stranded(self, stranded: list[CPred]) -> None:
        """Report a predicate that can be neither discharged nor quantified.

        A `HasField` whose receiver is still a variable is fine as long as some
        scheme can carry it. One that reaches a generalization point without
        appearing in any of the types being generalized is stuck for good: no
        later unification can reach it, because nothing else mentions it.
        """
        if not stranded:
            return
        c = stranded[0]
        label = c.pred.args[0]
        assert isinstance(label, TLabel)
        raise TypeError_(
            f"cannot determine the type of the value whose field '{label.name}' "
            f"is being accessed. Add a type annotation.",
            c.span,
        )

    def _scoped(self, env: Env):
        outer, self.env = self.env, env
        return _Restore(self, outer)

    # -- program -----------------------------------------------------------

    def check_program(self, program: ast.Program) -> list[ast.Stmt]:
        """Type-check every top-level item, returning them in dependency order.

        The evaluator reuses that order, so a binding is always initialized
        before anything that reads it.
        """
        type_decls = [d for d in program.decls if isinstance(d, ast.TypeDecl)]
        items = [d for d in program.decls if not isinstance(d, ast.TypeDecl)]
        self.decls.register_all(type_decls)

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
                owner[name] = keys[id(item)]

        graph = {
            keys[id(item)]: {owner[n] for n in self._item_deps(item) if n in owner}
            for item in items
        }

        ordered: list[ast.Stmt] = []
        for component in sccs(graph):
            group = [by_key[key] for key in component]
            bound = sorted(n for key in component for n in names_of[key])
            self._infer_group(group, bound, component, graph)
            ordered.extend(group)
        self.solver.settle()
        self._reject_stranded(self.solver.deferred)
        self._check_exhaustiveness()
        return ordered

    def _check_exhaustiveness(self) -> None:
        """Section 5.1: a non-exhaustive match is a warning, not an error --
        reaching an unhandled case is a runtime panic."""
        from .exhaustive import Checker

        checker = Checker(self.decls)
        for match, scrutinee in self.match_sites:
            missing = checker.check(match, scrutinee)
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

    def _infer_group(
        self,
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
        if all(isinstance(i, ast.SFun) for i in group):
            self._infer_fun_group([i.decl for i in group])
        else:
            for item in group:
                self.infer_stmt(item)

    def _infer_fun_group(self, decls: list[ast.FunDecl]) -> None:
        """Section 5.2 step 3: one strongly-connected group at a time."""
        self.level += 1
        placeholders = {}
        for decl in decls:
            tv = self.fresh()
            placeholders[decl.name] = tv
            self.env.define(decl.name, Binding(mono(tv), False))
        for decl in decls:
            self.solver.eq(placeholders[decl.name], self.infer_function(decl), decl.span)
        self.level -= 1
        # Everything the bodies deferred gets one more chance now that the
        # whole group has been generated, then is split: general enough to
        # quantify travels in the scheme, the rest waits for an enclosing
        # binding.
        self.solver.settle()
        retained = self.solver.retained(self.level)
        types = [placeholders[decl.name] for decl in decls]
        for decl, ty in zip(decls, types):
            # A `fun` is syntactically a value, so the value restriction never
            # blocks generalization here.
            self.env.define(
                decl.name,
                Binding(generalize(ty, self.level, _constrain(retained, ty)), False),
            )
        self._reject_stranded(_unattributed(retained, types))

    # -- functions ---------------------------------------------------------

    def infer_function(self, decl: ast.FunDecl | ast.ELambda) -> TFun:
        self.tyvar_scopes.append({})
        with self._scoped(self.env.child()):
            param_types: list[Type] = []
            for param in decl.params:
                tv = self.fresh()
                self.bind_pattern(param, tv, mutable=False)
                param_types.append(tv)

            ret = self.fresh()
            if decl.ret is not None:
                self.solver.eq(ret, self.type_of(decl.ret), decl.span, "the return type annotation")

            self.fn_stack.append(ret)
            loops, self.loop_stack = self.loop_stack, []  # `break` cannot cross a function
            body = self.infer_expr(decl.body)
            self.loop_stack = loops
            self.fn_stack.pop()

            # A body of type bottom leaves `ret` to be fixed by the `return`s.
            self.solver.eq(ret, body, decl.span, "the function body")
        self.tyvar_scopes.pop()
        return TFun(param_types, ret)

    # -- patterns ----------------------------------------------------------

    def bind_pattern(
        self,
        pat: ast.Pattern,
        t: Type,
        mutable: bool,
        gen: bool = False,
        preds: list[CPred] | None = None,
    ) -> None:
        for name, ty in self.match_pattern(pat, t).items():
            # `let (f, g) = ...` binds two schemes from one right-hand side, so
            # a retained predicate goes to whichever of them it constrains.
            scheme = (generalize(ty, self.level, _constrain(preds or [], ty))
                      if gen else mono(ty))
            self.env.define(name, Binding(scheme, mutable))

    def match_pattern(self, pat: ast.Pattern, t: Type) -> dict[str, Type]:
        """Unify a pattern against the type it scrutinizes, returning its binders."""
        if isinstance(pat, ast.PWild):
            return {}
        if isinstance(pat, ast.PVar):
            return {pat.name: t}
        if isinstance(pat, ast.PLit):
            self.solver.eq(t, LITERAL_TYPES[pat.kind], pat.span, "a literal pattern")
            return {}
        if isinstance(pat, ast.PAnnot):
            self.solver.eq(t, self.type_of(pat.type_expr), pat.span, "a pattern annotation")
            return self.match_pattern(pat.pat, t)
        if isinstance(pat, ast.PTuple):
            elems = [self.fresh() for _ in pat.elems]
            self.solver.eq(t, TTuple(elems), pat.span, "a tuple pattern")
            out: dict[str, Type] = {}
            for sub, ty in zip(pat.elems, elems):
                self._merge(out, self.match_pattern(sub, ty), pat.span)
            return out
        if isinstance(pat, ast.PCon):
            con = self.decls.instantiate_con(pat.name, self.level, pat.span)
            info = self.decls.con(pat.name)
            if len(pat.args) != len(con.params):
                raise TypeError_(
                    f"constructor '{pat.name}' takes {len(con.params)} argument(s), "
                    f"but the pattern supplies {len(pat.args)}",
                    pat.span,
                )
            if info.is_record and pat.args:
                raise TypeError_(
                    f"'{pat.name}' has named fields; match it with "
                    f"'{pat.name} {{ ... }}'",
                    pat.span,
                )
            self.solver.eq(t, con.ret, pat.span, f"the pattern '{pat.name}'")
            out = {}
            for sub, ty in zip(pat.args, con.params):
                self._merge(out, self.match_pattern(sub, ty), pat.span)
            return out
        if isinstance(pat, ast.PRecord):
            con = self.decls.instantiate_con(pat.name, self.level, pat.span)
            info = self.decls.con(pat.name)
            if not info.is_record:
                raise TypeError_(
                    f"constructor '{pat.name}' has positional arguments, not fields",
                    pat.span,
                )
            self.solver.eq(t, con.ret, pat.span, f"the pattern '{pat.name}'")
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

    def infer_stmt(self, stmt: ast.Stmt) -> Type:
        """Infer a statement, returning the value it contributes to its block."""
        if isinstance(stmt, ast.SExpr):
            return self.infer_expr(stmt.expr)

        if isinstance(stmt, (ast.SLet, ast.SVar)):
            is_let = isinstance(stmt, ast.SLet)
            self.level += 1
            value = self.infer_expr(stmt.value)
            self.level -= 1
            # Section 4.4: only a `let` bound to a syntactic value generalizes.
            gen = is_let and self.is_nonexpansive(stmt.value)
            retained: list[CPred] = []
            if gen:
                self.solver.settle()
                retained = self.solver.retained(self.level)
                self._reject_stranded(_unattributed(retained, [value]))
            else:
                # The right-hand side was inferred one level in, so its
                # variables are still marked deeper than this binding. Bring
                # them back down or the next binding to mention them will
                # generalize what the value restriction just refused to. This
                # lowers any deferred predicate over those variables with them,
                # so it stays behind for an enclosing binding as well.
                lower_to(value, self.level)
            self.bind_pattern(stmt.pat, value, mutable=not is_let, gen=gen,
                              preds=retained)
            return UNIT

        if isinstance(stmt, ast.SFun):
            self._infer_fun_group([stmt.decl])
            return UNIT

        if isinstance(stmt, ast.SAssign):
            self.infer_assign(stmt)
            return UNIT

        raise AssertionError(f"unhandled statement {type(stmt).__name__}")

    def infer_assign(self, stmt: ast.SAssign) -> None:
        target, span = stmt.target, stmt.span

        if isinstance(target, ast.EVar):
            binding = self.env.lookup(target.name)
            if binding is None:
                raise TypeError_(f"'{target.name}' is not defined", span)
            if not binding.mutable:
                raise TypeError_(
                    f"cannot assign to '{target.name}': it was bound with 'let'. "
                    f"Use 'var' to make it reassignable.",
                    span,
                )
            self.solver.eq(
                self.use(binding.scheme, span),
                self.infer_expr(stmt.value),
                span,
                f"the assignment to '{target.name}'",
            )
            return

        if isinstance(target, ast.EField):
            field = self.infer_field(target, assigning=True)
            self.solver.eq(field, self.infer_expr(stmt.value), span, "the assigned value")
            return

        if isinstance(target, ast.EIndex):
            element = self.fresh()
            self.solver.eq(
                self.infer_expr(target.arr), TCon("Array", [element]), span,
                "an indexed assignment",
            )
            self.solver.eq(self.infer_expr(target.index), INT, span, "an array index")
            self.solver.eq(element, self.infer_expr(stmt.value), span, "the assigned value")
            return

        raise AssertionError("parser should have rejected this assignment target")

    # -- expressions -------------------------------------------------------

    def infer_expr(self, e: ast.Expr) -> Type:
        method = getattr(self, "_infer_" + type(e).__name__, None)
        if method is None:
            raise AssertionError(f"unhandled expression {type(e).__name__}")
        return method(e)

    def _infer_ELit(self, e: ast.ELit) -> Type:
        return LITERAL_TYPES[e.kind]

    def _infer_EUnit(self, e: ast.EUnit) -> Type:
        return UNIT

    def _infer_EVar(self, e: ast.EVar) -> Type:
        binding = self.env.lookup(e.name)
        if binding is None:
            raise TypeError_(f"'{e.name}' is not defined", e.span)
        return self.use(binding.scheme, e.span)

    def _infer_ECon(self, e: ast.ECon) -> Type:
        con = self.decls.instantiate_con(e.name, self.level, e.span)
        # A nullary constructor is a value; anything else is a function.
        return con.ret if not con.params else con

    def _infer_ETuple(self, e: ast.ETuple) -> Type:
        return TTuple([self.infer_expr(x) for x in e.elems])

    def _infer_EArray(self, e: ast.EArray) -> Type:
        element: Type = self.fresh()
        for item in e.elems:
            element = self.solver.join(element, self.infer_expr(item), item.span, "an array literal")
        return TCon("Array", [element])

    def _infer_ERecord(self, e: ast.ERecord) -> Type:
        con = self.decls.instantiate_con(e.con, self.level, e.span)
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
            self.solver.eq(
                con.params[index], self.infer_expr(value), value.span,
                f"field '{label}' of '{e.con}'",
            )
        return con.ret

    def _infer_ELambda(self, e: ast.ELambda) -> Type:
        return self.infer_function(e)

    def _infer_ECall(self, e: ast.ECall) -> Type:
        fn = self.infer_expr(e.fn)
        args = [self.infer_expr(a) for a in e.args]
        result = self.fresh()
        self.solver.eq(fn, TFun(args, result), e.span, "a function call")
        return result

    def _infer_EIndex(self, e: ast.EIndex) -> Type:
        element = self.fresh()
        self.solver.eq(self.infer_expr(e.arr), TCon("Array", [element]), e.span, "an index")
        self.solver.eq(self.infer_expr(e.index), INT, e.index.span, "an array index")
        return element

    def _infer_EField(self, e: ast.EField) -> Type:
        return self.infer_field(e, assigning=False)

    def infer_field(self, e: ast.EField, assigning: bool) -> Type:
        """Emit `HasField "f" r a` for `r.f` and hand back the `a`.

        Nothing is decided here. If the receiver is already known the solver
        discharges the predicate immediately and the result is unified with the
        declared field type; if it is not, the predicate waits. That is the
        whole of SPEC-DELTAS.md entry 7: the old code pruned the receiver and
        demanded a record on the spot, which made `a.length` mean different
        things depending on whether the walk had reached `a[0]` yet.
        """
        receiver = self.infer_expr(e.obj)
        result = self.fresh()
        self.solver.pred(
            Pred(HAS_FIELD, [TLabel(e.name), receiver, result]),
            e.span,
            "mutate" if assigning else "read",
        )
        return result

    def _infer_EUnary(self, e: ast.EUnary) -> Type:
        operand, result = UNARY_OPS[e.op]
        self.solver.eq(self.infer_expr(e.operand), operand, e.span, f"the operand of '{e.op}'")
        return result

    def _infer_EBinary(self, e: ast.EBinary) -> Type:
        left, right, result = BINARY_OPS[e.op]
        self.solver.eq(self.infer_expr(e.left), left, e.left.span, f"the left operand of '{e.op}'")
        self.solver.eq(self.infer_expr(e.right), right, e.right.span, f"the right operand of '{e.op}'")
        return result

    def _infer_EAnnot(self, e: ast.EAnnot) -> Type:
        annotated = self.type_of(e.type_expr)
        return self.solver.join(self.infer_expr(e.expr), annotated, e.span, "a type annotation")

    def _infer_EBlock(self, e: ast.EBlock) -> Type:
        with self._scoped(self.env.child()):
            # Section 6.8: the block's value is its last statement's, and a
            # declaration contributes Unit.
            result: Type = UNIT
            for stmt in e.stmts:
                result = self.infer_stmt(stmt)
        return result

    def _infer_EIf(self, e: ast.EIf) -> Type:
        self.solver.eq(self.infer_expr(e.cond), BOOL, e.cond.span, "an 'if' condition")
        then = self.infer_expr(e.then)
        if e.otherwise is None:
            return UNIT  # section 6.7: statement-style `if` has no value
        return self.solver.join(then, self.infer_expr(e.otherwise), e.span, "the branches of an 'if'")

    def _infer_EWhile(self, e: ast.EWhile) -> Type:
        self.solver.eq(self.infer_expr(e.cond), BOOL, e.cond.span, "a 'while' condition")
        self.loop_stack.append(LoopCtx("while", UNIT))
        self.infer_expr(e.body)
        self.loop_stack.pop()
        return UNIT

    def _infer_EForIn(self, e: ast.EForIn) -> Type:
        element = self.fresh()
        self.solver.eq(
            self.infer_expr(e.iterable), TCon("Array", [element]), e.iterable.span,
            "the sequence of a 'for ... in' loop",
        )
        with self._scoped(self.env.child()):
            # Section 6.5: `x` is a fresh immutable binding each iteration.
            self.bind_pattern(e.pat, element, mutable=False)
            self.loop_stack.append(LoopCtx("for", UNIT))
            self.infer_expr(e.body)
            self.loop_stack.pop()
        return UNIT

    def _infer_EForC(self, e: ast.EForC) -> Type:
        with self._scoped(self.env.child()):
            if e.init is not None:
                self.infer_stmt(e.init)
            self.solver.eq(self.infer_expr(e.cond), BOOL, e.cond.span, "a 'for' condition")
            self.loop_stack.append(LoopCtx("for", UNIT))
            self.infer_expr(e.body)
            if e.step is not None:
                self.infer_stmt(e.step)
            self.loop_stack.pop()
        return UNIT

    def _infer_ELoop(self, e: ast.ELoop) -> Type:
        ctx = LoopCtx("loop", self.fresh())
        self.loop_stack.append(ctx)
        self.infer_expr(e.body)
        self.loop_stack.pop()
        # Section 6.7: a `loop` never falls through, so its value comes only
        # from its breaks. With no break at all it never produces one.
        return ctx.result if ctx.saw_break else BOTTOM

    def _infer_EMatch(self, e: ast.EMatch) -> Type:
        scrutinee = self.infer_expr(e.scrutinee)
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
                    self.solver.eq(bindings[name], ty, alt.span, f"the binding '{name}'")
            with self._scoped(self.env.child()):
                for name, ty in bindings.items():
                    self.env.define(name, Binding(mono(ty), False))
                body = self.infer_expr(arm.body)
            result = self.solver.join(result, body, arm.span, "the arms of a 'match'")
        return result

    def _infer_EReturn(self, e: ast.EReturn) -> Type:
        if not self.fn_stack:
            raise TypeError_("'return' is only valid inside a function", e.span)
        value = self.infer_expr(e.value) if e.value is not None else UNIT
        self.solver.eq(self.fn_stack[-1], value, e.span, "a 'return'")
        return BOTTOM

    def _infer_EBreak(self, e: ast.EBreak) -> Type:
        if not self.loop_stack:
            raise TypeError_("'break' is only valid inside a loop", e.span)
        ctx = self.loop_stack[-1]
        ctx.saw_break = True
        if e.value is None:
            self.solver.eq(ctx.result, UNIT, e.span, "a valueless 'break'")
        elif ctx.kind != "loop":
            raise TypeError_(
                f"'break' cannot carry a value out of a '{ctx.kind}' loop; only "
                f"'loop' produces one",
                e.span,
            )
        else:
            ctx.result = self.solver.join(ctx.result, self.infer_expr(e.value), e.span, "a 'break'")
        return BOTTOM

    def _infer_EContinue(self, e: ast.EContinue) -> Type:
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


def _reach(preds: list[CPred], types: list[Type]) -> set[int]:
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
    ids = _reach(preds, [t])
    return [c.pred for c in preds if _touches(c, ids)]


def _unattributed(preds: list[CPred], types: list[Type]) -> list[CPred]:
    """The retained predicates no scheme in the group will carry."""
    ids = _reach(preds, types)
    return [c for c in preds if not _touches(c, ids)]


def _touches(c: CPred, ids: set[int]) -> bool:
    return any(v.id in ids for v in vars_of(*c.pred.args))


class _Restore:
    """Restore `inferencer.env` when the `with` block ends."""

    def __init__(self, inferencer: Inferencer, previous: Env):
        self.inferencer = inferencer
        self.previous = previous

    def __enter__(self):
        return self.inferencer.env

    def __exit__(self, *exc):
        self.inferencer.env = self.previous
        return False
