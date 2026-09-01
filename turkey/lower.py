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
    CAlt, CApp, CArray, CAssign, CBind, CCon, CDeref, CExpr,
    CField, CIf, CIndex, CJoin, CJump, CLam, CLet, CLetRec, CLit,
    CMatch, CParam, CProgram, CRecord, CRef, CTuple, CTyApp, CTyLam,
    CUnit, CVar, ref_of,
)
from .decls import DeclTable, substitute
from .errors import Span, Unsupported
from .evidence import (
    Absent, Evidence, FromDict, FromInstance, InstancePlan, MethodImpl, Use,
)
from .types import (
    BOOL, EQUALS, KFun, STAR, TApp, TBottom, TCon, TFam, TFun, TTuple, TVar,
    Type, prune, raw_array_of, spine,
)

UNIT = TCon("Unit")

# The surface forms that this pass turns into join points rather than into
# nodes of their own. There is no Core node for any of them: `core.py` used to
# hold four loops and three transfers, and what replaced them is the `CJoin`
# and `CJump` that were already there for M15a.
LOOPS = (ast.EWhile, ast.ELoop, ast.EForC, ast.EForIn)
TRANSFERS = (ast.EReturn, ast.EBreak, ast.EContinue)

_counter = itertools.count()


def fresh_name(hint: str) -> str:
    """A binder this pass invents. `%` cannot start a source identifier."""
    return f"%{hint}{next(_counter)}"


def dict_con(cls: str, kind) -> TCon:
    return TCon(f"%Dict.{cls}", KFun(kind, STAR))


def super_field(cls: str) -> str:
    return f"%super.{cls}"


