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

Variables invented here have no rank: generation has no idea what binder depth
it is under, which is the point. Each is recorded in the enclosing frame and
becomes part of a `CExists`, and the solver stamps the rank when it gets there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from . import ast
from .constraints import (
    HAS_FIELD, ONE_OF, Binding, CAnd, CDef, CEq, CExists, CInstance, CLet,
    CPred, Constraint, Env,
)
from .decls import DeclTable
from .deps import free_names, pattern_vars, sccs
from .errors import Span, TypeError_
from .types import (
    BOOL, BOTTOM, CHAR, FLOAT, INT, STRING, UNIT, Pred, TBottom, TCon, TFun,
    TLabel, TSet, TTuple, TVar, Type, decimal_set, integral_set,
)

LITERAL_TYPES = {"Int": INT, "Float": FLOAT, "String": STRING, "Char": CHAR, "Bool": BOOL}

# Which literals get a set rather than a type. A string or a char has exactly
# one type and is emitted as one; only the numeric kinds are open.
NUMERIC_KINDS = frozenset({"Int", "Float"})

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
    def __init__(self, decls: DeclTable, builtins: Env):
        self.decls = decls
        self.frames: list[Frame] = [Frame()]
        # Name -> mutable. Types are the solver's business; this is only what
        # generation needs to reject an undefined name or a write to a `let`.
        self.scopes: list[dict[str, bool]] = [_names_of(builtins)]
        self.fn_stack: list[Type] = []
        self.loop_stack: list[LoopCtx] = []
        self.tyvar_scopes: list[dict[str, TVar]] = [{}]
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
        return self.decls.to_type(te, self.tyvar_scopes[-1], self.fresh)

    # -- program -----------------------------------------------------------

    def generate(self, program: ast.Program) -> tuple[list[ast.Stmt], Constraint]:
        """Build the whole program's constraint, and its evaluation order.

        The evaluator reuses that order, so a binding is always initialized
        before anything that reads it. The constraint has the same shape: one
        `CLet` per binding group, nested so that later groups are solved in the
        scope of earlier ones.
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

        nest(0)
        return ordered, self.pop()

    def check_exhaustiveness(self) -> None:
        """Section 5.1: a non-exhaustive match is a warning, not an error --
        reaching an unhandled case is a runtime panic.

        Runs after solving, since it needs the scrutinee types resolved.
        """
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
        binds, defn, generalizes, mutable = self.build_definition(group)

        self.scopes.append({name: mutable for name, _ in binds})
        self.push()
        try:
            out = rest()
        finally:
            body = self.pop()
            self.scopes.pop()

        if generalizes:
            self.emit(CLet(binds, defn, body, group[0].span, top_level))
        else:
            # No generalization means no new rank, so the definition is solved
            # right here and only the names are scoped. Nothing needs its rank
            # lowered afterwards, because nothing ever raised it.
            self.emit(CAnd([defn, CDef(binds, body, top_level)]))
        return out

    def build_definition(
        self, group: list[ast.Stmt]
    ) -> tuple[list[tuple[str, Type]], Constraint, bool, bool]:
        """The bound names, the definition's constraint, and how it binds."""
        if all(isinstance(item, ast.SFun) for item in group):
            binds, defn = self.build_fun_group([item.decl for item in group])
            # A `fun` is syntactically a value, so the value restriction never
            # blocks generalization here.
            return binds, defn, True, False

        assert len(group) == 1, "only functions may share a binding group"
        stmt = group[0]
        assert isinstance(stmt, (ast.SLet, ast.SVar))
        self.push()
        value = self.gen_expr(stmt.value)
        binds = list(self.match_pattern(stmt.pat, value).items())
        defn = self.pop()
        # Section 4.4: only a `let` bound to a syntactic value generalizes.
        generalizes = isinstance(stmt, ast.SLet) and self.is_nonexpansive(stmt.value)
        return binds, defn, generalizes, isinstance(stmt, ast.SVar)

    def build_fun_group(
        self, decls: list[ast.FunDecl]
    ) -> tuple[list[tuple[str, Type]], Constraint]:
        """Section 5.2 step 3: one strongly-connected group at a time.

        The placeholders are bound *monomorphically* inside the definition, by
        the inner `CDef`, which is what keeps polymorphic recursion out. The
        enclosing `CLet` generalizes them only once every body has been solved.
        """
        self.push()
        binds = [(decl.name, self.fresh()) for decl in decls]
        self.scopes.append({name: False for name, _ in binds})
        self.push()
        for decl, (_, placeholder) in zip(decls, binds):
            self.eq(placeholder, self.gen_function(decl), decl.span)
        inner = self.pop()
        self.scopes.pop()
        self.emit(CDef(binds, inner))
        return binds, self.pop()

    def gen_function(self, decl: ast.FunDecl | ast.ELambda) -> TFun:
        """A function's own constraint. Parameters bind monomorphically.

        No rank is pushed: a lambda is not a generalization point, so its
        parameters live at whatever rank the enclosing binder established.
        """
        self.tyvar_scopes.append({})
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

        self.scopes.append({name: False for name in binds})
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
            con = self.decls.instantiate_con(pat.name, self.fresh, pat.span)
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
                self.gen_expr(target.arr), TCon("Array", [element]), span,
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
        return method(e)

    def use(self, name: str, span: Span | None) -> Type:
        """`name <= t` for a fresh `t`. No lookup: that is the solver's job."""
        t = self.fresh()
        self.emit(CInstance(name, t, span))
        return t

    def _gen_ELit(self, e: ast.ELit) -> Type:
        if e.kind in NUMERIC_KINDS:
            return self.numeric(e.kind, e.value, e.span, "")
        return LITERAL_TYPES[e.kind]

    def numeric(self, kind: str, value, span: Span | None, context: str) -> Type:
        """A fresh variable, plus the set of types the literal could have.

        A numeric literal is not given a type here. Which type it has is a
        decision, and decisions belong to the solver -- the set is all the
        syntax actually determines. Today every set is a singleton, so the
        solver turns each one straight back into the equation this used to
        emit; the shape is what lets a wider tower arrive without touching
        this file.
        """
        t = self.fresh()
        names = integral_set(value) if kind == "Int" else decimal_set()
        self.emit(CPred(Pred(ONE_OF, [t, TSet(names)]), span, context))
        return t

    def _gen_EUnit(self, e: ast.EUnit) -> Type:
        return UNIT

    def _gen_EVar(self, e: ast.EVar) -> Type:
        if not self.bound(e.name):
            raise TypeError_(f"'{e.name}' is not defined", e.span)
        return self.use(e.name, e.span)

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
        return TCon("Array", [element])

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
        self.eq(self.gen_expr(e.arr), TCon("Array", [element]), e.span, "an index")
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
        operand, result = UNARY_OPS[e.op]
        self.eq(self.gen_expr(e.operand), operand, e.span, f"the operand of '{e.op}'")
        return result

    def _gen_EBinary(self, e: ast.EBinary) -> Type:
        left, right, result = BINARY_OPS[e.op]
        self.eq(self.gen_expr(e.left), left, e.left.span, f"the left operand of '{e.op}'")
        self.eq(self.gen_expr(e.right), right, e.right.span, f"the right operand of '{e.op}'")
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
        element = self.fresh()
        self.eq(
            self.gen_expr(e.iterable), TCon("Array", [element]), e.iterable.span,
            "the sequence of a 'for ... in' loop",
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
