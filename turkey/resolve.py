"""Name resolution across modules (design.md section 9).

Every stage after this one keys on a flat string: `Env`, `REnv`, `DeclTable`
and the evaluator's globals are all `dict[str, ...]`. So a module system needs
either *n* of each of those tables, threaded through every stage, or one table
and names that are unique across the program. This module takes the second
road: a module's top-level bindings are renamed to `Module.name`, and every
reference to one is rewritten to match, before generation ever runs. Nothing
downstream learns that modules exist.

Types and constructors go the same way (delta 43), in their own namespaces:
`type Point` in module `M` becomes `M#Point`, and so does its constructor. That
is what lets two libraries that each declare a `Node` be used in one program --
the two are different `TCon`s, and unification, which compares names, can tell
them apart. What a reader sees is unchanged, because the printer shows a
constructor's short name (`turkey/decls.py` decides which).

Three kinds of name are deliberately left alone.

* **Locals.** A parameter, a `let`, a pattern variable -- these never leave the
  scope that binds them, so they need no qualification, and the resolver tracks
  scopes precisely enough to tell one from a top-level reference.
* **Class methods, and classes themselves.** `add` is a method of `Add`, and
  classes and instances are global (see SPEC-DELTAS.md entry 41). A method name
  means the same thing in every module, so qualifying it would only make the
  same name look like several. This is also what lets a module define its own
  `add`: the ordinary binding becomes `Main#add` and the method stays `add`, so
  the two coexist.
* **Operators.** `a + b` desugared to a plain `EVar("add")` at parse time, and
  once shadowing an import is legal that node would resolve to whatever `add`
  the module happens to define. The parser now marks the node (`EVar.method`),
  and the resolver skips it -- so `+` still means `Add.add` in a module whose
  own `add` concatenates strings.

A qualified name whose module part is not in scope is reported here, because
this is the only stage that knows what "in scope" means. An *unqualified* name
that is not found is left alone for generation to report, so the existing
`'x' is not defined` keeps its wording and its span.
"""

from __future__ import annotations

from . import ast
from .deps import pattern_vars
from .errors import TypeError_