def _member_surface(name: str) -> str:
    return name.rpartition(".")[2].rpartition("#")[2] or name


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
    # Where a `return`, a `break` and a `continue` written here jump to. Each
    # is a join name, or None where writing that transfer would have been a
    # compile error anyway -- a `break` outside a loop, a `return` outside a
    # function. This is what replaces "the transfer names its target by where
    # it sits": the scope chain already knows where it sits, so it can say.
    ret: str | None = None
    brk: str | None = None
    cont: str | None = None

    def child(self) -> "Scope":
        return Scope(set(self.cells), dict(self.binders),
                     self.ret, self.brk, self.cont)


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
        # Surface expressions already lowered, by object identity, and what
        # they lowered to. Only `anf` puts anything here, and only for as long
        # as it takes to rebuild one node around its operands. See `anf`.
        self.placeholders: dict[int, CExpr] = {}

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
            fields.append((_member_surface(name),
                           self.method(inst, name, impl, plan.params)))
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
            call = CApp(result, impl.decl.span, fn, [own])
            # Eta-expanded, for the same reason `bind_self` puts the self
            # dictionary under the lambda: the argument is the dictionary this
            # field belongs to, so evaluating the application while the record
            # is being built would build it again, for ever. Under a lambda it
            # waits until the method is called.
            return self.eta(call, result, impl.decl.span)
        mapping = {method_info.class_var.id: head}
        ty = substitute(method_info.scheme.body, mapping)
        with self.skolems_of(impl, method_info, _free_vars(inst.head)):
            body = self.function(impl.decl, ty, Scope(set()),
                                 dict_params=impl.dict_params,
                                 preds=method_info.scheme.preds[1:])
        if impl.self_name:
            body = self.bind_self(inst, params, impl.self_name, body,
                                  impl.decl.span)
        return self.method_abstraction(method_info, body, impl.decl.span)

    def eta(self, call: CExpr, result: Type, span) -> CExpr:
        """`f` as `fun(x, ...) { f(x, ...) }`, to defer evaluating `f`."""
        fn = prune(result)
        if not isinstance(fn, TFun):
            return call
        params = [CParam(fresh_name("eta"), p) for p in fn.params]
        args: list[CExpr] = [CVar(p.ty, span, p.name) for p in params]
        return CLam(fn, span, params, CApp(fn.ret, span, call, args))

    def bind_self(self, inst: InstInfo, params: list[str], name: str,
                  body: CExpr, span) -> CExpr:
        """Bind the instance's own dictionary *inside* the method's lambda.

        Inside, not around it, and that placement is the difference between a
        program that runs and one that does not. `instance [Show a] Show
        (Array a)` names itself, so a binding evaluated while the dictionary is
        being built would build the dictionary again, for ever. Under the
        lambda it is evaluated when the method is *called*, by which time the
        dictionary exists.

        The evaluator used to arrange this with a memo table keyed on object
        identity, registering a dictionary before filling in its methods. That
        was a fact about the interpreter. Here it is a fact about the term.
        """
        own = self.self_dict(inst, params)
        if isinstance(body, CLam):
            inner = body.body
            assert inner is not None
            return CLam(body.ty, body.span, body.params,
                        CLet(inner.ty, span, name, own.ty, own, inner),
                        body.name)
        return CLet(body.ty, span, name, own.ty, own, body)

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
            if start is None and ev.pred is not None:
                start = self.pred_type(ev.pred)
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
        # A lambda is its own function: the `return` in it is this one's, and
        # the `break` in it cannot be an enclosing loop's, because a closure
        # outlives the frame that bound that loop's join. So all three targets
        # start again here, and `return` gets one exactly where the body has a
        # `return` to use it -- a function that never returns early reads as it
        # always did.
        inner.ret = inner.brk = inner.cont = None
        joined = None
        if _has_return(body):
            joined = (fresh_name("ret"), fresh_name("rv"), fn.ret)
            inner.ret = joined[0]
            out = self.conv(body, inner, joined[0])
        else:
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
        if joined is not None:
            ret_name, value_name, result = joined
            out = CJoin(result, span, ret_name, [CParam(value_name, result)],
                        CVar(result, span, value_name), out, False)
        return CLam(fn, span, cparams, out, name)

    # -- expressions -------------------------------------------------------

    def expr(self, e: ast.Expr, scope: Scope) -> CExpr:
        """One expression, as a term whose value is the value of the whole.

        Three answers, in the order they are asked for. A node `anf` has
        already lowered stands for what it lowered to. A node holding a loop
        or a transfer goes to `conv`, which is the only thing that knows how
        to give those a value. Everything else is the ordinary walk, which is
        what almost every node in almost every program is.
        """
        held = self.placeholders.get(id(e))
        if held is not None:
            return held
        if _special(e):
            return self.conv(e, scope, None)
        return self.dispatch(e, scope)

    def dispatch(self, e: ast.Expr, scope: Scope) -> CExpr:
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
                                 _member_surface(e.name))
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
        public = self.ty_of(e)
        _head, args = spine(public)
        assert len(args) == 1
        raw = CArray(raw_array_of(args[0]), e.span,
                     [self.expr(x, scope) for x in e.elems])
        constructor = CCon(TFun([raw.ty], public), e.span, "Data.Array#Array")
        return CApp(public, e.span, constructor, [raw])

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

    def _lower_EBlock(self, e: ast.EBlock, scope: Scope) -> CExpr:
        return self.block(e.stmts, self.ty_of(e), e.span, scope.child())

    # -- loops and transfers, as join points -------------------------------
    #
    # `while`, `loop`, the C-style `for` and `for ... in` are four constructs
    # in the surface language and none in Core, and `return`, `break` and
    # `continue`, which named their target by where they sat, are `CJump`s
    # that name it. Core holds no node for any of the seven, which is what
    # makes a term that names its target by position unrepresentable rather
    # than merely absent.
    #
    # The shape. Every function whose body contains a `return` is wrapped in
    # a join (`lambda_of`):
    #
    #     fun(x) { join %ret(%rv) = %rv in <body, every return a jump to %ret> }
    #
    # and every loop becomes one recursive join and, when nothing is already
    # waiting for the loop's value, one more for what follows it:
    #
    #     join %af(%v) = <what follows>
    #     in join rec %lp() = <one iteration, ending in a jump to %lp or %af>
    #        in jump %lp()
    #
    # `break` jumps to `%af` and `continue` to `%lp` -- or, for a C-style
    # `for`, to a third join holding the step, since `continue` there runs the
    # step before the test. A loop in a statement position is already a `let`
    # binding it, so `%af` is that continuation and the common case adds one
    # join rather than two.

    def conv(self, e: ast.Expr, scope: Scope, k: str | None) -> CExpr:
        """`e`, lowered so that its value reaches `k`.

        `k` is a join name, or None meaning "the value of this term is the
        value of the term it stands in". Every rule below either produces a
        value in place or ends in a jump, which is what makes the result
        satisfy `core.TAIL_FIELDS` without any rule having to check it.
        """
        held = self.placeholders.get(id(e))
        if held is not None:
            return self.finish(held, k)
        if not _special(e):
            return self.finish(self.dispatch(e, scope), k)
        if isinstance(e, ast.EAnnot):
            return self.conv(e.expr, scope, k)
        if isinstance(e, ast.EReturn):
            return self.transfer(scope.ret, e.value, e.span, scope,
                                 "a 'return' outside a function")
        if isinstance(e, ast.EBreak):
            return self.transfer(scope.brk, e.value, e.span, scope,
                                 "a 'break' outside a loop")
        if isinstance(e, ast.EContinue):
            if scope.cont is None:
                raise Unsupported("a 'continue' outside a loop", e.span)
            return CJump(TBottom(), e.span, scope.cont, [])
        if isinstance(e, ast.EBlock):
            return self.conv_block(e.stmts, self.ty_of(e), e.span,
                                   scope.child(), k)
        if isinstance(e, ast.EIf):
            return self.conv_if(e, scope, k)
        if isinstance(e, ast.EMatch):
            return self.conv_match(e, scope, k)
        if isinstance(e, ast.EBinary) and e.op in ("&&", "||"):
            return self.conv_shortcircuit(e, scope, k)
        if isinstance(e, LOOPS):
            return self.loop(e, scope, k)
        return self.anf(e, scope, k)

    def finish(self, value: CExpr, k: str | None) -> CExpr:
        return value if k is None else CJump(TBottom(), value.span, k, [value])

    def transfer(self, target: str | None, value: ast.Expr | None, span,
                 scope: Scope, complaint: str) -> CExpr:
        """`return e` and `break e`, which carry a value where `continue` does
        not. A bare one carries unit, which is what the value of the thing it
        leaves already was."""
        if target is None:
            raise Unsupported(complaint, span)
        if value is None:
            return CJump(TBottom(), span, target, [CUnit(UNIT, span)])
        return self.conv(value, scope, target)

    def conv_if(self, e: ast.EIf, scope: Scope, k: str | None) -> CExpr:
        """An `if` whose branches are the interesting part.

        A jump goes in each branch and `k` is a *name*, so the continuation is
        shared rather than copied -- which is the property join points exist
        for, and the reason this can be written at all without a code-size
        argument attached.

        An `if` with no `else` still has to reach `k` when it takes the branch
        that is not written: `if c { return None }` in a statement position
        falls through with unit, and a fall-through is exactly what `conv`
        promises not to leave. So the missing arm is supplied -- but only
        where a jump is actually owed.
        """
        ty = self.ty_of(e)

        def build(cond: CExpr) -> CExpr:
            then = self.conv(e.then, scope, k)
            if e.otherwise is not None:
                otherwise = self.conv(e.otherwise, scope, k)
            elif k is not None:
                otherwise = self.finish(CUnit(UNIT, e.span), k)
            else:
                otherwise = None
            return CIf(ty, e.span, cond, then, otherwise)

        return self.scrutinized(e.cond, ty, e.span, scope, build)

    def conv_match(self, e: ast.EMatch, scope: Scope, k: str | None) -> CExpr:
        ty = self.ty_of(e)

        def build(scrutinee: CExpr) -> CExpr:
            alts = []
            for arm in e.arms:
                for pat in arm.patterns:
                    alts.append(CAlt(pat, self.celled(
                        pat, arm.body,
                        lambda inner: self.conv(arm.body, inner, k),
                        scope.child(), arm.span)))
            return CMatch(ty, e.span, scrutinee, alts)

        return self.scrutinized(e.scrutinee, ty, e.span, scope, build)

    def scrutinized(self, e: ast.Expr, ty: Type, span, scope: Scope,
                    build) -> CExpr:
        """A branch whose *scrutinee* may itself hold a transfer, which has to
        run before the branch is taken and so becomes a join of its own."""
        if not _special(e):
            return build(self.expr(e, scope))
        name, value = fresh_name("on"), fresh_name("sv")
        held = self.ty_of(e)
        return CJoin(ty, span, name, [CParam(value, held)],
                     build(CVar(held, span, value)),
                     self.conv(e, scope, name), False)

    def conv_shortcircuit(self, e: ast.EBinary, scope: Scope,
                          k: str | None) -> CExpr:
        """`&&` and `||`, whose right operand a transfer must not escape past.

        `_lower_EBinary` makes these the `if` they already mean; this makes the
        same `if`, with the branches converted, so that a `break` on the right
        of an `&&` is taken only when the left was true.
        """
        ty = self.ty_of(e)
        name = fresh_name("b")

        def build(left: CExpr) -> CExpr:
            held = CVar(left.ty, e.span, name)
            right = self.conv(e.right, scope, k)
            kept = self.finish(held, k)
            branch = (CIf(ty, e.span, held, right, kept) if e.op == "&&"
                      else CIf(ty, e.span, held, kept, right))
            return CLet(ty, e.span, name, left.ty, left, branch)

        return self.scrutinized(e.left, ty, e.span, scope, build)

    def anf(self, e: ast.Expr, scope: Scope, k: str | None) -> CExpr:
        """A node whose *operands* hold a transfer: `push(ops, match c {...})`.

        `bf.tl` writes exactly that, with a `break` and a `continue` among the
        arms, and it is the one shape in the suite that none of the rules
        above covers. What makes it awkward is evaluation order: hoisting the
        `match` out and leaving `ops` in place would evaluate `ops` after it,
        and the evaluator is strict and left to right.

        So every operand up to and including the last special one is bound, in
        order, each to a join whose body is the next -- which is A-normal form,
        arrived at because the order has to be preserved rather than because
        the form is desirable. Operands after the last special one are left
        where they are: nothing has moved past them.

        The node itself is then lowered by its ordinary rule, with the bound
        operands standing for what they were bound to. That is what
        `placeholders` is: `_lower_ECall` neither knows nor needs to know that
        one of its arguments arrived as a jump.
        """
        children = _operands(e)
        if children is None or not any(_special(c) for c in children):
            raise Unsupported(
                f"a control transfer inside a {type(e).__name__}", e.span)
        last = max(i for i, c in enumerate(children) if _special(c))
        bound = children[:last + 1]
        joins = [(fresh_name("arg"), fresh_name("av")) for _ in bound]
        for child, (_, value) in zip(bound, joins):
            self.placeholders[id(child)] = CVar(self.ty_of(child), e.span, value)
        try:
            made = self.finish(self.dispatch(e, scope), k)
        finally:
            for child in bound:
                self.placeholders.pop(id(child), None)
        ty = self.ty_of(e)
        for (name, value), child in zip(reversed(joins), reversed(bound)):
            made = CJoin(ty, e.span, name,
                         [CParam(value, self.ty_of(child))], made,
                         self.conv(child, scope, name), False)
        return made

    # -- the four loop forms -----------------------------------------------

    def loop(self, e: ast.Expr, scope: Scope, k: str | None) -> CExpr:
        """Any of the four, as a recursive join and the jumps into it.

        One `join rec` for the iteration and, when there is nothing already
        waiting for the loop's value, one more for what follows it. Then the
        four forms differ only in what one iteration is, which is what
        `iteration` says and all that it says.
        """
        ty = self.ty_of(e)
        after, wrapper = k, None
        if after is None:
            after = fresh_name("af")
            wrapper = CParam(fresh_name("av"), ty)

        name = fresh_name("lp")
        inner = scope.child()
        inner.brk = after
        pending, body = self.iteration(e, inner, name, after)

        made: CExpr = CJoin(TBottom(), e.span, name, [], body,
                            CJump(TBottom(), e.span, name, []), True)
        for bound, bound_ty, value in reversed(pending):
            made = CLet(TBottom(), e.span, bound, bound_ty, value, made)
        if wrapper is None:
            return made
        return CJoin(ty, e.span, after, [wrapper],
                     CVar(ty, e.span, wrapper.name), made, False)

    def iteration(self, e: ast.Expr, scope: Scope, name: str, after: str):
        """One turn of the loop, and whatever bindings stand outside it.

        `while c { b }` *is* `if c { b; continue } else { break }`, and saying
        so here leaves one rule to be right about instead of four. `scope`
        already carries the `break` target; what each form decides is where
        its `continue` goes and what runs before the test.
        """
        span = e.span
        stop = CJump(TBottom(), span, after, [CUnit(UNIT, span)])
        if isinstance(e, ast.ELoop):
            scope.cont = name
            return [], self.seq_then(e.body, scope.child(),
                                     CJump(TBottom(), span, name, []))
        if isinstance(e, ast.EWhile):
            scope.cont = name
            cond = self.expr(e.cond, scope)
            body = self.seq_then(e.body, scope.child(),
                                 CJump(TBottom(), span, name, []))
            return [], CIf(TBottom(), span, cond, body, stop)
        if isinstance(e, ast.EForC):
            return self.for_c(e, scope, name, stop)
        assert isinstance(e, ast.EForIn)
        return self.for_in(e, scope, name, stop)

    def for_c(self, e: ast.EForC, scope: Scope, name: str, stop: CExpr):
        """The C-style `for`, whose `continue` runs the step before the test.

        So the step is a join of its own, and every `continue` this loop owns
        -- the written ones and the one that falls off the end of the body --
        names that rather than the loop's. Which is the whole of the
        difference between this and a `while`, and the reason nothing has to
        be duplicated at each `continue`.

        The init and the step are lowered in the loop's own scope, not in a
        child of it: `for var i = 0` binds `i` over the condition, the step
        and the body alike.
        """
        span = e.span
        pending: list[tuple[str, Type, CExpr]] = []
        if e.init is not None:
            init = self.stmt_value(e.init, scope)
            if isinstance(init, CLet):
                pending.append((init.name, init.bound, init.value))
            else:
                pending.append((fresh_name("seq"), init.ty, init))
        cond = self.expr(e.cond, scope)
        step = None
        if e.step is not None:
            value = self.stmt_value(e.step, scope)
            # `stmt_value` hands back a `let` whose body is a placeholder,
            # because a `for`'s init and step bind over the *loop* rather than
            # over a body of their own. The init's binding is hoisted out
            # above the loop join; what runs each turn is the step's value.
            if isinstance(value, CLet):
                value = value.value
            step = (fresh_name("st"), value)
        scope.cont = name if step is None else step[0]
        body = CIf(TBottom(), span, cond,
                   self.seq_then(e.body, scope.child(),
                                 CJump(TBottom(), span, scope.cont, [])),
                   stop)
        if step is not None:
            joined, value = step
            ran = CLet(TBottom(), span, fresh_name("seq"), value.ty, value,
                       CJump(TBottom(), span, name, []))
            body = CJoin(TBottom(), span, joined, [], ran, body, False)
        return pending, body

    def for_in(self, e: ast.EForIn, scope: Scope, name: str, stop: CExpr):
        """`for p in seq`, with the cursor made explicit.

        design.md 6.5's elaboration, which `core.py` used to leave as a note
        saying a later pass should perform it. The two calls already carry the
        `Iterator` dictionary -- that is why they are ordinary uses on the AST
        node rather than something this has to find -- so what is added here is
        the cursor binding and the `Option` match that reads `next`'s answer.

        Done here rather than after monomorphization, which is the point of
        doing it at all: `coretc.check_program` runs on this pass's output, so
        the cursor and the match are checked rather than taken on trust.
        """
        assert e.iter_fn is not None and e.next_fn is not None
        span = e.span
        seq = self.expr(e.iterable, scope)
        iter_fn = self.var(e.iter_fn, scope)
        next_fn = self.var(e.next_fn, scope)
        next_ty = prune(next_fn.ty)
        if not isinstance(next_ty, TFun) or len(next_ty.params) != 2:
            raise Unsupported("a 'for' whose `next` is not a cursor step", span)
        cursor_ty, option_ty = next_ty.params[1], next_ty.ret
        some, none = self.option_parts(option_ty, span)

        seq_name, cur_name = fresh_name("sq"), fresh_name("cu")
        pending = [
            (seq_name, seq.ty, seq),
            (cur_name, cursor_ty,
             CApp(cursor_ty, span, iter_fn, [CVar(seq.ty, span, seq_name)])),
        ]
        step = CApp(option_ty, span, next_fn,
                    [CVar(seq.ty, span, seq_name),
                     CVar(cursor_ty, span, cur_name)])
        scope.cont = name
        body = self.seq_then(e.body, scope.child(),
                             CJump(TBottom(), span, name, []))
        return pending, CMatch(TBottom(), span, step, [
            CAlt(ast.PCon(span, none, []), stop),
            CAlt(ast.PCon(span, some, [e.pat]), body),
        ])

    def option_parts(self, ty: Type, span) -> tuple[str, str]:
        """The `Option` constructors, found from the type `next` answers.

        By the type rather than by name: the Prelude's `Option` is an ordinary
        declaration under an ordinary qualified name, and a pass that spelled
        that name out would be one more thing to keep in agreement with it.
        """
        head, args = spine(prune(ty))
        if not isinstance(head, TCon) or not args:
            raise Unsupported("a 'for' whose `next` answers no Option", span)
        cons = [(cname, info)
                for cname, info in self.decls.constructors.items()
                if info.tycon == head.name]
        some = [n for n, i in cons if i.arity == 1]
        none = [n for n, i in cons if i.arity == 0]
        if len(some) != 1 or len(none) != 1:
            raise Unsupported(f"'{head.name}' is not shaped like an Option",
                              span)
        return some[0], none[0]

    def seq_then(self, e: ast.Expr, scope: Scope, after: CExpr) -> CExpr:
        """`e; after` -- `e` run for its effect, and then `after`.

        A loop body is this: it is run, its value is discarded, and what
        follows is the jump that goes round again. When the body itself holds
        a transfer the discarding has to be a join, since a `let` has nowhere
        for a jump to land.
        """
        if not _special(e):
            value = self.expr(e, scope)
            return CLet(after.ty, e.span, fresh_name("seq"), value.ty, value,
                        after)
        name, value_name = fresh_name("bind"), fresh_name("bv")
        return CJoin(after.ty, e.span, name,
                     [CParam(value_name, self.ty_of(e))], after,
                     self.conv(e, scope, name), False)

    # -- statements, in the presence of a transfer -------------------------

    def conv_block(self, stmts: list[ast.Stmt], ty: Type, span: Span | None,
                   scope: Scope, k: str | None) -> CExpr:
        """`block`, for a statement sequence one of whose statements holds a
        transfer. The same shape, with the value of the last statement reaching
        `k` and every earlier one still a binding around what follows it."""
        if not stmts:
            return self.finish(CUnit(ty, span), k)
        head, rest = stmts[0], stmts[1:]
        if not rest:
            if isinstance(head, ast.SExpr):
                return self.conv(head.expr, scope, k)
            return self.conv_bind(head, lambda: self.finish(CUnit(ty, span), k),
                                  ty, span, scope)
        if isinstance(head, ast.SFun):
            group = [head]
            while rest and isinstance(rest[0], ast.SFun):
                group.append(rest[0])
                rest = rest[1:]
            for stmt in group:
                scope.binders[stmt.decl.name] = self.fun_binders(stmt.decl)
            binds = [self.local_fun(stmt.decl, scope) for stmt in group]
            return CLetRec(ty, span, binds,
                           self.conv_block(rest, ty, span, scope, k))
        return self.conv_bind(
            head, lambda: self.conv_block(rest, ty, span, scope, k),
            ty, span, scope)

    def conv_bind(self, stmt: ast.Stmt, rest, ty: Type, span: Span | None,
                  scope: Scope) -> CExpr:
        """One statement, as a binding around everything after it.

        When the value being bound holds a transfer that leaves it -- a
        `return` in a branch of an `if` that is being bound -- the binding
        becomes a join: the value runs first and jumps to it, and the join's
        body is the rest. Which is exactly what a `let` already means, said in
        a form a jump can land in.
        """
        if isinstance(stmt, ast.SFun):
            scope.binders[stmt.decl.name] = self.fun_binders(stmt.decl)
            return CLetRec(ty, stmt.span, [self.local_fun(stmt.decl, scope)],
                           rest())
        source = stmt.value if isinstance(stmt, (ast.SLet, ast.SVar, ast.SAssign)) \
            else stmt.expr if isinstance(stmt, ast.SExpr) else None
        if source is None or not _special(source):
            return self.bind(stmt, rest, ty, span, scope)
        # The value transfers. It gets a join, whose parameter is the bound
        # name itself where there is one, so that nothing is bound twice.
        held = self.ty_of(source)
        if isinstance(stmt, (ast.SLet, ast.SVar)):
            bare = _unannot(stmt.pat)
            if not isinstance(bare, ast.PVar):
                raise Unsupported(
                    "a control transfer in a destructuring binding", stmt.span)
            if self.let_binders(stmt):
                raise Unsupported(
                    "a control transfer under a polymorphic 'let'", stmt.span)
            joined, value_name = fresh_name("bind"), fresh_name("bv")
            held_var = CVar(held, stmt.span, value_name)
            # The value is converted *before* the rest, because the rest is
            # lowered under whatever this binding did to the scope -- a `var`
            # becomes a cell, and every mention of it after this point has to
            # be a read of that cell.
            value = self.conv(source, scope, joined)
            scope.binders[bare.name] = []
            if isinstance(stmt, ast.SVar):
                scope.cells.add(bare.name)
                cell = CRef(ref_of(held), stmt.span, held_var)
                body: CExpr = CLet(ty, stmt.span, bare.name, cell.ty, cell,
                                   rest())
            else:
                body = CLet(ty, stmt.span, bare.name, held, held_var, rest())
            return CJoin(ty, stmt.span, joined, [CParam(value_name, held)],
                         body, value, False)
        name = fresh_name("bind")
        value_name = fresh_name("bv")
        self.placeholders[id(source)] = CVar(held, stmt.span, value_name)
        try:
            made = (self.assign(stmt, scope) if isinstance(stmt, ast.SAssign)
                    else self.expr(source, scope))
        finally:
            self.placeholders.pop(id(source), None)
        after = CLet(ty, stmt.span, fresh_name("seq"), made.ty, made, rest())
        return CJoin(ty, stmt.span, name, [CParam(value_name, held)], after,
                     self.conv(source, scope, name), False)

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


