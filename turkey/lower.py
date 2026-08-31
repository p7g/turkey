"""Surface AST plus what solving decided, down to `turkey/core.py`.

This is the pass that makes the elaboration a *tree*. Everything it needs was
already computed and is currently scattered across three side channels and an
instance table:

* `EVar.use` -- an `evidence.Use`: which dictionaries this occurrence needs,
  and (delta 48) which types it instantiated its scheme at.
* `FunDecl.dicts` / `SLet.dicts` -- an `evidence.Abstraction`: the leading
  dictionary parameters a binding group gained, and in which order.
* `InstInfo.plan` -- an `evidence.InstancePlan`: an instance's methods, its
  superclass evidence, and the context it takes.
* `typed.TypeTable` -- every expression's type.

None of that is rediscovered here. What changes is that it stops being an
agreement between the elaborator and the evaluator, and becomes syntax that
`turkey/coretc.py` can reject.

## The three translations that carry the milestone

**A class becomes a record type.** `class C a` with methods `m1..mk` and
superclasses `S1..Sj` gives `%Dict.C a`, with a field per method and a field
`%super.Sn` per superclass. It is opaque -- no program can write `%Dict.C`, `%`
being illegal in an identifier -- and it exists so that a dictionary has a type
to be wrong about.

**An instance becomes a top-level binding** of that type, named
`%inst.C.Con`, which is unique because instances are coherent (delta 43). An
instance with a context becomes a *function* from dictionaries to a dictionary,
which is what `instance [Show a] Show (Array a)` always meant; the evaluator
built it on demand and memoised it on object identity, and here it is an
ordinary binding that an ordinary application uses.

**Evidence becomes a term.** `FromDict(name, path)` is a variable and a chain
of superclass projections; `FromInstance(inst, args)` is a variable applied to
the evidence for the instance's own context. The `Evidence` language does not
survive this pass, which is the point: it was a second, unchecked term language
that only the evaluator understood.

## What a use site becomes

A use of a polymorphic name is a type application and then a value application:

    f : forall a. %Dict.Show a -> fun(a) -> String
    f(x)  ==>  (f[Int])(%d.Show.Int)(x)

in that order, and the order is not a convention this pass invents -- it is the
order the two were abstracted in. A method use is the same with the function
reached by projection instead of by name: `show` is `%d.Show.Int.show`.

## Types on the nodes, and one thing the checker is left to do itself

Every node gets `TypeTable.of`, which after delta 48 is a real type: family
applications reduced throughout, numeric literals decided. The exception is a
node this pass *creates* -- a projection, a type application, a reference cell
-- whose type it computes, and computing it is where a lowering could be
wrong. That is exactly what `coretc.py` re-derives rather than believes.

Patterns are the other half of that: a `CAlt` records no binding types, so the
checker derives them from the scrutinee and the constructor's declaration. A
pattern that does not fit is then a rejected term rather than an unspoken
agreement.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from . import ast, core, prelude
from .classes import ClassTable, InstInfo, match
from .core import (
    CAlt, CApp, CArray, CAssign, CBind, CBreak, CCon, CContinue, CDeref, CExpr,
    CField, CForC, CForIn, CIf, CIndex, CLam, CLet, CLetRec, CLit, CLoop,
    CMatch, CParam, CProgram, CRecord, CRef, CReturn, CTuple, CTyApp, CTyLam,
    CUnit, CVar, CWhile, ref_of,
)
from .decls import DeclTable, substitute
from .errors import Span
from .evidence import (
    Absent, Evidence, FromDict, FromInstance, InstancePlan, MethodImpl, Use,
)
from .types import (
    BOOL, EQUALS, KFun, STAR, TApp, TCon, TFam, TFun, TTuple, TVar, Type,
    prune,
)

_counter = itertools.count()


def fresh_name(hint: str) -> str:
    """A binder this pass invents. `%` cannot start a source identifier."""
    return f"%{hint}{next(_counter)}"


def dict_con(cls: str, kind) -> TCon:
    return TCon(f"%Dict.{cls}", KFun(kind, STAR))


def super_field(cls: str) -> str:
    return f"%super.{cls}"


def inst_name(inst: InstInfo) -> str:
    return f"%inst.{inst.cls}.{inst.con}"


class _Rigid:
    """Sets the lowerer's skolem map for the length of one body.

    `None` means "this body says nothing about skolems", which leaves the map
    alone rather than emptying it. The distinction matters: a *method* declared
    inside an instance has no signature of its own, so the signature path finds
    nothing to say -- and must not thereby erase what the instance said.
    """

    def __init__(self, owner: "Lowerer", rigid: dict[str, TVar] | None) -> None:
        self.owner = owner
        self.rigid = rigid
        self.saved: dict[str, TVar] = {}

    def __enter__(self) -> None:
        self.saved = self.owner.rigid
        if self.rigid is not None:
            self.owner.rigid = self.rigid

    def __exit__(self, *_) -> None:
        self.owner.rigid = self.saved


@dataclass
class Scope:
    """Which names in scope are reference cells.

    A `var` is one, and so is a parameter that something assigns -- parameters
    are reassignable (delta 35), but a parameter nothing writes is
    indistinguishable from a `let`, and making a cell for it anyway would put
    an indirection on every argument of every function for no observable gain.
    """

    cells: set[str]
    # Name -> what it abstracts over, for the local bindings in scope. A
    # *recursive* occurrence needs this and nothing else does: inference binds
    # a name monomorphically inside its own definition, so `size(l)` within
    # `size` records no type arguments -- correctly, there is nothing yet to
    # record. System-F still wants the application, and at the binding's own
    # variables, which is what this remembers.
    binders: dict[str, list[TVar]] = field(default_factory=dict)

    def child(self) -> "Scope":
        return Scope(set(self.cells), dict(self.binders))


class Lowerer:
    def __init__(self, decls: DeclTable, classes: ClassTable, env, types) -> None:
        self.decls = decls
        self.classes = classes
        self.env = env
        self.types = types
        # While a method body is being lowered: which rigid constant in it
        # stands for which quantified variable. Empty everywhere else, because
        # nothing else is checked against skolems. See `MethodImpl.skolems`.
        self.rigid: dict[str, TVar] = {}

    # -- the program -------------------------------------------------------

    def program(self, ordered: list[ast.Stmt]) -> CProgram:
        out = CProgram()
        for name, info in self.classes.classes.items():
            for method, impl in info.defaults.items():
                out.dicts.append(self.default_method(name, method, impl))
        for cls in self.classes.instances:
            for inst in self.classes.instances[cls]:
                out.dicts.append(self.instance(inst))
        scope = Scope(set())
        for stmt in ordered:
            out.binds.extend(self.top(stmt, scope))
        return out

    def top(self, stmt: ast.Stmt, scope: Scope) -> list[CBind]:
        """One top-level statement. Only bindings occur here."""
        if isinstance(stmt, ast.SFun):
            return [self.fun_bind(stmt.decl, scope)]
        if isinstance(stmt, (ast.SLet, ast.SVar)):
            return self.let_binds(stmt, scope)
        if isinstance(stmt, ast.SExpr):
            # A bare expression at the top level: evaluated for effect, so it
            # is a binding nothing reads, which is what `CLet` already means.
            value = self.expr(stmt.expr, scope)
            return [CBind(fresh_name("top"), value.ty, [], value, stmt.span)]
        raise AssertionError(f"unhandled top-level {type(stmt).__name__}")

    def fun_bind(self, decl: ast.FunDecl, scope: Scope) -> CBind:
        scheme = self.scheme_of(decl.name)
        binders = list(scheme.quantified) if scheme is not None else []
        ty = scheme.body if scheme is not None else self.fun_type(decl)
        return CBind(decl.name, self.abstracted(ty, getattr(decl, "dicts", None)),
                     binders, self.function(decl, ty, scope), decl.span,
                     equations=self.equations(scheme),
                     module=_module_of(decl.name))

    def abstracted(self, ty: Type, dicts) -> Type:
        """A binding's type as Core sees it: dictionaries first.

        The scheme says `[Show a] fun(a) -> Unit`, with the context to one side
        of the type. In Core there is no "to one side" -- a dictionary is an
        argument, so it is in the type, and `print` is
        `forall a. fun(%Dict.Show a) -> fun(a) -> Unit`. Every use site is
        already built to match: a type application, and then an application to
        the dictionaries.
        """
        if dicts is None or not dicts.params:
            return ty
        return TFun([self.pred_type(p) for p in dicts.preds], ty)

    def let_binds(self, stmt, scope: Scope) -> list[CBind]:
        """A top-level `let` or `var`, possibly binding several names.

        A destructuring binding becomes one binding of the whole value and one
        projection per name, rather than a pattern at the top level: Core has
        no statements, so there is nowhere for a multi-name binder to live.
        """
        value = self.expr(stmt.value, scope)
        dicts = getattr(stmt, "dicts", None)
        if isinstance(stmt.pat, ast.PVar) or isinstance(stmt.pat, ast.PAnnot):
            pat = _unannot(stmt.pat)
            if isinstance(pat, ast.PVar):
                scheme = self.scheme_of(pat.name)
                binders = list(scheme.quantified) if scheme is not None else []
                body = self.abstract(value, dicts)
                mutable = isinstance(stmt, ast.SVar)
                if mutable:
                    scope.cells.add(pat.name)
                    body = CRef(ref_of(body.ty), stmt.span, body)
                return [CBind(pat.name, body.ty, binders, body, stmt.span,
                              mutable)]
        holder = fresh_name("bound")
        out = [CBind(holder, value.ty, [], value, stmt.span)]
        for name in sorted(_pattern_vars(stmt.pat)):
            picked = CMatch(
                self.name_type(name), stmt.span, CVar(value.ty, stmt.span, holder),
                [CAlt(stmt.pat, CVar(self.name_type(name), stmt.span, name))],
            )
            out.append(CBind(name, picked.ty, [], picked, stmt.span))
        return out

    # -- classes and instances --------------------------------------------

    def dict_type(self, cls: str, arg: Type) -> Type:
        info = self.classes.classes[cls]
        return TApp(dict_con(cls, info.kind), arg, STAR)

    def instance(self, inst: InstInfo) -> CBind:
        """One instance dictionary, as a top-level binding.

        With a context it is a function: `instance [Show a] Show (Array a)`
        takes the `Show a` dictionary and answers the `Show (Array a)` one.
        That is what it always meant; the evaluator built it lazily and
        memoised on identity, and here it is an ordinary binding.
        """
        plan = inst.plan
        assert isinstance(plan, InstancePlan)
        result = self.dict_type(inst.cls, inst.head)
        fields: list[tuple[str, CExpr]] = []
        for sup, evidence in plan.supers.items():
            fields.append((super_field(sup),
                           self.evidence(evidence,
                                         self.dict_type(sup, inst.head),
                                         self.instance_givens(inst, plan))))
        for name, impl in plan.methods.items():
            fields.append((name, self.method(inst, name, impl, plan.params)))
        record = CRecord(result, inst.decl.span, f"%Dict.{inst.cls}", fields)
        value: CExpr = record
        if plan.params:
            params = [CParam(p, self.dict_type(pred.name, pred.args[0]))
                      for p, pred in zip(plan.params, inst.context)]
            value = CLam(TFun([p.ty for p in params], result), inst.decl.span,
                         params, record, inst_name(inst))
        return CBind(inst_name(inst), value.ty, self.instance_binders(inst),
                     value, inst.decl.span, module=inst.module)

    def self_dict(self, inst: InstInfo, params: list[str]) -> CExpr:
        """The instance's own dictionary, as seen from inside one of its
        methods: applied to its own binders and to its own context, which is
        what makes the reference well-typed rather than merely well-named."""
        result = self.dict_type(inst.cls, inst.head)
        fn: CExpr = CVar(_unknown(), inst.decl.span, inst_name(inst))
        binders = self.instance_binders(inst)
        if binders:
            fn = CTyApp(_unknown(), inst.decl.span, fn, list(binders))
        if not inst.context:
            fn.ty = result
            return fn
        args = [CVar(self.pred_type(pred), inst.decl.span, name)
                for name, pred in zip(params, inst.context)]
        fn.ty = TFun([a.ty for a in args], result)
        return CApp(result, inst.decl.span, fn, args)

    def default_name(self, cls: str, method: str) -> str:
        return f"%default.{cls}.{method}"

    def method_type(self, info, head: Type) -> Type:
        """A method's type as it sits in a dictionary, at one head.

        A method with its own context (`foldMap[Monoid m]`) takes those
        dictionaries per *call* rather than per instance, so they are
        parameters of the field, not of the dictionary that holds it.
        """
        mapping = {info.class_var.id: head}
        ty = substitute(info.scheme.body, mapping)
        own = info.scheme.preds[1:]
        if not own:
            return ty
        return TFun([self.dict_type(p.name, substitute(p.args[0], mapping))
                     for p in own], ty)

    def default_method(self, cls: str, name: str, impl: MethodImpl) -> CBind:
        """A class's default method, lowered once, for every instance to share.

        It has to be once. The generator checks a default body a single time,
        against the *class* variable, so the types recorded in it are about
        that variable and not about any instance. Copying the body into each
        dictionary would put `a` where `Int` belongs -- which is exactly what
        the checker caught the moment it existed.

        So it is what it always was: a function of the dictionary it belongs
        to, polymorphic in the class variable. `impl.self_name` is the name
        the body already uses for that dictionary, so it becomes the
        parameter, and an instance that does not override the method fills its
        field with an application of this.
        """
        info = self.classes.classes[cls]
        method_info = info.methods[name]
        var = method_info.class_var
        self_ty = self.dict_type(cls, var)
        with self.skolems_of(impl, method_info, [var]):
            body = self.function(impl.decl, method_info.scheme.body,
                                 Scope(set()), dict_params=impl.dict_params,
                                 preds=method_info.scheme.preds[1:])
        body = self.method_abstraction(method_info, body, impl.decl.span)
        lam = CLam(TFun([self_ty], body.ty), impl.decl.span,
                   [CParam(impl.self_name, self_ty)], body,
                   self.default_name(cls, name))
        return CBind(self.default_name(cls, name), lam.ty, [var], lam,
                     impl.decl.span, module=info.module)

    def method(self, inst: InstInfo, name: str, impl: MethodImpl,
               params: list[str]) -> CExpr:
        """One method body, as a lambda inside its dictionary.

        `impl.self_name` is the dictionary the body belongs to, bound so that a
        method may call another of its own class without anything being passed
        -- which is what makes a *default* method work at all. In Core it is a
        `let` around the body rather than a name the evaluator arranges to have
        in scope.
        """
        info = self.classes.classes[inst.cls]
        method_info = info.methods[name]
        head = inst.head
        if impl is info.defaults.get(name):
            # Not this instance's body: the class's, shared. Applied at this
            # head and to this dictionary. See `default_method`.
            own = self.self_dict(inst, params)
            result = self.method_type(method_info, head)
            fn: CExpr = CTyApp(TFun([own.ty], result), impl.decl.span,
                               CVar(_unknown(), impl.decl.span,
                                    self.default_name(inst.cls, name)),
                               [head])
            return CApp(result, impl.decl.span, fn, [own])
        mapping = {method_info.class_var.id: head}
        ty = substitute(method_info.scheme.body, mapping)
        with self.skolems_of(impl, method_info, _free_vars(inst.head)):
            body = self.function(impl.decl, ty, Scope(set()),
                                 dict_params=impl.dict_params,
                                 preds=method_info.scheme.preds[1:])
        if impl.self_name:
            own = self.self_dict(inst, params)
            body = CLet(body.ty, impl.decl.span, impl.self_name, own.ty,
                        own, body)
        return self.method_abstraction(method_info, body, impl.decl.span)

    def method_abstraction(self, info, body: CExpr, span) -> CExpr:
        """A method is polymorphic in its *own* variables, and the dictionary
        that holds it does not fix those -- only the class variable. `map` in a
        `Functor (Either l)` dictionary is still `forall a b`, so the field is
        a type abstraction and every use of it a type application."""
        binders = [q for q in info.scheme.quantified if q is not info.class_var]
        if not binders:
            return body
        return CTyLam(body.ty, span, binders, body)

    def skolems_of(self, impl: MethodImpl, info, head_vars: list[TVar]):
        """Set the constant-to-variable map for one method body."""
        by_id = {v.id: v for v in head_vars}
        by_id.update({q.id: q for q in info.scheme.quantified})
        rigid: dict[str, TVar] = {}
        for var_id, con in impl.skolems.items():
            var = by_id.get(var_id)
            if isinstance(con, TCon) and var is not None:
                rigid[con.name] = var
        return _Rigid(self, rigid)

    # -- evidence ----------------------------------------------------------

    def evidence(self, ev: Evidence, want: Type,
                 givens: dict[str, Type] | None = None) -> CExpr:
        """One `Evidence` tree, as a term. This is the milestone in one method."""
        if isinstance(ev, FromDict):
            # Every node of the chain gets its real type, not just the last.
            # A superclass constrains the class variable itself (`classes.py`),
            # so every dictionary along the path is *about the same type* and
            # differs only in its class -- which the path names. Leaving the
            # middle for the checker to guess was the same mistake in miniature
            # as leaving the whole elaboration unchecked: it made the checker
            # believe a term instead of reading it.
            head = _dict_arg(want)
            start = (givens or {}).get(ev.name)
            out: CExpr = CVar(start if start is not None else want, None, ev.name)
            for i, step in enumerate(ev.path):
                at = want if i == len(ev.path) - 1 else self.dict_type(step, head)
                out = CField(at, None, out, super_field(step))
            return out
        if isinstance(ev, FromInstance):
            return self.instance_ref(ev.inst, want, ev.args)
        if isinstance(ev, Absent):
            # The predicate's argument is `⊥`: there is no value here, so
            # there is no dictionary to find. `error` diverges and so has
            # whatever type its context wants (design.md 10).
            return CApp(want, None,
                        CVar(TFun([TCon("String")], want), None, prelude.ERROR),
                        [CLit(TCon("String"), None, "String",
                              f"no evidence for {ev.pred}")])
        raise AssertionError(f"unhandled evidence {type(ev).__name__}")

    # -- functions ---------------------------------------------------------

    def fun_type(self, decl: ast.FunDecl) -> Type:
        assert decl.body is not None
        return self.types.of(decl.body)

    def function(self, decl: ast.FunDecl, ty: Type, scope: Scope,
                 dict_params: list[str] | None = None,
                 preds=None) -> CExpr:
        """A `fun` declaration, as a lambda -- possibly under dictionary ones."""
        assert decl.body is not None
        dicts = getattr(decl, "dicts", None)
        if dict_params is None and dicts is not None and dicts.params:
            dict_params, preds = dicts.params, dicts.preds
        with self.signature_skolems(decl, ty):
            inner = self.lambda_of(decl.params, decl.body, ty, decl.span,
                                   scope, decl.name)
        return self.wrap_dicts(inner, dict_params, preds, decl.span)

    def signature_skolems(self, decl: ast.FunDecl, ty: Type):
        """The constant-to-variable map for a signature-checked body.

        A method body sets this in `method`; this is the other case, a `fun`
        whose own declared type was checked against skolems (delta 38). If the
        declaration has no signature there is nothing rigid in it and the map
        is empty, which leaves `rigidly` the identity.
        """
        dicts = getattr(decl, "dicts", None)
        if dicts is None or not dicts.skolems:
            return _Rigid(self, None)
        by_id = {v.id: v for v in _free_vars(ty)}
        rigid: dict[str, TVar] = {}
        for var_id, con in dicts.skolems.items():
            var = by_id.get(var_id)
            if isinstance(con, TCon) and var is not None:
                rigid[con.name] = var
        return _Rigid(self, rigid)

    def wrap_dicts(self, inner: CExpr, params, preds, span) -> CExpr:
        if not params:
            return inner
        cparams = [CParam(p, self.dict_type(pred.name, pred.args[0]))
                   for p, pred in zip(params, preds)]
        return CLam(TFun([p.ty for p in cparams], inner.ty), span, cparams, inner)

    def abstract(self, value: CExpr, dicts) -> CExpr:
        if dicts is None or not dicts.params:
            return value
        return self.wrap_dicts(value, dicts.params, dicts.preds, value.span)

    def lambda_of(self, params: list[ast.Pattern], body: ast.Expr, ty: Type,
                  span: Span | None, scope: Scope, name: str = "<anonymous>"
                  ) -> CExpr:
        """Parameters come from the function's *type*, not from the patterns.

        A parameter is a pattern, and a pattern binds no type of its own that
        anything recorded. The function type has one entry per parameter and is
        recorded, so that is where the types come from -- and a parameter that
        is not a plain name becomes a fresh binder plus a `match`, which is
        what it always meant.
        """
        fn = prune(ty)
        assert isinstance(fn, TFun), f"a function whose type is not one: {fn}"
        inner = scope.child()
        cparams: list[CParam] = []
        matches: list[tuple[str, ast.Pattern, Type]] = []
        assigned = _assigned(body)
        cells: list[tuple[str, Type]] = []
        for pat, pty in zip(params, fn.params):
            bare = _unannot(pat)
            if isinstance(bare, ast.PVar):
                cparams.append(CParam(bare.name, pty))
                if bare.name in assigned:
                    cells.append((bare.name, pty))
            elif isinstance(bare, ast.PWild):
                cparams.append(CParam(fresh_name("wild"), pty))
            else:
                holder = fresh_name("param")
                cparams.append(CParam(holder, pty))
                matches.append((holder, bare, pty))
        # A destructured parameter's binders are reassignable too, so any that
        # the body writes become cells, exactly as a named parameter's would.
        # Collected across every pattern first, because the body is lowered
        # once, inside all of them.
        pattern_cells: list[tuple[str, Type]] = []
        for _, pat, _ in matches:
            bound = self.types.of_pattern(pat)
            for bound_name in sorted(bound):
                if bound_name in assigned:
                    pattern_cells.append(
                        (bound_name, self.rigidly(bound[bound_name])))
        # Every binder shadows whatever was in scope under its name, and binds
        # it monomorphically: nothing a lambda or a pattern binds generalizes.
        # So an outer name's cell-ness and its binders both stop here, and are
        # reinstated only for the binders this body actually makes cells of.
        shadowed = {p.name for p in cparams}
        for _, pat, _ in matches:
            shadowed.update(self.types.of_pattern(pat))
        for shadow in shadowed:
            inner.binders.pop(shadow, None)
            inner.cells.discard(shadow)
        inner.cells.update(name for name, _ in cells)
        inner.cells.update(name for name, _ in pattern_cells)
        out = self.expr(body, inner)
        for cell_name, cell_ty in reversed(pattern_cells):
            out = CLet(out.ty, span, cell_name, ref_of(cell_ty),
                       CRef(ref_of(cell_ty), span,
                            CVar(cell_ty, span, cell_name)), out)
        for holder, pat, pty in reversed(matches):
            out = CMatch(out.ty, span, CVar(pty, span, holder), [CAlt(pat, out)])
        for cell_name, cell_ty in reversed(cells):
            # A parameter something writes is a cell, made from the parameter
            # itself. The parameter keeps its name and the cell shadows it,
            # which is exactly the scope the evaluator gives it today.
            out = CLet(out.ty, span, cell_name, ref_of(cell_ty),
                       CRef(ref_of(cell_ty), span, CVar(cell_ty, span, cell_name)),
                       out)
        return CLam(fn, span, cparams, out, name)

    # -- expressions -------------------------------------------------------

    def expr(self, e: ast.Expr, scope: Scope) -> CExpr:
        method = getattr(self, "_lower_" + type(e).__name__, None)
        if method is None:
            raise AssertionError(f"cannot lower {type(e).__name__}")
        return method(e, scope)

    def ty_of(self, e: ast.Expr) -> Type:
        return self.rigidly(self.types.of(e))

    def rigidly(self, t: Type) -> Type:
        """A type read out of a method body, back in terms of variables.

        Outside a method body this is the identity. Inside one it undoes the
        skolemization the generator applied: `l` becomes the instance's own
        variable again, so the body agrees with the dictionary that holds it.
        """
        return t if not self.rigid else _unskolem(t, self.rigid)

    def _lower_ELit(self, e: ast.ELit, scope: Scope) -> CExpr:
        return CLit(self.ty_of(e), e.span, e.kind, e.value)

    def _lower_EUnit(self, e: ast.EUnit, scope: Scope) -> CExpr:
        return CUnit(self.ty_of(e), e.span)

    def _lower_ECon(self, e: ast.ECon, scope: Scope) -> CExpr:
        return CCon(self.ty_of(e), e.span, e.name)

    def _lower_EVar(self, e: ast.EVar, scope: Scope) -> CExpr:
        return self.var(e, scope)

    def var(self, e: ast.EVar, scope: Scope) -> CExpr:
        """A name: possibly a projection, possibly applied to types and dicts.

        The three steps are in the order they were abstracted in, which is the
        only order that typechecks: select or name the value, apply its type
        arguments, then apply its dictionaries.
        """
        result = self.ty_of(e)
        use: Use | None = e.use  # type: ignore[assignment]
        if use is None or (not use.evidence and not use.type_args):
            if e.name in scope.cells:
                return CDeref(result, e.span,
                              CVar(ref_of(result), e.span, e.name))
            binders = self.binders_of(e.name, scope)
            if binders:
                # A recursive occurrence: monomorphic to inference, still a
                # type application here, at the binding's own variables.
                return CTyApp(result, e.span,
                              CVar(_unknown(), e.span, e.name), list(binders))
            return CVar(result, e.span, e.name)

        dicts = [self.evidence(ev, self.pred_type(p), self.givens(use))
                 for ev, p in zip(use.evidence, use.preds)]
        type_args = [self.resolved(a) for a in use.type_args]
        if use.method is not None:
            # The class's own dictionary is the first predicate, by the
            # convention the method's scheme sets up; the method is a field of
            # it and the rest, if any, are the method's own context.
            base: CExpr = CField(_unknown(), e.span, dicts[0],
                                 e.name.rpartition(SEP_HASH)[2] or e.name)
            dicts = dicts[1:]
            # And the class variable is *already* fixed, by the dictionary the
            # method was projected from. It is one of the method scheme's
            # quantified variables, so it is one of the recorded type
            # arguments -- but applying it here would fix it a second time,
            # which is the same variable bound twice and no longer a type
            # application. What remains is the method's own polymorphism,
            # `foldMap`'s `[Monoid c]` and the like.
            type_args = _without_class_var(use.method, type_args)
        else:
            base = CVar(_unknown(), e.span, e.name)
            if not type_args:
                # A recursive or mutually recursive occurrence: the group is
                # bound monomorphically while its own bodies are checked, so
                # nothing was recorded, and the application is at the group's
                # own variables. `isEven` calling `isOdd` is this, and it does
                # carry dictionaries -- the solver patched those in -- which is
                # why the test cannot be "no evidence either".
                type_args = list(self.binders_of(e.name, scope))
        after_types = TFun([d.ty for d in dicts], result) if dicts else result
        if type_args:
            base.ty = _unknown()
            base = CTyApp(after_types, e.span, base, type_args)
        else:
            base.ty = after_types
        if dicts:
            return CApp(result, e.span, base, dicts)
        return base

    def instance_binders(self, inst: InstInfo) -> list[TVar]:
        """The instance's own variables, in the order its binding abstracts
        them. Read off the head, which is the only place they appear, and used
        by both the definition and every reference so the two cannot disagree."""
        return _free_vars(inst.head)

    def instance_ref(self, inst: InstInfo, want: Type,
                     args: list[Evidence]) -> CExpr:
        """A reference to an instance's dictionary, at the types it was
        selected at and applied to the evidence for its own context.

        The types are not recorded on the `Evidence` -- it names the instance,
        not the match that chose it -- so they are recovered the way instance
        selection recovered them in the first place: by matching the head
        against the dictionary type wanted here. `classes.match` is the same
        one-way match `by_inst` uses, which is what makes this the *same*
        answer rather than a second opinion.
        """
        head = _dict_arg(want)
        mapping = match(inst.head, head)
        assert mapping is not None, (
            f"instance {inst.cls} {inst.con} does not cover {want}")
        binders = self.instance_binders(inst)
        type_args = [mapping.get(b.id, b) for b in binders]
        result = self.dict_type(inst.cls, head)
        fn: CExpr = CVar(_unknown(), None, inst_name(inst))
        if type_args:
            fn.ty = _unknown()
            fn = CTyApp(_unknown(), None, fn, type_args)
        if not args:
            fn.ty = result
            return fn
        arg_terms = []
        for evidence, pred in zip(args, inst.context):
            at = substitute(pred.args[0], mapping)
            arg_terms.append(self.evidence(
                evidence, self.dict_type(pred.name, self.resolved(at))))
            # No givens: an instance's context arrives as its own parameters,
            # which `instance_givens` supplies where those are in scope.
        fn.ty = TFun([a.ty for a in arg_terms], result)
        return CApp(result, None, fn, arg_terms)

    def givens(self, use: Use) -> dict[str, Type]:
        """The dictionary variables in scope at a use, and their types.

        Recorded by the solver as it descended (`evidence.Scope.givens`), and
        needed here because a `FromDict` names a variable and a path but not
        the class the variable's own dictionary has.
        """
        out: dict[str, Type] = {}
        for scope in use.scopes:
            for name, pred in scope.givens:
                if self.classes.is_class(pred.name):
                    out[name] = self.pred_type(pred)
        return out

    def instance_givens(self, inst: InstInfo, plan: InstancePlan) -> dict[str, Type]:
        """The dictionaries an instance's own methods have in scope: its
        context's, under the names the plan gave them."""
        return {name: self.dict_type(pred.name, pred.args[0])
                for name, pred in zip(plan.params, inst.context)}

    def pred_type(self, pred) -> Type:
        return self.dict_type(pred.name, self.resolved(pred.args[0]))

    def resolved(self, t: Type) -> Type:
        """A type from the solver's own records, read the way `ty_of` reads
        one: reduced, and back in terms of variables rather than skolems."""
        return self.rigidly(self.types.resolve(t))

    def _lower_ETuple(self, e: ast.ETuple, scope: Scope) -> CExpr:
        return CTuple(self.ty_of(e), e.span,
                      [self.expr(x, scope) for x in e.elems])

    def _lower_EArray(self, e: ast.EArray, scope: Scope) -> CExpr:
        return CArray(self.ty_of(e), e.span,
                      [self.expr(x, scope) for x in e.elems])

    def _lower_ERecord(self, e: ast.ERecord, scope: Scope) -> CExpr:
        return CRecord(self.ty_of(e), e.span, e.con,
                       [(n, self.expr(v, scope)) for n, v in e.fields])

    def _lower_EField(self, e: ast.EField, scope: Scope) -> CExpr:
        return CField(self.ty_of(e), e.span, self.expr(e.obj, scope), e.name)

    def _lower_EIndex(self, e: ast.EIndex, scope: Scope) -> CExpr:
        return CIndex(self.ty_of(e), e.span, self.expr(e.arr, scope),
                      self.expr(e.index, scope))

    def _lower_ELambda(self, e: ast.ELambda, scope: Scope) -> CExpr:
        return self.lambda_of(e.params, e.body, self.ty_of(e), e.span, scope)

    def _lower_ECall(self, e: ast.ECall, scope: Scope) -> CExpr:
        return CApp(self.ty_of(e), e.span, self.expr(e.fn, scope),
                    [self.expr(a, scope) for a in e.args])

    def _lower_EAnnot(self, e: ast.EAnnot, scope: Scope) -> CExpr:
        # An annotation constrained inference and has nothing left to say.
        return self.expr(e.expr, scope)

    def _lower_EUnary(self, e: ast.EUnary, scope: Scope) -> CExpr:
        operand = self.expr(e.operand, scope)
        if e.fn is None:
            # `!x`, which is not a method (design.md 8.2).
            return CApp(self.ty_of(e), e.span,
                        core.CPrim(TFun([BOOL], BOOL), e.span, "Prim.not"),
                        [operand])
        return CApp(self.ty_of(e), e.span, self.var(e.fn, scope), [operand])

    def _lower_EBinary(self, e: ast.EBinary, scope: Scope) -> CExpr:
        ty = self.ty_of(e)
        if e.op in ("&&", "||"):
            # These two short-circuit, so they are the `if` they already mean.
            # The left operand is bound first because it is both the test and,
            # when it decides the answer, the answer.
            left = self.expr(e.left, scope)
            right = self.expr(e.right, scope)
            name = fresh_name("b")
            held = CVar(left.ty, e.span, name)
            branch = (CIf(ty, e.span, held, right, held) if e.op == "&&"
                      else CIf(ty, e.span, held, held, right))
            return CLet(ty, e.span, name, left.ty, left, branch)
        assert e.fn is not None, f"operator '{e.op}' has no method"
        return CApp(ty, e.span, self.var(e.fn, scope),
                    [self.expr(e.left, scope), self.expr(e.right, scope)])

    def _lower_EIf(self, e: ast.EIf, scope: Scope) -> CExpr:
        return CIf(self.ty_of(e), e.span, self.expr(e.cond, scope),
                   self.expr(e.then, scope),
                   None if e.otherwise is None else self.expr(e.otherwise, scope))

    def _lower_EMatch(self, e: ast.EMatch, scope: Scope) -> CExpr:
        alts = []
        for arm in e.arms:
            for pat in arm.patterns:
                alts.append(CAlt(pat, self.celled(
                    pat, arm.body,
                    lambda inner: self.expr(arm.body, inner),
                    scope.child(), arm.span)))
        return CMatch(self.ty_of(e), e.span, self.expr(e.scrutinee, scope), alts)

    def _lower_EWhile(self, e: ast.EWhile, scope: Scope) -> CExpr:
        return CWhile(self.ty_of(e), e.span, self.expr(e.cond, scope),
                      self.expr(e.body, scope.child()))

    def _lower_ELoop(self, e: ast.ELoop, scope: Scope) -> CExpr:
        return CLoop(self.ty_of(e), e.span, self.expr(e.body, scope.child()))

    def _lower_EForC(self, e: ast.EForC, scope: Scope) -> CExpr:
        inner = scope.child()
        init = None if e.init is None else self.stmt_value(e.init, inner)
        cond = self.expr(e.cond, inner)
        step = None if e.step is None else self.stmt_value(e.step, inner)
        return CForC(self.ty_of(e), e.span, init, cond, step,
                     self.expr(e.body, inner.child()))

    def _lower_EForIn(self, e: ast.EForIn, scope: Scope) -> CExpr:
        assert e.iter_fn is not None and e.next_fn is not None
        inner = scope.child()
        return CForIn(
            self.ty_of(e), e.span, e.pat, self.expr(e.iterable, scope),
            self.var(e.iter_fn, scope), self.var(e.next_fn, scope),
            self.expr(e.body, inner),
        )

    def _lower_EReturn(self, e: ast.EReturn, scope: Scope) -> CExpr:
        value = None if e.value is None else self.expr(e.value, scope)
        return CReturn(self.ty_of(e), e.span, value)

    def _lower_EBreak(self, e: ast.EBreak, scope: Scope) -> CExpr:
        value = None if e.value is None else self.expr(e.value, scope)
        return CBreak(self.ty_of(e), e.span, value)

    def _lower_EContinue(self, e: ast.EContinue, scope: Scope) -> CExpr:
        return CContinue(self.ty_of(e), e.span)

    def _lower_EBlock(self, e: ast.EBlock, scope: Scope) -> CExpr:
        return self.block(e.stmts, self.ty_of(e), e.span, scope.child())

    # -- statements, which Core does not have ------------------------------

    def block(self, stmts: list[ast.Stmt], ty: Type, span: Span | None,
              scope: Scope) -> CExpr:
        """A statement sequence, as nested `let`s.

        The value of a block is its last statement's, and every earlier one is
        a binding -- of the name it introduces, or of a name nothing reads.
        `{ }` is `()`.
        """
        if not stmts:
            return CUnit(ty, span)
        head, rest = stmts[0], stmts[1:]
        if not rest:
            return self.tail(head, ty, span, scope)
        if isinstance(head, ast.SFun):
            # A local `fun` may recurse and may call its siblings, so it binds
            # as a group even when the group has one member.
            group = [head]
            while rest and isinstance(rest[0], ast.SFun):
                group.append(rest[0])
                rest = rest[1:]
            # Registered before any body is lowered, so a member calling
            # itself or a sibling finds the group's binders.
            for stmt in group:
                scope.binders[stmt.decl.name] = self.fun_binders(stmt.decl)
            binds = [self.local_fun(stmt.decl, scope) for stmt in group]
            return CLetRec(ty, span, binds, self.block(rest, ty, span, scope))
        # The rest of the block is lowered *by* `bind` rather than before it,
        # and that ordering is load-bearing: a `var` becomes a reference cell,
        # and every mention of it after the binding has to become a read of
        # that cell. Lowering the rest first would lower those mentions while
        # the name was still an ordinary one.
        return self.bind(head, lambda: self.block(rest, ty, span, scope),
                         ty, span, scope)

    def fun_binders(self, decl: ast.FunDecl) -> list[TVar]:
        scheme = self.local_scheme(decl)
        return [] if scheme is None else list(scheme.quantified)

    def local_fun(self, decl: ast.FunDecl, scope: Scope) -> CBind:
        """A `fun` bound inside a body, with whatever it generalized over.

        Its scheme is not in the environment -- a local binding's is defined
        into a scope the solver pops -- so it comes from the group's own
        `Abstraction`, which is where the solver recorded it, and failing that
        from the type the generator bound it at.
        """
        scheme = self.local_scheme(decl)
        if scheme is not None:
            fn_ty = self.types.resolve(scheme.body)
            binders = list(scheme.quantified)
        else:
            fn_ty = self.decl_type(decl)
            binders = []
        dicts = getattr(decl, "dicts", None)
        return CBind(decl.name, self.abstracted(fn_ty, dicts), binders,
                     self.function(decl, fn_ty, scope), decl.span,
                     equations=self.equations(scheme))

    def local_scheme(self, decl: ast.FunDecl):
        dicts = getattr(decl, "dicts", None)
        if dicts is None:
            return None
        return dicts.schemes.get(decl.name)

    def decl_type(self, decl: ast.FunDecl) -> Type:
        found = self.types.of_decl(decl)
        if found is not None:
            return found
        scheme = self.scheme_of(decl.name)
        assert scheme is not None, f"'{decl.name}' has no type"
        return self.types.resolve(scheme.body)

    def tail(self, stmt: ast.Stmt, ty: Type, span: Span | None,
             scope: Scope) -> CExpr:
        """The last statement of a block, whose value is the block's."""
        if isinstance(stmt, ast.SExpr):
            return self.expr(stmt.expr, scope)
        return self.bind(stmt, lambda: CUnit(ty, span), ty, span, scope)

    def bind(self, stmt: ast.Stmt, rest, ty: Type, span: Span | None,
             scope: Scope) -> CExpr:
        """One non-final statement, as a `let` around everything after it.

        `rest` is a thunk, not a term: what a statement binds may change how
        the statements after it lower, and a `var` does.
        """
        if isinstance(stmt, ast.SExpr):
            value = self.expr(stmt.expr, scope)
            body = rest()
            return CLet(body.ty, stmt.span, fresh_name("seq"), value.ty,
                        value, body)
        if isinstance(stmt, (ast.SLet, ast.SVar)):
            return self.bind_pattern(stmt, rest, scope)
        if isinstance(stmt, ast.SAssign):
            value = self.assign(stmt, scope)
            body = rest()
            return CLet(body.ty, stmt.span, fresh_name("seq"), value.ty,
                        value, body)
        if isinstance(stmt, ast.SFun):
            scope.binders[stmt.decl.name] = self.fun_binders(stmt.decl)
            bind = self.local_fun(stmt.decl, scope)
            return CLetRec(ty, stmt.span, [bind], rest())
        raise AssertionError(f"unhandled statement {type(stmt).__name__}")

    def bind_pattern(self, stmt, rest, scope: Scope) -> CExpr:
        value = self.abstract(self.expr(stmt.value, scope),
                              getattr(stmt, "dicts", None))
        bare = _unannot(stmt.pat)
        mutable = isinstance(stmt, ast.SVar)
        binders = self.let_binders(stmt)
        if isinstance(bare, ast.PVar):
            scope.binders[bare.name] = binders
            if mutable:
                scope.cells.add(bare.name)
                cell = CRef(ref_of(value.ty), stmt.span, value)
                body = rest()
                return CLet(body.ty, stmt.span, bare.name, cell.ty, cell, body,
                            binders)
            body = rest()
            return CLet(body.ty, stmt.span, bare.name, value.ty, value, body,
                        binders)
        if isinstance(bare, ast.PWild):
            body = rest()
            return CLet(body.ty, stmt.span, fresh_name("seq"), value.ty,
                        value, body)
        # A destructuring binding is a one-armed match, which is what it is.
        body = rest()
        return CMatch(body.ty, stmt.span, value, [CAlt(bare, body)])

    def celled(self, pat, body: ast.Expr, lower, scope: Scope, span) -> CExpr:
        """Lower a pattern's body, making a cell of any binder it assigns.

        A pattern binder is reassignable like any other -- `mutation.tl`'s
        `fun ignore(Cell { value })` writes to `value`, and the file's own
        comment says what that means: "reassigning a destructured parameter
        rebinds the local name and nothing else: patterns bind, they do not
        alias." A cell is exactly that, and nothing more.
        """
        assigned = _assigned(body) & set(self.types.of_pattern(pat))
        if not assigned:
            return lower(scope)
        inner = scope.child()
        inner.cells.update(assigned)
        types = self.types.of_pattern(pat)
        out = lower(inner)
        for name in sorted(assigned, reverse=True):
            ty = self.rigidly(types[name])
            out = CLet(out.ty, span, name, ref_of(ty),
                       CRef(ref_of(ty), span, CVar(ty, span, name)), out)
        return out

    def let_binders(self, stmt) -> list[TVar]:
        """What a local `let` generalized over, if it generalized."""
        dicts = getattr(stmt, "dicts", None)
        bare = _unannot(stmt.pat)
        if dicts is None or not isinstance(bare, ast.PVar):
            return []
        scheme = dicts.schemes.get(bare.name)
        return [] if scheme is None else list(scheme.quantified)

    def stmt_value(self, stmt: ast.Stmt, scope: Scope) -> CExpr:
        """A statement in a position that wants an expression: a C-style
        `for`'s init and step, which are statements in the grammar."""
        if isinstance(stmt, ast.SExpr):
            return self.expr(stmt.expr, scope)
        if isinstance(stmt, ast.SAssign):
            return self.assign(stmt, scope)
        if isinstance(stmt, (ast.SLet, ast.SVar)):
            # `for var i = 0; ...`: the binding is the init, and its scope is
            # the loop, so it is expressed as the cell it already is.
            bare = _unannot(stmt.pat)
            assert isinstance(bare, ast.PVar)
            value = self.expr(stmt.value, scope)
            if isinstance(stmt, ast.SVar):
                scope.cells.add(bare.name)
                value = CRef(ref_of(value.ty), stmt.span, value)
            return CLet(value.ty, stmt.span, bare.name, value.ty, value,
                        CVar(value.ty, stmt.span, bare.name))
        raise AssertionError(f"unhandled for-statement {type(stmt).__name__}")

    def assign(self, stmt: ast.SAssign, scope: Scope) -> CExpr:
        target = stmt.target
        value = self.expr(stmt.value, scope)
        unit = TCon("Unit")
        if isinstance(target, ast.EVar):
            cell = CVar(ref_of(value.ty), target.span, target.name)
            return CAssign(unit, stmt.span, cell, value)
        if isinstance(target, ast.EField):
            obj = self.expr(target.obj, scope)
            field = CField(value.ty, target.span, obj, target.name)
            return CAssign(unit, stmt.span, field, value)
        if isinstance(target, ast.EIndex):
            arr = self.expr(target.arr, scope)
            slot = CIndex(value.ty, target.span, arr,
                          self.expr(target.index, scope))
            return CAssign(unit, stmt.span, slot, value)
        raise AssertionError("the parser should have rejected this target")

    # -- looking things up -------------------------------------------------

    def equations(self, scheme) -> list[tuple[Type, Type]]:
        """The equalities a scheme's context states, as rewrite rules.

        `Classes.resolve_equality` already guarantees the shape: a family
        application on the left, not mentioning itself on the right. So the
        pair can be used as a rule exactly as `Solver.reduce` uses it.
        """
        if scheme is None:
            return []
        return [(p.args[0], p.args[1]) for p in scheme.preds
                if p.name == EQUALS and len(p.args) == 2]

    def binders_of(self, name: str, scope: Scope) -> list[TVar]:
        """What a name abstracts over, local bindings before top-level ones."""
        local = scope.binders.get(name)
        if local is not None:
            return local
        scheme = self.scheme_of(name)
        return [] if scheme is None else list(scheme.quantified)

    def scheme_of(self, name: str):
        binding = self.env.lookup(name)
        return None if binding is None else binding.scheme

    def name_type(self, name: str) -> Type:
        scheme = self.scheme_of(name)
        assert scheme is not None, f"'{name}' has no type"
        return self.types.resolve(scheme.body)