class Resolver:
    """Rewrites one module's AST in place against a surface-name scope."""

    def __init__(self, scope):
        self.scope = scope.values
        self.types = scope.types
        self.cons = scope.cons
        self.locals: list[set[str]] = []

    # -- types and constructors --------------------------------------------

    def tycon(self, name: str) -> str:
        return self.types.get(name, name)

    def con(self, name: str) -> str:
        return self.cons.get(name, name)

    def type_expr(self, te) -> None:
        t = type(te)
        if t is ast.TECon:
            te.name = self.tycon(te.name)
            for arg in te.args:
                self.type_expr(arg)
        elif t is ast.TEApp:
            self.type_expr(te.fn)
            for arg in te.args:
                self.type_expr(arg)
        elif t is ast.TETuple:
            for elem in te.elems:
                self.type_expr(elem)
        elif t is ast.TEFun:
            for param in te.params:
                self.type_expr(param)
            self.type_expr(te.ret)
        # TEVar names a type variable, which is scoped to its own declaration.

    def context(self, preds) -> None:
        """A `[...]` context, a superclass list, an instance's context. Class
        names stay as written; the types they constrain do not."""
        for pred in preds:
            if isinstance(pred, ast.EqPred):
                self.type_expr(pred.left)
                self.type_expr(pred.right)
            else:
                self.type_expr(pred.arg)

    def pattern(self, pat) -> None:
        """Rename the constructors a pattern mentions. Its *variables* are
        binders, and `pattern_vars` collects them separately."""
        t = type(pat)
        if t is ast.PCon:
            pat.name = self.con(pat.name)
            for arg in pat.args:
                self.pattern(arg)
        elif t is ast.PRecord:
            pat.name = self.con(pat.name)
            for _label, sub in pat.fields:
                self.pattern(sub)
        elif t is ast.PTuple:
            for elem in pat.elems:
                self.pattern(elem)
        elif t is ast.PAnnot:
            self.pattern(pat.pat)
            self.type_expr(pat.type_expr)

    # -- scope helpers -----------------------------------------------------

    def _is_local(self, name: str) -> bool:
        return any(name in frame for frame in self.locals)

    def _push(self, names: set[str] = frozenset()) -> None:
        self.locals.append(set(names))

    def _pop(self) -> None:
        self.locals.pop()

    def _bind(self, names) -> None:
        self.locals[-1] |= set(names)

    # -- entry point -------------------------------------------------------

    def program(self, program: ast.Program) -> None:
        for decl in program.decls:
            if isinstance(decl, ast.Stmt):
                self.top_level(decl)
            elif isinstance(decl, ast.TypeDecl):
                decl.name = self.tycon(decl.name)
                if decl.alias is not None:
                    self.type_expr(decl.alias)
                for variant in decl.variants or []:
                    variant.name = self.con(variant.name)
                    for arg in variant.args:
                        self.type_expr(arg)
                    for _label, te in variant.fields or []:
                        self.type_expr(te)
            elif isinstance(decl, ast.ClassDecl):
                self.context(decl.supers)
                for method in decl.methods:
                    self.fun(method)
            elif isinstance(decl, ast.InstanceDecl):
                self.type_expr(decl.head)
                self.context(decl.context)
                for bind in decl.families:
                    self.type_expr(bind.body)
                for method in decl.methods:
                    self.fun(method)

    def top_level(self, stmt: ast.Stmt) -> None:
        """A top-level item: its binders are renamed, its body resolved."""
        if isinstance(stmt, ast.SFun):
            stmt.decl.name = self.scope.get(stmt.decl.name, stmt.decl.name)
            self.fun(stmt.decl)
        elif isinstance(stmt, (ast.SLet, ast.SVar)):
            self.expr(stmt.value)
            self.pattern(stmt.pat)
            rename_pattern(stmt.pat, self.scope)
        else:
            # An assignment or a bare expression at the top level binds
            # nothing, so there is nothing to rename -- only to resolve.
            self._push()
            self.stmt(stmt)
            self._pop()

    def fun(self, decl: ast.FunDecl) -> None:
        self.context(decl.context)
        for param in decl.params:
            self.pattern(param)
        if decl.ret is not None:
            self.type_expr(decl.ret)
        self._push(_params(decl.params))
        if decl.body is not None:
            self.expr(decl.body)
        self._pop()

    # -- expressions -------------------------------------------------------

    def expr(self, e) -> None:
        t = type(e)

        if t is ast.EVar:
            # `e.method` is the parser's own node for an operator or a `for`
            # loop's `iter`/`next`; it means the class method by construction.
            if e.method or self._is_local(e.name):
                return
            internal = self.scope.get(e.name)
            if internal is not None:
                e.name = internal
            elif "." in e.name:
                # Qualified, and its module is not in scope. Generation would
                # find it anyway -- `Prim.intAdd` is in the shared environment
                # so that the Prelude can be checked -- so this is the stage
                # that has to say no.
                raise TypeError_(f"'{e.name}' is not defined", e.span)
            return

        if t is ast.ECon:
            e.name = self.con(e.name)
            return
        if t in (ast.ELit, ast.EUnit, ast.EContinue):
            return
        if t is ast.ETuple or t is ast.EArray:
            for elem in e.elems:
                self.expr(elem)
            return
        if t is ast.ERecord:
            e.con = self.con(e.con)
            for _label, value in e.fields:
                self.expr(value)
            return
        if t is ast.ECall:
            self.expr(e.fn)
            for arg in e.args:
                self.expr(arg)
            return
        if t is ast.EIndex:
            self.expr(e.arr)
            self.expr(e.index)
            return
        if t is ast.EField:
            self.expr(e.obj)
            return
        if t is ast.EUnary:
            self.expr(e.operand)
            return
        if t is ast.EBinary:
            self.expr(e.left)
            self.expr(e.right)
            return
        if t is ast.EAnnot:
            self.expr(e.expr)
            self.type_expr(e.type_expr)
            return
        if t is ast.EIf:
            self.expr(e.cond)
            self.expr(e.then)
            if e.otherwise is not None:
                self.expr(e.otherwise)
            return
        if t is ast.EWhile:
            self.expr(e.cond)
            self.expr(e.body)
            return
        if t is ast.ELoop:
            self.expr(e.body)
            return
        if t is ast.EReturn or t is ast.EBreak:
            if e.value is not None:
                self.expr(e.value)
            return
        if t is ast.ELambda:
            for param in e.params:
                self.pattern(param)
            if e.ret is not None:
                self.type_expr(e.ret)
            self._push(_params(e.params))
            self.expr(e.body)
            self._pop()
            return
        if t is ast.EMatch:
            self.expr(e.scrutinee)
            for arm in e.arms:
                bound: set[str] = set()
                for pat in arm.patterns:
                    self.pattern(pat)
                    bound |= pattern_vars(pat)
                self._push(bound)
                self.expr(arm.body)
                self._pop()
            return
        if t is ast.EForIn:
            self.expr(e.iterable)
            self.pattern(e.pat)
            self._push(pattern_vars(e.pat))
            self.expr(e.body)
            self._pop()
            return
        if t is ast.EForC:
            # init, cond, step and body share one scope, as in `deps`.
            self._push()
            if e.init is not None:
                self.stmt(e.init)
            self.expr(e.cond)
            if e.step is not None:
                self.stmt(e.step)
            self.expr(e.body)
            self._pop()
            return
        if t is ast.EBlock:
            self._push()
            for stmt in e.stmts:
                self.stmt(stmt)
            self._pop()
            return

        raise AssertionError(f"resolve: unrecognized expression node {t.__name__}")

    # -- statements --------------------------------------------------------

    def stmt(self, s: ast.Stmt) -> None:
        t = type(s)
        if t is ast.SLet or t is ast.SVar:
            self.expr(s.value)
            self.pattern(s.pat)
            self._bind(pattern_vars(s.pat))
            return
        if t is ast.SFun:
            # In scope for its own body, so it may recurse.
            self._bind({s.decl.name})
            self.fun(s.decl)
            return
        if t is ast.SAssign:
            self.expr(s.target)
            self.expr(s.value)
            return
        if t is ast.SExpr:
            self.expr(s.expr)
            return
        raise AssertionError(f"resolve: unrecognized statement node {t.__name__}")


def rename_pattern(pat: ast.Pattern, scope: dict[str, str]) -> None:
    """Rewrite a top-level binder's variables to their internal names."""
    t = type(pat)
    if t is ast.PVar:
        pat.name = scope.get(pat.name, pat.name)
    elif t is ast.PCon or t is ast.PTuple:
        for sub in (pat.args if t is ast.PCon else pat.elems):
            rename_pattern(sub, scope)
    elif t is ast.PRecord:
        for _label, sub in pat.fields:
            rename_pattern(sub, scope)
    elif t is ast.PAnnot:
        rename_pattern(pat.pat, scope)


def _params(params) -> set[str]:
    out: set[str] = set()
    for p in params:
        out |= pattern_vars(p)
    return out