def _walk(node):
    """Every AST child of `node`, without descending into a nested function.

    A lambda and a local `fun` are their own functions: the `return` in one is
    that one's, and it travels with it. Every question this file asks about
    what a body *contains* stops there for that reason, so the stopping is
    written once here rather than in each of them.
    """
    import dataclasses
    if isinstance(node, (ast.ELambda, ast.SFun)):
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)
        return
    if not dataclasses.is_dataclass(node) or not isinstance(node, ast.Node):
        return
    for f in dataclasses.fields(node):
        held = getattr(node, f.name)
        if isinstance(held, ast.Node):
            yield held
        elif isinstance(held, (list, tuple)):
            for item in held:
                if isinstance(item, ast.Node):
                    yield item


def _holds(node, kinds) -> bool:
    if isinstance(node, kinds):
        return True
    return any(_holds(child, kinds) for child in _walk(node))


def _special(e) -> bool:
    """Whether `e` holds a loop or a transfer outside any nested function.

    The question `conv` exists to answer: a term for which this is false has
    nothing in it that a join point is needed for, and is lowered by the
    ordinary walk.
    """
    return _holds(e, LOOPS + TRANSFERS)


def _has_return(e) -> bool:
    """Whether a function body needs a `%ret` join at all."""
    return _holds(e, ast.EReturn)


def _operands(e: ast.Expr) -> list[ast.Expr] | None:
    """The subterms of `e` that are evaluated, in the order they are evaluated.

    Taken from what this file lowers each node to and what `eval.py` then does
    with it, which is not the same as the field order. A node absent from this
    table cannot be A-normalized, and a transfer inside one is declined rather
    than guessed at -- including `&&` and `||`, whose right operand is not
    unconditionally evaluated and which `conv_shortcircuit` handles instead.
    """
    if isinstance(e, ast.ECall):
        return [e.fn, *e.args]
    if isinstance(e, (ast.ETuple, ast.EArray)):
        return list(e.elems)
    if isinstance(e, ast.ERecord):
        return [v for _, v in e.fields]
    if isinstance(e, ast.EIndex):
        return [e.arr, e.index]
    if isinstance(e, ast.EField):
        return [e.obj]
    if isinstance(e, ast.EUnary):
        return [e.operand]
    if isinstance(e, ast.EBinary) and e.op not in ("&&", "||"):
        return [e.left, e.right]
    return None


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