SEP_HASH = "#"


def _unskolem(t: Type, rigid: dict[str, TVar]) -> Type:
    """Replace the rigid constants of a method body with their variables.

    `decls.substitute` cannot do this: it maps variables to types, and here it
    is constants that have to go. A skolem is a `TCon` whose name is the one
    the declaration wrote, which is what makes the map keyable by name.
    """
    t = prune(t)
    if isinstance(t, TCon):
        return rigid.get(t.name, t)
    if isinstance(t, TApp):
        return TApp(_unskolem(t.fn, rigid), _unskolem(t.arg, rigid), t.kind)
    if isinstance(t, TFam):
        return TFam(t.name, _unskolem(t.arg, rigid), t.kind)
    if isinstance(t, TFun):
        return TFun([_unskolem(p, rigid) for p in t.params],
                    _unskolem(t.ret, rigid))
    if isinstance(t, TTuple):
        return TTuple([_unskolem(e, rigid) for e in t.elems])
    return t


def _without_class_var(method, type_args: list[Type]) -> list[Type]:
    """A method's type arguments, less the one its dictionary already fixed.

    The arguments are in `scheme.quantified`'s order (delta 48), so the class
    variable's position in that list is the position to drop.
    """
    quantified = method.scheme.quantified
    if len(type_args) != len(quantified):
        return type_args
    return [a for a, q in zip(type_args, quantified)
            if q is not method.class_var]


def _module_of(name: str) -> str:
    """Which module a resolved top-level name belongs to (`Main#f` -> `Main`)."""
    module, sep, _ = name.rpartition(SEP_HASH)
    return module if sep else ""


def _dict_arg(t: Type) -> Type:
    """The type a dictionary type classifies: the `t` of `%Dict.C t`."""
    t = prune(t)
    assert isinstance(t, TApp), f"not a dictionary type: {t}"
    return t.arg


def _unknown() -> Type:
    """A placeholder for a type this pass fills in a moment later."""
    return TVar(0)


def _unannot(pat: ast.Pattern) -> ast.Pattern:
    while isinstance(pat, ast.PAnnot):
        pat = pat.pat
    return pat


def _pattern_vars(pat: ast.Pattern) -> set[str]:
    from .deps import pattern_vars
    return set(pattern_vars(pat))


def _free_vars(t: Type) -> list[TVar]:
    from .typed import children
    out: list[TVar] = []
    seen: set[int] = set()

    def go(ty: Type) -> None:
        ty = prune(ty)
        if isinstance(ty, TVar):
            if ty.id not in seen:
                seen.add(ty.id)
                out.append(ty)
            return
        for c in children(ty):
            go(c)

    go(t)
    return out


def _assigned(node) -> set[str]:
    """Every name assigned anywhere under `node`, nested functions included.

    Nested ones are the whole point: a closure that writes a captured `var`
    writes through to it, so what decides whether a binding is a cell is
    whether *anything* reachable from here writes it -- not whether this body
    does.
    """
    import dataclasses
    out: set[str] = set()

    def go(n) -> None:
        if isinstance(n, ast.SAssign) and isinstance(n.target, ast.EVar):
            out.add(n.target.name)
        if not dataclasses.is_dataclass(n):
            return
        for f in dataclasses.fields(n):
            value = getattr(n, f.name)
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, ast.Node):
                    go(item)

    go(node)
    return out


def program(checked) -> CProgram:
    """Lower a checked program. The entry point `driver.check` calls."""
    return Lowerer(checked.decls, checked.classes, checked.env,
                   checked.types).program(checked.ordered)


__all__ = ["Lowerer", "program"]
