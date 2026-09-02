module Turkey.Lower (Lowerer(..), newLowerer, program)

-- Surface tree plus what solving decided, down to `Turkey.Core`
-- (`turkey/lower.py`).
--
-- This is the pass that makes the elaboration a *tree*. Everything it needs was
-- already computed and is currently scattered across three side tables and the
-- instance table: which dictionaries an occurrence needs and which types it
-- instantiated its scheme at; the leading dictionary parameters a binding group
-- gained; an instance's methods, superclass evidence and context; and every
-- expression's type. None of that is rediscovered here. What changes is that it
-- stops being an agreement between the elaborator and the evaluator, and becomes
-- syntax that `Turkey.Coretc` can reject.
--
-- ## The three translations that carry the milestone
--
-- **A class becomes a record type.** `class C a` with methods and superclasses
-- gives `%Dict.C a`, with a field per method and a `%super.S` per superclass. It
-- is opaque -- `%` is illegal in an identifier -- and it exists so that a
-- dictionary has a type to be wrong about.
--
-- **An instance becomes a top-level binding** of that type, named
-- `%inst.C.Con`, which is unique because instances are coherent. An instance
-- with a context becomes a *function* from dictionaries to a dictionary, which
-- is what `instance [Show a] Show (Array a)` always meant.
--
-- **Evidence becomes a term.** A dictionary in scope is a variable and a chain
-- of superclass projections; an instance's is a variable applied to the evidence
-- for its own context. The evidence language does not survive this pass, which
-- is the point: it was a second, unchecked term language.
--
-- ## What a use site becomes
--
-- A use of a polymorphic name is a type application and then a value
-- application:
--
--     f : forall a. %Dict.Show a -> fun(a) -> String
--     f(x)  ==>  (f[Int])(%d.Show.Int)(x)
--
-- in that order, and the order is not a convention this pass invents -- it is
-- the order the two were abstracted in. A method use is the same with the
-- function reached by projection instead of by name.
--
-- ## Loops and transfers stop existing
--
-- `while`, `loop`, the C-style `for` and `for ... in` are four constructs in the
-- surface language and none in Core, and `return`, `break` and `continue`, which
-- named their target by where they sat, become jumps that name it. Core holds no
-- node for any of the seven, which is what makes a term that names its target by
-- position unrepresentable rather than merely absent.

import Data.Set as Set
import Turkey.Ast
import Turkey.Ast as Ast
import Turkey.Classes as Classes
import Turkey.Classes (ClassTable(..), ClassInfo(..), InstInfo(..),
                       InstancePlan(..), MethodInfo(..), MethodImpl(..),
                       Evidence(..))
import Turkey.Constraints as C
import Turkey.Constraints (Env(..), Binding(..))
import Turkey.Core
import Turkey.Core as Cr
import Turkey.Decls as Decls
import Turkey.Decls (DeclTable(..), ConInfo(..))
import Turkey.Diag as Diag
import Turkey.Prelude as P
import Turkey.Solved (Abstraction(..), Use(..))
import Turkey.Source (Span)
import Turkey.Typed as Typed
import Turkey.Typed (TypeTable(..))
import Turkey.Types as Ty
import Turkey.Types (Kind(..), VarCell(..), ConCell(..), Scheme(..), Pred(..),
                     TVar, TCon, TApp, TFam, TFun, TTuple, TBottom)

-- -- naming -------------------------------------------------------------------------

fun dictConName(cls : String) -> String = "%Dict." + cls

fun superField(cls : String) -> String = "%super." + cls

fun instName(inst : InstInfo) -> String =
    "%inst." + inst.cls + "." + Classes.instCon(inst)

fun defaultName(cls : String, method : String) -> String =
    "%default." + cls + "." + method

-- Which module a resolved top-level name belongs to (`Main#f` -> `Main`).
fun moduleOf(name : String) -> String = match String.rsplitOnce(name, "#") {
    Some((before, _)) -> before
    None -> ""
}

-- -- scope --------------------------------------------------------------------------

-- Which names in scope are reference cells.
--
-- A `var` is one, and so is a parameter that something assigns -- parameters are
-- reassignable, but a parameter nothing writes is indistinguishable from a
-- `let`, and making a cell for it anyway would put an indirection on every
-- argument of every function for no observable gain.
type Scope = Scope {
    cells   : Set.Set String,
    -- Name -> what it abstracts over, for the local bindings in scope. A
    -- *recursive* occurrence needs this and nothing else does: inference binds a
    -- name monomorphically inside its own definition, so a self-call records no
    -- type arguments -- correctly, there is nothing yet to record. System F
    -- still wants the application, at the binding's own variables.
    binders : Map String (Array VarCell),
    -- Where a `return`, a `break` and a `continue` written here jump to. Each is
    -- a join name, or absent where writing that transfer would have been a
    -- compile error anyway. This is what replaces "the transfer names its target
    -- by where it sits": the scope chain already knows where it sits.
    ret     : Option String,
    brk     : Option String,
    cont    : Option String,
}

fun newScope() -> Scope = Scope {
    cells = Set.new(), binders = Map.new(), ret = None, brk = None, cont = None,
}

fun childScope(s : Scope) -> Scope {
    let cells = Set.new()
    for c in Set.elements(s.cells) { Set.add(cells, c) }
    let binders = Map.new()
    for (k, v) in Map.entries(s.binders) { Map.put(binders, k, v) }
    Scope { cells = cells, binders = binders, ret = s.ret, brk = s.brk,
            cont = s.cont }
}

-- -- the lowerer --------------------------------------------------------------------

type Lowerer = Lowerer {
    decls        : DeclTable,
    classes      : ClassTable,
    env          : Env,
    types        : TypeTable,
    -- While a method body is being lowered: which rigid constant in it stands
    -- for which quantified variable. Empty everywhere else, because nothing else
    -- is checked against skolems.
    rigid        : Map String VarCell,
    -- Surface expressions already lowered, and what they lowered to. Only
    -- A-normalization puts anything here, and only for as long as it takes to
    -- rebuild one node around its operands.
    placeholders : Map Int Core,
    counter      : Int,
}

fun newLowerer(decls : DeclTable, classes : ClassTable, env : Env,
               types : TypeTable) -> Lowerer = Lowerer {
    decls = decls, classes = classes, env = env, types = types,
    rigid = Map.new(), placeholders = Map.new(), counter = 0,
}

-- A binder this pass invents. `%` cannot start a source identifier.
fun freshName(l : Lowerer, hint : String) -> String {
    let n = l.counter
    l.counter = l.counter + 1
    "%" + hint + Int.toString(n)
}

fun node(ty : Ty.Type, where : Option Span, k : CoreKind) -> Core =
    mkCore(ty, where, k)

-- A placeholder for a type this pass fills in a moment later.
fun unknown() -> Ty.Type = TVar(Ty.freshVar(0))

fun unitType() -> Ty.Type = Ty.tUnit()

-- -- the program --------------------------------------------------------------------

fun program(l : Lowerer, ordered : Array Stmt) -> CProgram {
    let out = CProgram { dicts = Array.new(16), binds = Array.new(32) }
    for (name, info) in Map.entries(l.classes.classes) {
        for (method, impl) in Map.entries(info.defaults) {
            Array.push(out.dicts, defaultMethod(l, name, method, impl))
        }
    }
    for (_, insts) in Map.entries(l.classes.instances) {
        for inst in insts { Array.push(out.dicts, instanceBind(l, inst)) }
    }
    let scope = newScope()
    for stmt in ordered {
        for b in top(l, stmt, scope) { Array.push(out.binds, b) }
    }
    out
}

fun newBind(name : String, ty : Ty.Type, binders : Array VarCell, value : Core,
            where : Option Span) -> CBind = CBind {
    name = name, ty = ty, binders = binders, value = value, span = where,
    mutable = False, module_ = "", equations = Array.new(0),
}

-- One top-level statement. Only bindings occur here.
fun top(l : Lowerer, stmt : Stmt, scope : Scope) -> Array CBind = match stmt.kind {
    SFun(decl) -> [funBind(l, decl, scope)]
    SLet(pat, value) -> letBinds(l, stmt, pat, value, False, scope)
    SVar(pat, value) -> letBinds(l, stmt, pat, value, True, scope)
    -- A bare expression at the top level: evaluated for effect, so it is a
    -- binding nothing reads, which is what a `let` already means.
    SExpr(e) -> {
        let value = expr(l, e, scope)
        [newBind(freshName(l, "top"), value.ty, Array.new(0), value,
                 Some(stmt.span))]
    }
    _ -> oops("unhandled top-level statement", Some(stmt.span))
}

fun funBind(l : Lowerer, decl : FunDecl, scope : Scope) -> CBind {
    let s = schemeOf(l, decl.name)
    let binders = match s {
        Some(sc) -> sc.quantified
        None -> Array.new(0)
    }
    let ty = match s {
        Some(sc) -> sc.body
        None -> funType(l, decl)
    }
    let dicts = abstractionOf(l, decl.id)
    let b = newBind(decl.name, abstracted(l, ty, dicts), binders,
                    function(l, decl, ty, scope, None, None), Some(decl.span))
    b.equations = equationsOf(s)
    b.module_ = moduleOf(decl.name)
    b
}

-- A binding's type as Core sees it: dictionaries first.
--
-- The scheme says `[Show a] fun(a) -> Unit`, with the context to one side of the
-- type. In Core there is no "to one side" -- a dictionary is an argument, so it
-- is in the type. Every use site is already built to match: a type application,
-- and then an application to the dictionaries.
fun abstracted(l : Lowerer, ty : Ty.Type, dicts : Option Abstraction) -> Ty.Type =
    match dicts {
        None -> ty
        Some(a) -> if len(a.params) == 0 { ty } else {
            TFun(Array.map(a.preds, fun(p) = predType(l, p)), ty)
        }
    }

-- A top-level `let` or `var`, possibly binding several names.
--
-- A destructuring binding becomes one binding of the whole value and one
-- projection per name, rather than a pattern at the top level: Core has no
-- statements, so there is nowhere for a multi-name binder to live.
fun letBinds(l : Lowerer, stmt : Stmt, pat : Pat, value : Expr, mutable : Bool,
             scope : Scope) -> Array CBind {
    let lowered = expr(l, value, scope)
    let dicts = abstractionOf(l, stmt.id)
    let bare = unannot(pat)
    match bare.kind {
        PVar(name) -> {
            let s = schemeOf(l, name)
            let binders = match s {
                Some(sc) -> sc.quantified
                None -> Array.new(0)
            }
            var body = abstract(l, lowered, dicts)
            if mutable {
                Set.add(scope.cells, name)
                body = node(refOf(body.ty), Some(stmt.span), CRef(body))
            }
            let b = newBind(name, body.ty, binders, body, Some(stmt.span))
            b.mutable = mutable
            return [b]
        }
        _ -> {}
    }
    let holder = freshName(l, "bound")
    let out = [newBind(holder, lowered.ty, Array.new(0), lowered, Some(stmt.span))]
    for name in Array.sort(patternVars(pat)) {
        let nty = nameType(l, name)
        let picked = node(nty, Some(stmt.span), CMatch(
            node(lowered.ty, Some(stmt.span), CVar(holder)),
            [CAlt { pat = pat, body = node(nty, Some(stmt.span), CVar(name)) }]))
        Array.push(out, newBind(name, picked.ty, Array.new(0), picked,
                                Some(stmt.span)))
    }
    out
}

-- -- classes and instances ----------------------------------------------------------

fun dictType(l : Lowerer, cls : String, arg : Ty.Type) -> Ty.Type {
    let info = classNamed(l, cls)
    TApp(Ty.tcon(dictConName(cls), KFun(Classes.classKind(info), KStar),
                 Ty.noScope), arg, KStar)
}

-- One instance dictionary, as a top-level binding.
--
-- With a context it is a function: `instance [Show a] Show (Array a)` takes the
-- `Show a` dictionary and answers the `Show (Array a)` one. That is what it
-- always meant; the evaluator built it lazily and memoised on identity, and here
-- it is an ordinary binding.
fun instanceBind(l : Lowerer, inst : InstInfo) -> CBind {
    let plan = match inst.plan {
        Some(p) -> p
        None -> oops("instance has no plan", Some(inst.decl.span))
    }
    let result = dictType(l, inst.cls, inst.head)
    let fields = Array.new(8)
    let givens = instanceGivens(l, inst, plan)
    for (sup, ev) in Map.entries(plan.supers) {
        Array.push(fields, (superField(sup),
            evidence(l, ev, dictType(l, sup, inst.head), givens)))
    }
    for (name, impl) in Map.entries(plan.methods) {
        Array.push(fields, (Classes.surfaceMember(name),
            methodBody(l, inst, name, impl, plan.params)))
    }
    let record = node(result, Some(inst.decl.span),
                      CRecord(dictConName(inst.cls), fields))
    var value = record
    if len(plan.params) > 0 {
        let params = Array.new(len(plan.params))
        for var i = 0; i < len(plan.params) && i < len(inst.context); i = i + 1 {
            let p = inst.context[i]
            Array.push(params, CParam {
                name = plan.params[i], ty = dictType(l, p.name, p.args[0]),
            })
        }
        value = node(TFun(Array.map(params, fun(p) = p.ty), result),
                     Some(inst.decl.span),
                     CLam(params, record, instName(inst)))
    }
    let b = newBind(instName(inst), value.ty, instanceBinders(inst), value,
                    Some(inst.decl.span))
    b.module_ = inst.module_
    b
}

-- The instance's own variables, in the order its binding abstracts them. Read
-- off the head, which is the only place they appear, and used by both the
-- definition and every reference so the two cannot disagree.
fun instanceBinders(inst : InstInfo) -> Array VarCell = Ty.varsOf([inst.head])

-- The instance's own dictionary, as seen from inside one of its methods: applied
-- to its own binders and to its own context, which is what makes the reference
-- well-typed rather than merely well-named.
fun selfDict(l : Lowerer, inst : InstInfo, params : Array String) -> Core {
    let result = dictType(l, inst.cls, inst.head)
    var fn = node(unknown(), Some(inst.decl.span), CVar(instName(inst)))
    let binders = instanceBinders(inst)
    if len(binders) > 0 {
        fn = node(unknown(), Some(inst.decl.span),
                  CTyApp(fn, Array.map(binders, fun(b) = TVar(b))))
    }
    if len(inst.context) == 0 {
        fn.ty = result
        return fn
    }
    let args = Array.new(len(inst.context))
    for var i = 0; i < len(params) && i < len(inst.context); i = i + 1 {
        Array.push(args, node(predType(l, inst.context[i]), Some(inst.decl.span),
                              CVar(params[i])))
    }
    fn.ty = TFun(Array.map(args, fun(a) = a.ty), result)
    node(result, Some(inst.decl.span), CApp(fn, args))
}

-- A method's type as it sits in a dictionary, at one head.
--
-- A method with its own context takes those dictionaries per *call* rather than
-- per instance, so they are parameters of the field, not of the dictionary that
-- holds it.
fun methodType(l : Lowerer, info : MethodInfo, head : Ty.Type) -> Ty.Type {
    let mapping = Map.new()
    Map.put(mapping, info.classVar.id, head)
    let ty = Decls.substitute(info.scheme.body, mapping)
    let own = Array.slice(info.scheme.preds, 1, len(info.scheme.preds))
    if len(own) == 0 { return ty }
    TFun(Array.map(own, fun(p) =
        dictType(l, p.name, Decls.substitute(p.args[0], mapping))), ty)
}

-- A class's default method, lowered once, for every instance to share.
--
-- It has to be once. The generator checks a default body a single time, against
-- the *class* variable, so the types recorded in it are about that variable and
-- not about any instance. Copying the body into each dictionary would put `a`
-- where `Int` belongs.
--
-- So it is what it always was: a function of the dictionary it belongs to,
-- polymorphic in the class variable. The body already uses a name for that
-- dictionary, so it becomes the parameter.
fun defaultMethod(l : Lowerer, cls : String, name : String,
                  impl : MethodImpl) -> CBind {
    let info = classNamed(l, cls)
    let mi = match Map.get(info.methods, name) {
        Some(m) -> m
        None -> oops("unknown method '" + name + "'", Some(impl.decl.span))
    }
    let cvar = mi.classVar
    let selfTy = dictType(l, cls, TVar(cvar))
    let saved = setSkolems(l, impl, mi, [cvar])
    var body = function(l, impl.decl, mi.scheme.body, newScope(),
                        Some(impl.dictParams),
                        Some(Array.slice(mi.scheme.preds, 1,
                                         len(mi.scheme.preds))))
    l.rigid = saved
    body = methodAbstraction(mi, body, Some(impl.decl.span))
    let lam = node(TFun([selfTy], body.ty), Some(impl.decl.span),
        CLam([CParam { name = impl.selfName, ty = selfTy }], body,
             defaultName(cls, name)))
    let b = newBind(defaultName(cls, name), lam.ty, [cvar], lam,
                    Some(impl.decl.span))
    b.module_ = info.module_
    b
}

-- One method body, as a lambda inside its dictionary.
--
-- The self name is the dictionary the body belongs to, bound so that a method
-- may call another of its own class without anything being passed -- which is
-- what makes a *default* method work at all. In Core it is a `let` around the
-- body rather than a name the evaluator arranges to have in scope.
fun methodBody(l : Lowerer, inst : InstInfo, name : String, impl : MethodImpl,
               params : Array String) -> Core {
    let info = classNamed(l, inst.cls)
    let mi = match Map.get(info.methods, name) {
        Some(m) -> m
        None -> oops("unknown method '" + name + "'", Some(impl.decl.span))
    }
    let head = inst.head
    if isDefaultOf(info, name, impl) {
        -- Not this instance's body: the class's, shared. Applied at this head
        -- and to this dictionary.
        let own = selfDict(l, inst, params)
        let result = methodType(l, mi, head)
        let fn = node(TFun([own.ty], result), Some(impl.decl.span),
            CTyApp(node(unknown(), Some(impl.decl.span),
                        CVar(defaultName(inst.cls, name))), [head]))
        let called = node(result, Some(impl.decl.span), CApp(fn, [own]))
        -- Eta-expanded, for the same reason the self dictionary goes under the
        -- lambda: the argument is the dictionary this field belongs to, so
        -- evaluating the application while the record is being built would build
        -- it again, for ever. Under a lambda it waits until the method is called.
        return eta(l, called, result, Some(impl.decl.span))
    }
    let mapping = Map.new()
    Map.put(mapping, mi.classVar.id, head)
    let ty = Decls.substitute(mi.scheme.body, mapping)
    let saved = setSkolems(l, impl, mi, Ty.varsOf([inst.head]))
    var body = function(l, impl.decl, ty, newScope(), Some(impl.dictParams),
                        Some(Array.slice(mi.scheme.preds, 1,
                                         len(mi.scheme.preds))))
    l.rigid = saved
    if impl.selfName != "" {
        body = bindSelf(l, inst, params, impl.selfName, body,
                        Some(impl.decl.span))
    }
    methodAbstraction(mi, body, Some(impl.decl.span))
}

-- Whether this implementation is the class's shared default rather than the
-- instance's own body. Python compares object identity; a default is registered
-- under the class and reused, so comparing the declaration's node id is the same
-- question.
fun isDefaultOf(info : ClassInfo, name : String, impl : MethodImpl) -> Bool =
    match Map.get(info.defaults, name) {
        Some(other) -> other.decl.id == impl.decl.id
        None -> False
    }

-- `f` as `fun(x, ...) { f(x, ...) }`, to defer evaluating `f`.
fun eta(l : Lowerer, called : Core, result : Ty.Type, where : Option Span) -> Core =
    match Ty.prune(result) {
        TFun(params, ret) -> {
            let ps = Array.map(params, fun(p) =
                CParam { name = freshName(l, "eta"), ty = p })
            let args = Array.map(ps, fun(p) = node(p.ty, where, CVar(p.name)))
            node(TFun(params, ret), where,
                 CLam(ps, node(ret, where, CApp(called, args)), "<anonymous>"))
        }
        _ -> called
    }

-- Bind the instance's own dictionary *inside* the method's lambda.
--
-- Inside, not around it, and that placement is the difference between a program
-- that runs and one that does not. `instance [Show a] Show (Array a)` names
-- itself, so a binding evaluated while the dictionary is being built would build
-- the dictionary again, for ever. Under the lambda it is evaluated when the
-- method is *called*, by which time the dictionary exists.
fun bindSelf(l : Lowerer, inst : InstInfo, params : Array String, name : String,
             body : Core, where : Option Span) -> Core {
    let own = selfDict(l, inst, params)
    match body.kind {
        CLam(ps, inner, lamName) -> node(body.ty, body.span, CLam(ps,
            node(inner.ty, where, CLet(name, own.ty, own, inner, Array.new(0))),
            lamName))
        _ -> node(body.ty, where,
                  CLet(name, own.ty, own, body, Array.new(0)))
    }
}

-- A method is polymorphic in its *own* variables, and the dictionary that holds
-- it does not fix those -- only the class variable. `map` in a `Functor
-- (Either l)` dictionary is still `forall a b`, so the field is a type
-- abstraction and every use of it a type application.
fun methodAbstraction(info : MethodInfo, body : Core, where : Option Span) -> Core {
    let binders = Array.new(4)
    for q in info.scheme.quantified {
        if q.id != info.classVar.id { Array.push(binders, q) }
    }
    if len(binders) == 0 { return body }
    node(body.ty, where, CTyLam(binders, body))
}

-- Set the constant-to-variable map for one method body, and answer what it was.
fun setSkolems(l : Lowerer, impl : MethodImpl, info : MethodInfo,
               headVars : Array VarCell) -> Map String VarCell {
    let byId = Map.new()
    for v in headVars { Map.put(byId, v.id, v) }
    for q in info.scheme.quantified { Map.put(byId, q.id, q) }
    let rigid = Map.new()
    for (varId, con) in Map.entries(impl.skolems) {
        match (Map.get(byId, varId), con) {
            (Some(v), TCon(c)) -> Map.put(rigid, c.name, v)
            _ -> {}
        }
    }
    let saved = l.rigid
    l.rigid = rigid
    saved
}

-- -- evidence -----------------------------------------------------------------------

-- One evidence tree, as a term. This is the milestone in one function.
fun evidence(l : Lowerer, ev : Evidence, want : Ty.Type,
             givens : Map String Ty.Type) -> Core = match ev {
    FromDict(name, path, p) -> {
        -- Every node of the chain gets its real type, not just the last. A
        -- superclass constrains the class variable itself, so every dictionary
        -- along the path is *about the same type* and differs only in its class
        -- -- which the path names. Leaving the middle for the checker to guess
        -- was the same mistake in miniature as leaving the whole elaboration
        -- unchecked: it made the checker believe a term instead of reading it.
        let head = dictArg(want)
        let start = match Map.get(givens, name) {
            Some(t) -> t
            None -> match p {
                Some(pred) -> predType(l, pred)
                None -> want
            }
        }
        var out = node(start, None, CVar(name))
        for var i = 0; i < len(path); i = i + 1 {
            let at = if i == len(path) - 1 { want }
                     else { dictType(l, path[i], head) }
            out = node(at, None, CField(out, superField(path[i])))
        }
        out
    }
    FromInstance(inst, args) -> instanceRef(l, inst, want, args)
    -- The predicate's argument is bottom: there is no value here, so there is no
    -- dictionary to find. `error` diverges and so has whatever type its context
    -- wants.
    Absent(name) -> node(want, None, CApp(
        node(TFun([Ty.tString()], want), None, CVar(P.error)),
        [node(Ty.tString(), None, CLit(LString("no evidence for " + name)))]))
}

-- A reference to an instance's dictionary, at the types it was selected at and
-- applied to the evidence for its own context.
--
-- The types are not recorded on the evidence -- it names the instance, not the
-- match that chose it -- so they are recovered the way instance selection
-- recovered them in the first place: by matching the head against the dictionary
-- type wanted here. It is the same one-way match, which is what makes this the
-- *same* answer rather than a second opinion.
fun instanceRef(l : Lowerer, inst : InstInfo, want : Ty.Type,
                args : Array Evidence) -> Core {
    let head = dictArg(want)
    let mapping = match Classes.match_(inst.head, head) {
        Some(m) -> m
        None -> oops("instance " + inst.cls + " does not cover " + Ty.show(want),
                     Some(inst.decl.span))
    }
    let binders = instanceBinders(inst)
    let typeArgs = Array.map(binders, fun(b) = Map.getOr(mapping, b.id, TVar(b)))
    let result = dictType(l, inst.cls, head)
    var fn = node(unknown(), None, CVar(instName(inst)))
    if len(typeArgs) > 0 {
        fn = node(unknown(), None, CTyApp(fn, typeArgs))
    }
    if len(args) == 0 {
        fn.ty = result
        return fn
    }
    let argTerms = Array.new(len(args))
    for var i = 0; i < len(args) && i < len(inst.context); i = i + 1 {
        let p = inst.context[i]
        let at = Decls.substitute(p.args[0], mapping)
        -- No givens: an instance's context arrives as its own parameters, which
        -- `instanceGivens` supplies where those are in scope.
        Array.push(argTerms, evidence(l, args[i],
            dictType(l, p.name, resolved(l, at)), Map.new()))
    }
    fn.ty = TFun(Array.map(argTerms, fun(a) = a.ty), result)
    node(result, None, CApp(fn, argTerms))
}

-- The dictionary variables in scope at a use, and their types. Recorded by the
-- solver as it descended, and needed here because a dictionary reference names a
-- variable and a path but not the class the variable's own dictionary has.
fun givensOf(l : Lowerer, u : Use) -> Map String Ty.Type {
    let out = Map.new()
    for sc in u.scopes {
        for (name, p) in sc.givens {
            if Classes.isClass(l.classes, p.name) {
                Map.put(out, name, predType(l, p))
            }
        }
    }
    out
}

-- The dictionaries an instance's own methods have in scope: its context's, under
-- the names the plan gave them.
fun instanceGivens(l : Lowerer, inst : InstInfo,
                   plan : InstancePlan) -> Map String Ty.Type {
    let out = Map.new()
    for var i = 0; i < len(plan.params) && i < len(inst.context); i = i + 1 {
        let p = inst.context[i]
        Map.put(out, plan.params[i], dictType(l, p.name, p.args[0]))
    }
    out
}

fun predType(l : Lowerer, p : Pred) -> Ty.Type =
    dictType(l, p.name, resolved(l, p.args[0]))

-- A type from the solver's own records, read the way a node's type is read:
-- reduced, and back in terms of variables rather than skolems.
fun resolved(l : Lowerer, t : Ty.Type) -> Ty.Type =
    rigidly(l, Typed.resolve(l.types, t))

-- The type a dictionary type classifies: the `t` of `%Dict.C t`.
fun dictArg(t : Ty.Type) -> Ty.Type = match Ty.prune(t) {
    TApp(_, arg, _) -> arg
    _ -> oops("not a dictionary type: " + Ty.show(t), None)
}

-- -- functions ---------------------------------------------------------------------

fun funType(l : Lowerer, decl : FunDecl) -> Ty.Type = match decl.body {
    Some(b) -> typeOfExpr(l, b)
    None -> oops("a function with no body reached the lowering",
                 Some(decl.span))
}

-- A `fun` declaration, as a lambda -- possibly under dictionary ones.
fun function(l : Lowerer, decl : FunDecl, ty : Ty.Type, scope : Scope,
             dictParams : Option (Array String),
             preds : Option (Array Pred)) -> Core {
    let body = match decl.body {
        Some(b) -> b
        None -> oops("a function with no body reached the lowering",
                     Some(decl.span))
    }
    let dicts = abstractionOf(l, decl.id)
    var params = dictParams
    var ps = preds
    match dicts {
        Some(a) -> if Option.isNone(dictParams) && len(a.params) > 0 {
            params = Some(a.params)
            ps = Some(a.preds)
        }
        None -> {}
    }
    let saved = signatureSkolems(l, dicts, ty)
    let inner = lambdaOf(l, decl.params, body, ty, Some(decl.span), scope,
                         decl.name)
    l.rigid = saved
    wrapDicts(l, inner, params, ps, Some(decl.span))
}

-- The constant-to-variable map for a signature-checked body.
--
-- A method body sets this elsewhere; this is the other case, a `fun` whose own
-- declared type was checked against skolems. If the declaration has no signature
-- there is nothing rigid in it and the map is left exactly as it was -- which
-- matters: a *method* declared inside an instance has no signature of its own,
-- so this path finds nothing to say, and must not thereby erase what the
-- instance said.
fun signatureSkolems(l : Lowerer, dicts : Option Abstraction,
                     ty : Ty.Type) -> Map String VarCell {
    let saved = l.rigid
    match dicts {
        None -> return saved
        Some(a) -> {
            if len(Map.keys(a.skolems)) == 0 { return saved }
            let byId = Map.new()
            for v in Ty.varsOf([ty]) { Map.put(byId, v.id, v) }
            let rigid = Map.new()
            for (varId, con) in Map.entries(a.skolems) {
                match (Map.get(byId, varId), con) {
                    (Some(v), TCon(c)) -> Map.put(rigid, c.name, v)
                    _ -> {}
                }
            }
            l.rigid = rigid
            saved
        }
    }
}

fun wrapDicts(l : Lowerer, inner : Core, params : Option (Array String),
              preds : Option (Array Pred), where : Option Span) -> Core {
    let ns = match params {
        Some(p) -> p
        None -> return inner
    }
    if len(ns) == 0 { return inner }
    let ps = match preds {
        Some(p) -> p
        None -> Array.new(0)
    }
    let cparams = Array.new(len(ns))
    for var i = 0; i < len(ns) && i < len(ps); i = i + 1 {
        Array.push(cparams, CParam {
            name = ns[i], ty = dictType(l, ps[i].name, ps[i].args[0]),
        })
    }
    node(TFun(Array.map(cparams, fun(p) = p.ty), inner.ty), where,
         CLam(cparams, inner, "<anonymous>"))
}

fun abstract(l : Lowerer, value : Core, dicts : Option Abstraction) -> Core =
    match dicts {
        None -> value
        Some(a) -> if len(a.params) == 0 { value } else {
            wrapDicts(l, value, Some(a.params), Some(a.preds), value.span)
        }
    }

-- Parameters come from the function's *type*, not from the patterns.
--
-- A parameter is a pattern, and a pattern binds no type of its own that anything
-- recorded. The function type has one entry per parameter and is recorded, so
-- that is where the types come from -- and a parameter that is not a plain name
-- becomes a fresh binder plus a `match`, which is what it always meant.
fun lambdaOf(l : Lowerer, params : Array Pat, body : Expr, ty : Ty.Type,
             where : Option Span, scope : Scope, name : String) -> Core {
    let (fnParams, fnRet) = match Ty.prune(ty) {
        TFun(ps, r) -> (ps, r)
        _ -> oops("a function whose type is not one: " + Ty.show(ty), where)
    }
    let inner = childScope(scope)
    let cparams = Array.new(len(params))
    let matches = Array.new(2)
    let assignedNames = assignedIn(ChExpr(body))
    let cells = Array.new(2)
    for var i = 0; i < len(params) && i < len(fnParams); i = i + 1 {
        let bare = unannot(params[i])
        let pty = fnParams[i]
        match bare.kind {
            PVar(pname) -> {
                Array.push(cparams, CParam { name = pname, ty = pty })
                if Set.contains(assignedNames, pname) {
                    Array.push(cells, (pname, pty))
                }
            }
            PWild -> Array.push(cparams,
                CParam { name = freshName(l, "wild"), ty = pty })
            _ -> {
                let holder = freshName(l, "param")
                Array.push(cparams, CParam { name = holder, ty = pty })
                Array.push(matches, (holder, bare, pty))
            }
        }
    }
    -- A destructured parameter's binders are reassignable too, so any the body
    -- writes become cells, exactly as a named parameter's would. Collected
    -- across every pattern first, because the body is lowered once, inside all
    -- of them.
    let patternCells = Array.new(2)
    for (_, pat, _) in matches {
        let bound = Typed.ofPattern(l.types, pat)
        for boundName in Array.sort(Map.keys(bound)) {
            if Set.contains(assignedNames, boundName) {
                Array.push(patternCells,
                    (boundName, rigidly(l, Map.getOr(bound, boundName, TBottom))))
            }
        }
    }
    -- Every binder shadows whatever was in scope under its name, and binds it
    -- monomorphically: nothing a lambda or a pattern binds generalizes. So an
    -- outer name's cell-ness and its binders both stop here, and are reinstated
    -- only for the binders this body actually makes cells of.
    for p in cparams {
        discard(Map.delete(inner.binders, p.name))
        discard(Set.remove(inner.cells, p.name))
    }
    for (_, pat, _) in matches {
        for k in Map.keys(Typed.ofPattern(l.types, pat)) {
            discard(Map.delete(inner.binders, k))
            discard(Set.remove(inner.cells, k))
        }
    }
    for (cname, _) in cells { Set.add(inner.cells, cname) }
    for (cname, _) in patternCells { Set.add(inner.cells, cname) }
    -- A lambda is its own function: the `return` in it is this one's, and the
    -- `break` in it cannot be an enclosing loop's, because a closure outlives the
    -- frame that bound that loop's join. So all three targets start again here,
    -- and `return` gets one exactly where the body has a `return` to use it.
    inner.ret = None
    inner.brk = None
    inner.cont = None
    var joined : Option (String, String, Ty.Type) = None
    var out = node(TBottom, where, CUnit)
    if holdsReturn(ChExpr(body)) {
        let retName = freshName(l, "ret")
        let valueName = freshName(l, "rv")
        joined = Some((retName, valueName, fnRet))
        inner.ret = Some(retName)
        out = conv(l, body, inner, Some(retName))
    } else {
        out = expr(l, body, inner)
    }
    for var i = len(patternCells) - 1; i >= 0; i = i - 1 {
        let (cname, cty) = patternCells[i]
        out = node(out.ty, where, CLet(cname, refOf(cty),
            node(refOf(cty), where, CRef(node(cty, where, CVar(cname)))),
            out, Array.new(0)))
    }
    for var i = len(matches) - 1; i >= 0; i = i - 1 {
        let (holder, pat, pty) = matches[i]
        out = node(out.ty, where, CMatch(node(pty, where, CVar(holder)),
                                         [CAlt { pat = pat, body = out }]))
    }
    for var i = len(cells) - 1; i >= 0; i = i - 1 {
        -- A parameter something writes is a cell, made from the parameter
        -- itself. The parameter keeps its name and the cell shadows it.
        let (cname, cty) = cells[i]
        out = node(out.ty, where, CLet(cname, refOf(cty),
            node(refOf(cty), where, CRef(node(cty, where, CVar(cname)))),
            out, Array.new(0)))
    }
    match joined {
        Some((retName, valueName, result)) -> {
            out = node(result, where, CJoin(retName,
                [CParam { name = valueName, ty = result }],
                node(result, where, CVar(valueName)), out, False))
        }
        None -> {}
    }
    node(TFun(fnParams, fnRet), where, CLam(cparams, out, name))
}

-- -- expressions -------------------------------------------------------------------

-- One expression, as a term whose value is the value of the whole.
--
-- Three answers, in the order they are asked for. A node A-normalization has
-- already lowered stands for what it lowered to. A node holding a loop or a
-- transfer goes to `conv`, which is the only thing that knows how to give those
-- a value. Everything else is the ordinary walk.
fun expr(l : Lowerer, e : Expr, scope : Scope) -> Core {
    match Map.get(l.placeholders, e.id) {
        Some(held) -> return held
        None -> {}
    }
    if special(ChExpr(e)) { return conv(l, e, scope, None) }
    dispatch(l, e, scope)
}

fun typeOfExpr(l : Lowerer, e : Expr) -> Ty.Type = rigidly(l, Typed.of_(l.types, e))

-- A type read out of a method body, back in terms of variables.
--
-- Outside a method body this is the identity. Inside one it undoes the
-- skolemization the generator applied: the instance's own variable comes back,
-- so the body agrees with the dictionary that holds it.
fun rigidly(l : Lowerer, t : Ty.Type) -> Ty.Type {
    if len(Map.keys(l.rigid)) == 0 { return t }
    unskolem(t, l.rigid)
}

-- Replace the rigid constants of a method body with their variables.
--
-- Substitution cannot do this: it maps variables to types, and here it is
-- constants that have to go. A skolem is a constructor whose name is the one the
-- declaration wrote, which is what makes the map keyable by name.
fun unskolem(t : Ty.Type, rigid : Map String VarCell) -> Ty.Type =
    match Ty.prune(t) {
        TCon(c) -> match Map.get(rigid, c.name) {
            Some(v) -> TVar(v)
            None -> Ty.prune(t)
        }
        TApp(fn, arg, k) -> TApp(unskolem(fn, rigid), unskolem(arg, rigid), k)
        TFam(name, arg, k) -> TFam(name, unskolem(arg, rigid), k)
        TFun(params, ret) -> TFun(Array.map(params, fun(p) = unskolem(p, rigid)),
                                  unskolem(ret, rigid))
        TTuple(elems) -> TTuple(Array.map(elems, fun(x) = unskolem(x, rigid)))
        _ -> Ty.prune(t)
    }

fun dispatch(l : Lowerer, e : Expr, scope : Scope) -> Core {
    let ty = typeOfExpr(l, e)
    let where = Some(e.span)
    match e.kind {
        ELit(lit) -> node(ty, where, CLit(lit))
        EUnit -> node(ty, where, CUnit)
        ECon(name) -> node(ty, where, CCon(name))
        EVar(_, _) -> var_(l, e, scope)
        ETuple(elems) -> node(ty, where,
            CTuple(Array.map(elems, fun(x) = expr(l, x, scope))))
        EArray(elems) -> arrayLiteral(l, e, ty, elems, scope)
        ERecord(con, fields) -> {
            let out = Array.new(len(fields))
            for (n, v) in fields { Array.push(out, (n, expr(l, v, scope))) }
            node(ty, where, CRecord(con, out))
        }
        EField(obj, name) -> node(ty, where, CField(expr(l, obj, scope), name))
        EProject(obj, index) -> node(ty, where,
            CProject(expr(l, obj, scope), index))
        EIndex(arr, index, _, _) -> node(ty, where,
            CIndex(expr(l, arr, scope), expr(l, index, scope)))
        ELambda(params, _, body) ->
            lambdaOf(l, params, body, ty, where, scope, "<anonymous>")
        ECall(fn, args) -> node(ty, where, CApp(expr(l, fn, scope),
            Array.map(args, fun(a) = expr(l, a, scope))))
        -- An annotation constrained inference and has nothing left to say.
        EAnnot(inner, _) -> expr(l, inner, scope)
        EUnary(_, operand, fn) -> {
            let lowered = expr(l, operand, scope)
            match fn {
                -- `!x`, which is not a method.
                None -> node(ty, where, CApp(
                    node(TFun([Ty.tBool()], Ty.tBool()), where,
                         CPrim("Prim.not")), [lowered]))
                Some(f) -> node(ty, where, CApp(var_(l, f, scope), [lowered]))
            }
        }
        EBinary(op, left, right, fn) -> binary(l, e, ty, op, left, right, fn, scope)
        EIf(cond, then, otherwise) -> node(ty, where, CIf(
            expr(l, cond, scope), expr(l, then, scope),
            match otherwise {
                Some(o) -> Some(expr(l, o, scope))
                None -> None
            }))
        EMatch(scrutinee, arms) -> {
            let alts = Array.new(4)
            for arm in arms {
                for pat in arm.patterns {
                    Array.push(alts, CAlt { pat = pat, body = celled(l, pat,
                        arm.body, fun(s) = expr(l, arm.body, s),
                        childScope(scope), Some(arm.span)) })
                }
            }
            node(ty, where, CMatch(expr(l, scrutinee, scope), alts))
        }
        EBlock(stmts) -> block(l, stmts, ty, where, childScope(scope))
        _ -> oops("cannot lower this expression", where)
    }
}

fun arrayLiteral(l : Lowerer, e : Expr, public : Ty.Type, elems : Array Expr,
                 scope : Scope) -> Core {
    let where = Some(e.span)
    let (_, args) = Ty.spine(public)
    let element = if len(args) == 1 { args[0] } else { TBottom }
    let raw = node(Ty.rawArrayOf(element), where,
        CArray(Array.map(elems, fun(x) = expr(l, x, scope))))
    let length = node(Ty.tInt(), where, CLit(LInt(len(elems))))
    let storageTy = Ty.applyTo(Decls.head(l.decls, "Data.Array#ArrayStorage"),
                               [element], where)
    let storage = node(storageTy, where, CRecord("Data.Array#ArrayStorage",
        [("storage", raw), ("length", length)]))
    let constructor = node(TFun([storageTy], public), where,
                           CCon("Data.Array#Array"))
    node(public, where, CApp(constructor, [storage]))
}

fun binary(l : Lowerer, e : Expr, ty : Ty.Type, op : String, left : Expr,
           right : Expr, fn : Option Expr, scope : Scope) -> Core {
    let where = Some(e.span)
    if op == "&&" || op == "||" {
        -- These two short-circuit, so they are the `if` they already mean. The
        -- left operand is bound first because it is both the test and, when it
        -- decides the answer, the answer.
        let lo = expr(l, left, scope)
        let ro = expr(l, right, scope)
        let name = freshName(l, "b")
        let held = node(lo.ty, where, CVar(name))
        let branch = if op == "&&" {
            node(ty, where, CIf(held, ro, Some(held)))
        } else {
            node(ty, where, CIf(held, held, Some(ro)))
        }
        return node(ty, where, CLet(name, lo.ty, lo, branch, Array.new(0)))
    }
    match fn {
        Some(f) -> node(ty, where, CApp(var_(l, f, scope),
            [expr(l, left, scope), expr(l, right, scope)]))
        None -> oops("operator '" + op + "' has no method", where)
    }
}

-- A name: possibly a projection, possibly applied to types and dictionaries.
--
-- The three steps are in the order they were abstracted in, which is the only
-- order that typechecks: select or name the value, apply its type arguments,
-- then apply its dictionaries.
fun var_(l : Lowerer, e : Expr, scope : Scope) -> Core {
    let name = match e.kind {
        EVar(n, _) -> n
        _ -> oops("not a variable", Some(e.span))
    }
    let result = typeOfExpr(l, e)
    let where = Some(e.span)
    let u = Map.get(l.types.uses, e.id)
    let plain = match u {
        None -> True
        Some(x) -> len(x.evidence) == 0 && len(x.typeArgs) == 0
    }
    if plain {
        if Set.contains(scope.cells, name) {
            return node(result, where,
                        CDeref(node(refOf(result), where, CVar(name))))
        }
        let binders = bindersOf(l, name, scope)
        if len(binders) > 0 {
            -- A recursive occurrence: monomorphic to inference, still a type
            -- application here, at the binding's own variables.
            return node(result, where, CTyApp(
                node(unknown(), where, CVar(name)),
                Array.map(binders, fun(b) = TVar(b))))
        }
        return node(result, where, CVar(name))
    }
    let use = match u {
        Some(x) -> x
        None -> oops("a use vanished", where)
    }
    let givens = givensOf(l, use)
    var dicts = Array.new(len(use.evidence))
    for var i = 0; i < len(use.evidence) && i < len(use.preds); i = i + 1 {
        Array.push(dicts, evidence(l, use.evidence[i],
                                   predType(l, use.preds[i]), givens))
    }
    var typeArgs = Array.map(use.typeArgs, fun(a) = resolved(l, a))
    var base = node(unknown(), where, CVar(name))
    match use.method {
        Some(mi) -> {
            -- The class's own dictionary is the first predicate, by the
            -- convention the method's scheme sets up; the method is a field of
            -- it and the rest, if any, are the method's own context.
            base = node(unknown(), where,
                        CField(dicts[0], Classes.surfaceMember(name)))
            dicts = Array.slice(dicts, 1, len(dicts))
            -- And the class variable is *already* fixed, by the dictionary the
            -- method was projected from. It is one of the method scheme's
            -- quantified variables, so it is one of the recorded type arguments
            -- -- but applying it here would fix it a second time, which is the
            -- same variable bound twice and no longer a type application.
            typeArgs = withoutClassVar(mi, typeArgs)
        }
        None -> if len(typeArgs) == 0 {
            -- A recursive or mutually recursive occurrence: the group is bound
            -- monomorphically while its own bodies are checked, so nothing was
            -- recorded, and the application is at the group's own variables. It
            -- does carry dictionaries -- the solver patched those in -- which is
            -- why the test cannot be "no evidence either".
            typeArgs = Array.map(bindersOf(l, name, scope), fun(b) = TVar(b))
        }
    }
    let afterTypes = if len(dicts) > 0 {
        TFun(Array.map(dicts, fun(d) = d.ty), result)
    } else { result }
    if len(typeArgs) > 0 {
        base.ty = unknown()
        base = node(afterTypes, where, CTyApp(base, typeArgs))
    } else {
        base.ty = afterTypes
    }
    if len(dicts) > 0 { node(result, where, CApp(base, dicts)) } else { base }
}

-- A method's type arguments, less the one its dictionary already fixed. They are
-- in the scheme's quantified order, so the class variable's position in that
-- list is the position to drop.
fun withoutClassVar(mi : MethodInfo, typeArgs : Array Ty.Type) -> Array Ty.Type {
    let quantified = mi.scheme.quantified
    if len(typeArgs) != len(quantified) { return typeArgs }
    let out = Array.new(len(typeArgs))
    for var i = 0; i < len(typeArgs); i = i + 1 {
        if quantified[i].id != mi.classVar.id { Array.push(out, typeArgs[i]) }
    }
    out
}

fun oops(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("internal error", message), "")
    Some(s) -> Diag.fail(Diag.at("internal error", message, s), s.file)
}

fun discard(x : a) -> Unit = {}

-- -- loops and transfers, as join points ---------------------------------------------
--
-- The shape. Every function whose body contains a `return` is wrapped in a join:
--
--     fun(x) { join %ret(%rv) = %rv in <body, every return a jump to %ret> }
--
-- and every loop becomes one recursive join and, when nothing is already waiting
-- for the loop's value, one more for what follows it:
--
--     join %af(%v) = <what follows>
--     in join rec %lp() = <one iteration, ending in a jump to %lp or %af>
--        in jump %lp()
--
-- `break` jumps to the after-join and `continue` to the loop's -- or, for a
-- C-style `for`, to a third join holding the step, since `continue` there runs
-- the step before the test. A loop in a statement position is already a `let`
-- binding it, so the after-join is that continuation and the common case adds
-- one join rather than two.

-- An expression, lowered so that its value reaches `k`.
--
-- `k` is a join name, or `None` meaning "the value of this term is the value of
-- the term it stands in". Every rule below either produces a value in place or
-- ends in a jump, which is what makes the result satisfy the tail-position rule
-- without any rule having to check it.
fun conv(l : Lowerer, e : Expr, scope : Scope, k : Option String) -> Core {
    match Map.get(l.placeholders, e.id) {
        Some(held) -> return finish(l, held, k)
        None -> {}
    }
    if !special(ChExpr(e)) { return finish(l, dispatch(l, e, scope), k) }
    let where = Some(e.span)
    match e.kind {
        EAnnot(inner, _) -> conv(l, inner, scope, k)
        EReturn(value) -> transfer(l, scope.ret, value, where, scope,
                                   "a 'return' outside a function")
        EBreak(value) -> transfer(l, scope.brk, value, where, scope,
                                  "a 'break' outside a loop")
        EContinue -> match scope.cont {
            None -> unsupported("a 'continue' outside a loop", where)
            Some(target) -> node(TBottom, where, CJump(target, Array.new(0)))
        }
        EBlock(stmts) -> convBlock(l, stmts, typeOfExpr(l, e), where,
                                   childScope(scope), k)
        EIf(cond, then, otherwise) -> convIf(l, e, cond, then, otherwise, scope, k)
        EMatch(scrutinee, arms) -> convMatch(l, e, scrutinee, arms, scope, k)
        EBinary(op, left, right, _) -> if op == "&&" || op == "||" {
            convShortCircuit(l, e, op, left, right, scope, k)
        } else { anf(l, e, scope, k) }
        EWhile(_, _) -> lowerLoop(l, e, scope, k)
        ELoop(_) -> lowerLoop(l, e, scope, k)
        EForC(_, _, _, _) -> lowerLoop(l, e, scope, k)
        EForIn(_, _, _, _, _) -> lowerLoop(l, e, scope, k)
        _ -> anf(l, e, scope, k)
    }
}

fun finish(l : Lowerer, value : Core, k : Option String) -> Core = match k {
    None -> value
    Some(target) -> node(TBottom, value.span, CJump(target, [value]))
}

-- `return e` and `break e`, which carry a value where `continue` does not. A
-- bare one carries unit, which is what the value of the thing it leaves already
-- was.
fun transfer(l : Lowerer, target : Option String, value : Option Expr,
             where : Option Span, scope : Scope, complaint : String) -> Core {
    let name = match target {
        Some(t) -> t
        None -> unsupported(complaint, where)
    }
    match value {
        None -> node(TBottom, where,
                     CJump(name, [node(unitType(), where, CUnit)]))
        Some(v) -> conv(l, v, scope, Some(name))
    }
}

-- An `if` whose branches are the interesting part.
--
-- A jump goes in each branch and `k` is a *name*, so the continuation is shared
-- rather than copied -- which is the property join points exist for.
--
-- An `if` with no `else` still has to reach `k` when it takes the branch that is
-- not written: `if c { return None }` in a statement position falls through with
-- unit, and a fall-through is exactly what this promises not to leave. So the
-- missing arm is supplied -- but only where a jump is actually owed.
fun convIf(l : Lowerer, e : Expr, cond : Expr, then : Expr,
           otherwise : Option Expr, scope : Scope, k : Option String) -> Core {
    let ty = typeOfExpr(l, e)
    let where = Some(e.span)
    scrutinized(l, cond, ty, where, scope, fun(c) = {
        let thenC = conv(l, then, scope, k)
        let elseC = match otherwise {
            Some(o) -> Some(conv(l, o, scope, k))
            None -> match k {
                Some(_) -> Some(finish(l, node(unitType(), where, CUnit), k))
                None -> None
            }
        }
        node(ty, where, CIf(c, thenC, elseC))
    })
}

fun convMatch(l : Lowerer, e : Expr, scrutinee : Expr, arms : Array Arm,
              scope : Scope, k : Option String) -> Core {
    let ty = typeOfExpr(l, e)
    let where = Some(e.span)
    scrutinized(l, scrutinee, ty, where, scope, fun(s) = {
        let alts = Array.new(4)
        for arm in arms {
            for pat in arm.patterns {
                Array.push(alts, CAlt { pat = pat, body = celled(l, pat,
                    arm.body, fun(inner) = conv(l, arm.body, inner, k),
                    childScope(scope), Some(arm.span)) })
            }
        }
        node(ty, where, CMatch(s, alts))
    })
}

-- A branch whose *scrutinee* may itself hold a transfer, which has to run before
-- the branch is taken and so becomes a join of its own.
fun scrutinized(l : Lowerer, e : Expr, ty : Ty.Type, where : Option Span,
                scope : Scope, build : fun(Core) -> Core) -> Core {
    if !special(ChExpr(e)) { return build(expr(l, e, scope)) }
    let name = freshName(l, "on")
    let value = freshName(l, "sv")
    let held = typeOfExpr(l, e)
    node(ty, where, CJoin(name, [CParam { name = value, ty = held }],
        build(node(held, where, CVar(value))),
        conv(l, e, scope, Some(name)), False))
}

-- `&&` and `||`, whose right operand a transfer must not escape past. The `if`
-- they already mean, with the branches converted, so that a `break` on the right
-- of an `&&` is taken only when the left was true.
fun convShortCircuit(l : Lowerer, e : Expr, op : String, left : Expr,
                     right : Expr, scope : Scope, k : Option String) -> Core {
    let ty = typeOfExpr(l, e)
    let where = Some(e.span)
    let name = freshName(l, "b")
    scrutinized(l, left, ty, where, scope, fun(lo) = {
        let held = node(lo.ty, where, CVar(name))
        let ro = conv(l, right, scope, k)
        let kept = finish(l, held, k)
        let branch = if op == "&&" {
            node(ty, where, CIf(held, ro, Some(kept)))
        } else {
            node(ty, where, CIf(held, kept, Some(ro)))
        }
        node(ty, where, CLet(name, lo.ty, lo, branch, Array.new(0)))
    })
}

-- A node whose *operands* hold a transfer: `push(ops, match c {...})`.
--
-- What makes it awkward is evaluation order: hoisting the `match` out and
-- leaving `ops` in place would evaluate `ops` after it, and evaluation is strict
-- and left to right. So every operand up to and including the last special one
-- is bound, in order, each to a join whose body is the next -- which is A-normal
-- form, arrived at because the order has to be preserved rather than because the
-- form is desirable. Operands after the last special one are left where they
-- are: nothing has moved past them.
--
-- The node itself is then lowered by its ordinary rule, with the bound operands
-- standing for what they were bound to. That is what the placeholders are: the
-- ordinary rule neither knows nor needs to know that one of its arguments
-- arrived as a jump.
fun anf(l : Lowerer, e : Expr, scope : Scope, k : Option String) -> Core {
    let where = Some(e.span)
    let kids = match operands(e) {
        Some(cs) -> cs
        None -> unsupported("a control transfer inside this expression", where)
    }
    var last = 0 - 1
    for var i = 0; i < len(kids); i = i + 1 {
        if special(ChExpr(kids[i])) { last = i }
    }
    if last < 0 {
        unsupported("a control transfer inside this expression", where)
    }
    let bound = Array.slice(kids, 0, last + 1)
    let joins = Array.map(bound, fun(_) =
        (freshName(l, "arg"), freshName(l, "av")))
    for var i = 0; i < len(bound); i = i + 1 {
        let (_, value) = joins[i]
        Map.put(l.placeholders, bound[i].id,
                node(typeOfExpr(l, bound[i]), where, CVar(value)))
    }
    var made = finish(l, dispatch(l, e, scope), k)
    for child in bound { discard(Map.delete(l.placeholders, child.id)) }
    let ty = typeOfExpr(l, e)
    for var i = len(bound) - 1; i >= 0; i = i - 1 {
        let (name, value) = joins[i]
        made = node(ty, where, CJoin(name,
            [CParam { name = value, ty = typeOfExpr(l, bound[i]) }], made,
            conv(l, bound[i], scope, Some(name)), False))
    }
    made
}

-- -- the four loop forms ------------------------------------------------------------

-- Any of the four, as a recursive join and the jumps into it.
--
-- One recursive join for the iteration and, when there is nothing already
-- waiting for the loop's value, one more for what follows it. Then the four
-- forms differ only in what one iteration is.
fun lowerLoop(l : Lowerer, e : Expr, scope : Scope, k : Option String) -> Core {
    let ty = typeOfExpr(l, e)
    let where = Some(e.span)
    var after = k
    var wrapper : Option CParam = None
    match after {
        None -> {
            let made = freshName(l, "af")
            after = Some(made)
            wrapper = Some(CParam { name = freshName(l, "av"), ty = ty })
        }
        Some(_) -> {}
    }
    let afterName = match after {
        Some(n) -> n
        None -> ""
    }
    let name = freshName(l, "lp")
    let inner = childScope(scope)
    inner.brk = after
    let (pending, body) = iteration(l, e, inner, name, afterName)

    var made = node(TBottom, where, CJoin(name, Array.new(0), body,
        node(TBottom, where, CJump(name, Array.new(0))), True))
    for var i = len(pending) - 1; i >= 0; i = i - 1 {
        let (bname, bty, value) = pending[i]
        made = node(TBottom, where, CLet(bname, bty, value, made, Array.new(0)))
    }
    match wrapper {
        None -> made
        Some(w) -> node(ty, where, CJoin(afterName, [w],
            node(ty, where, CVar(w.name)), made, False))
    }
}

-- One turn of the loop, and whatever bindings stand outside it.
--
-- `while c { b }` *is* `if c { b; continue } else { break }`, and saying so here
-- leaves one rule to be right about instead of four. The scope already carries
-- the `break` target; what each form decides is where its `continue` goes and
-- what runs before the test.
fun iteration(l : Lowerer, e : Expr, scope : Scope, name : String,
              after : String) -> (Array (String, Ty.Type, Core), Core) {
    let where = Some(e.span)
    let stop = node(TBottom, where,
                    CJump(after, [node(unitType(), where, CUnit)]))
    match e.kind {
        ELoop(body) -> {
            scope.cont = Some(name)
            (Array.new(0), seqThen(l, body, childScope(scope),
                node(TBottom, where, CJump(name, Array.new(0)))))
        }
        EWhile(cond, body) -> {
            scope.cont = Some(name)
            let c = expr(l, cond, scope)
            let b = seqThen(l, body, childScope(scope),
                node(TBottom, where, CJump(name, Array.new(0))))
            (Array.new(0), node(TBottom, where, CIf(c, b, Some(stop))))
        }
        EForC(init, cond, step, body) ->
            forC(l, e, init, cond, step, body, scope, name, stop)
        EForIn(pat, iterable, body, iterFn, nextFn) ->
            forIn(l, e, pat, iterable, body, iterFn, nextFn, scope, name, stop)
        _ -> oops("not a loop", where)
    }
}

-- The C-style `for`, whose `continue` runs the step before the test.
--
-- So the step is a join of its own, and every `continue` this loop owns -- the
-- written ones and the one that falls off the end of the body -- names that
-- rather than the loop's. Which is the whole of the difference between this and
-- a `while`, and the reason nothing has to be duplicated at each `continue`.
--
-- The init and the step are lowered in the loop's own scope, not in a child of
-- it: `for var i = 0` binds `i` over the condition, the step and the body alike.
fun forC(l : Lowerer, e : Expr, init : Option Stmt, cond : Expr,
         step : Option Stmt, body : Expr, scope : Scope, name : String,
         stop : Core) -> (Array (String, Ty.Type, Core), Core) {
    let where = Some(e.span)
    let pending = Array.new(2)
    match init {
        Some(s) -> {
            let lowered = stmtValue(l, s, scope)
            match lowered.kind {
                CLet(n, bound, value, _, _) -> Array.push(pending, (n, bound, value))
                _ -> Array.push(pending, (freshName(l, "seq"), lowered.ty, lowered))
            }
        }
        None -> {}
    }
    let c = expr(l, cond, scope)
    var stepJoin : Option (String, Core) = None
    match step {
        Some(s) -> {
            let lowered = stmtValue(l, s, scope)
            -- A statement's value is a `let` whose body is a placeholder,
            -- because a `for`'s init and step bind over the *loop* rather than
            -- over a body of their own. The init's binding is hoisted out above
            -- the loop join; what runs each turn is the step's value.
            let value = match lowered.kind {
                CLet(_, _, v, _, _) -> v
                _ -> lowered
            }
            stepJoin = Some((freshName(l, "st"), value))
        }
        None -> {}
    }
    scope.cont = match stepJoin {
        Some((joined, _)) -> Some(joined)
        None -> Some(name)
    }
    let contName = match scope.cont {
        Some(n) -> n
        None -> name
    }
    var made = node(TBottom, where, CIf(c,
        seqThen(l, body, childScope(scope),
                node(TBottom, where, CJump(contName, Array.new(0)))),
        Some(stop)))
    match stepJoin {
        Some((joined, value)) -> {
            let ran = node(TBottom, where, CLet(freshName(l, "seq"), value.ty,
                value, node(TBottom, where, CJump(name, Array.new(0))),
                Array.new(0)))
            made = node(TBottom, where,
                        CJoin(joined, Array.new(0), ran, made, False))
        }
        None -> {}
    }
    (pending, made)
}

-- `for p in seq`, with the cursor made explicit.
--
-- design.md 6.5's elaboration. The two calls already carry the `Iterator`
-- dictionary -- that is why they are ordinary uses on the node rather than
-- something this has to find -- so what is added here is the cursor binding and
-- the `Option` match that reads what `next` answers.
--
-- Done here rather than after monomorphization, which is the point of doing it
-- at all: the Core checker runs on this pass's output, so the cursor and the
-- match are checked rather than taken on trust.
fun forIn(l : Lowerer, e : Expr, pat : Pat, iterable : Expr, body : Expr,
          iterFn : Option Expr, nextFn : Option Expr, scope : Scope,
          name : String, stop : Core) -> (Array (String, Ty.Type, Core), Core) {
    let where = Some(e.span)
    let seqCore = expr(l, iterable, scope)
    let iterCore = match iterFn {
        Some(f) -> var_(l, f, scope)
        None -> unsupported("a 'for' with no 'iter'", where)
    }
    let nextCore = match nextFn {
        Some(f) -> var_(l, f, scope)
        None -> unsupported("a 'for' with no 'next'", where)
    }
    let (cursorTy, optionTy) = match Ty.prune(nextCore.ty) {
        TFun(ps, r) -> if len(ps) == 2 { (ps[1], r) } else {
            unsupported("a 'for' whose `next` is not a cursor step", where)
        }
        _ -> unsupported("a 'for' whose `next` is not a cursor step", where)
    }
    let (some, none) = optionParts(l, optionTy, where)

    let seqName = freshName(l, "sq")
    let curName = freshName(l, "cu")
    let pending = [
        (seqName, seqCore.ty, seqCore),
        (curName, cursorTy, node(cursorTy, where, CApp(iterCore,
            [node(seqCore.ty, where, CVar(seqName))]))),
    ]
    let step = node(optionTy, where, CApp(nextCore,
        [node(seqCore.ty, where, CVar(seqName)),
         node(cursorTy, where, CVar(curName))]))
    scope.cont = Some(name)
    let b = seqThen(l, body, childScope(scope),
                    node(TBottom, where, CJump(name, Array.new(0))))
    (pending, node(TBottom, where, CMatch(step, [
        CAlt { pat = mkPat(e, PCon(none, Array.new(0))), body = stop },
        CAlt { pat = mkPat(e, PCon(some, [pat])), body = b },
    ])))
}

fun mkPat(e : Expr, k : PatKind) -> Pat =
    Pat { id = freshNodeId(), span = e.span, kind = k }

-- The `Option` constructors, found from the type `next` answers.
--
-- By the type rather than by name: the Prelude's `Option` is an ordinary
-- declaration under an ordinary qualified name, and a pass that spelled that
-- name out would be one more thing to keep in agreement with it.
fun optionParts(l : Lowerer, ty : Ty.Type, where : Option Span) -> (String, String) {
    let (h, args) = Ty.spine(Ty.prune(ty))
    let headName = match h {
        TCon(c) -> if len(args) == 0 {
            unsupported("a 'for' whose `next` answers no Option", where)
        } else { c.name }
        _ -> unsupported("a 'for' whose `next` answers no Option", where)
    }
    let some = Array.new(2)
    let none = Array.new(2)
    for (cname, info) in Map.entries(l.decls.constructors) {
        if info.tycon == headName {
            if info.arity == 1 { Array.push(some, cname) }
            if info.arity == 0 { Array.push(none, cname) }
        }
    }
    if len(some) != 1 || len(none) != 1 {
        unsupported("'" + headName + "' is not shaped like an Option", where)
    }
    (some[0], none[0])
}

-- `e; after` -- `e` run for its effect, and then `after`.
--
-- A loop body is this: it is run, its value is discarded, and what follows is
-- the jump that goes round again. When the body itself holds a transfer the
-- discarding has to be a join, since a `let` has nowhere for a jump to land.
fun seqThen(l : Lowerer, e : Expr, scope : Scope, after : Core) -> Core {
    let where = Some(e.span)
    if !special(ChExpr(e)) {
        let value = expr(l, e, scope)
        return node(after.ty, where, CLet(freshName(l, "seq"), value.ty, value,
                                          after, Array.new(0)))
    }
    let name = freshName(l, "bind")
    let valueName = freshName(l, "bv")
    node(after.ty, where, CJoin(name,
        [CParam { name = valueName, ty = typeOfExpr(l, e) }], after,
        conv(l, e, scope, Some(name)), False))
}

-- -- statements, in the presence of a transfer ---------------------------------------

-- A statement sequence one of whose statements holds a transfer. The same shape
-- as an ordinary block, with the value of the last statement reaching `k` and
-- every earlier one still a binding around what follows it.
fun convBlock(l : Lowerer, stmts : Array Stmt, ty : Ty.Type, where : Option Span,
              scope : Scope, k : Option String) -> Core =
    convBlockFrom(l, stmts, 0, ty, where, scope, k)

fun convBlockFrom(l : Lowerer, stmts : Array Stmt, from : Int, ty : Ty.Type,
                  where : Option Span, scope : Scope, k : Option String) -> Core {
    if from >= len(stmts) { return finish(l, node(ty, where, CUnit), k) }
    let head = stmts[from]
    let isLast = from == len(stmts) - 1
    if isLast {
        match head.kind {
            SExpr(e) -> return conv(l, e, scope, k)
            _ -> return convBind(l, head,
                fun() = finish(l, node(ty, where, CUnit), k), ty, where, scope)
        }
    }
    match head.kind {
        SFun(_) -> {
            var last = from
            while last + 1 < len(stmts) && isFun(stmts[last + 1]) {
                last = last + 1
            }
            let group = Array.slice(stmts, from, last + 1)
            for stmt in group {
                match stmt.kind {
                    SFun(d) -> Map.put(scope.binders, d.name, funBinders(l, d))
                    _ -> {}
                }
            }
            let binds = Array.new(len(group))
            for stmt in group {
                match stmt.kind {
                    SFun(d) -> Array.push(binds, localFun(l, d, scope))
                    _ -> {}
                }
            }
            node(ty, where, CLetRec(binds,
                convBlockFrom(l, stmts, last + 1, ty, where, scope, k)))
        }
        _ -> convBind(l, head,
            fun() = convBlockFrom(l, stmts, from + 1, ty, where, scope, k),
            ty, where, scope)
    }
}

fun isFun(s : Stmt) -> Bool = match s.kind {
    SFun(_) -> True
    _ -> False
}

-- One statement, as a binding around everything after it.
--
-- When the value being bound holds a transfer that leaves it -- a `return` in a
-- branch of an `if` that is being bound -- the binding becomes a join: the value
-- runs first and jumps to it, and the join's body is the rest. Which is exactly
-- what a `let` already means, said in a form a jump can land in.
fun convBind(l : Lowerer, stmt : Stmt, rest : fun() -> Core, ty : Ty.Type,
             where : Option Span, scope : Scope) -> Core {
    let at = Some(stmt.span)
    match stmt.kind {
        SFun(d) -> {
            Map.put(scope.binders, d.name, funBinders(l, d))
            return node(ty, at, CLetRec([localFun(l, d, scope)], rest()))
        }
        _ -> {}
    }
    let source = match stmt.kind {
        SLet(_, v) -> Some(v)
        SVar(_, v) -> Some(v)
        SAssign(_, v) -> Some(v)
        SExpr(e) -> Some(e)
        _ -> None
    }
    let transfers = match source {
        Some(s) -> special(ChExpr(s))
        None -> False
    }
    if !transfers { return bind(l, stmt, rest, ty, where, scope) }
    let src = match source {
        Some(s) -> s
        None -> oops("a statement with no value transferred", at)
    }
    let held = typeOfExpr(l, src)
    match stmt.kind {
        SLet(pat, _) ->
            convBindName(l, stmt, pat, src, held, False, rest, ty, at, scope)
        SVar(pat, _) ->
            convBindName(l, stmt, pat, src, held, True, rest, ty, at, scope)
        _ -> {
            let name = freshName(l, "bind")
            let valueName = freshName(l, "bv")
            Map.put(l.placeholders, src.id, node(held, at, CVar(valueName)))
            let made = match stmt.kind {
                SAssign(target, value) -> assign(l, stmt, target, value, scope)
                _ -> expr(l, src, scope)
            }
            discard(Map.delete(l.placeholders, src.id))
            let after = node(ty, at, CLet(freshName(l, "seq"), made.ty, made,
                                          rest(), Array.new(0)))
            node(ty, at, CJoin(name, [CParam { name = valueName, ty = held }],
                after, conv(l, src, scope, Some(name)), False))
        }
    }
}

fun convBindName(l : Lowerer, stmt : Stmt, pat : Pat, src : Expr, held : Ty.Type,
                 mutable : Bool, rest : fun() -> Core, ty : Ty.Type,
                 at : Option Span, scope : Scope) -> Core {
    let bare = unannot(pat)
    let name = match bare.kind {
        PVar(n) -> n
        _ -> unsupported("a control transfer in a destructuring binding", at)
    }
    if len(letBinders(l, stmt, pat)) > 0 {
        unsupported("a control transfer under a polymorphic 'let'", at)
    }
    let joined = freshName(l, "bind")
    let valueName = freshName(l, "bv")
    let heldVar = node(held, at, CVar(valueName))
    -- The value is converted *before* the rest, because the rest is lowered
    -- under whatever this binding did to the scope -- a `var` becomes a cell,
    -- and every mention of it after this point has to be a read of that cell.
    let value = conv(l, src, scope, Some(joined))
    Map.put(scope.binders, name, Array.new(0))
    let body = if mutable {
        Set.add(scope.cells, name)
        let cell = node(refOf(held), at, CRef(heldVar))
        node(ty, at, CLet(name, cell.ty, cell, rest(), Array.new(0)))
    } else {
        node(ty, at, CLet(name, held, heldVar, rest(), Array.new(0)))
    }
    node(ty, at, CJoin(joined, [CParam { name = valueName, ty = held }],
                       body, value, False))
}

-- -- statements, which Core does not have --------------------------------------------

-- A statement sequence, as nested `let`s.
--
-- The value of a block is its last statement's, and every earlier one is a
-- binding -- of the name it introduces, or of a name nothing reads. `{ }` is
-- `()`.
fun block(l : Lowerer, stmts : Array Stmt, ty : Ty.Type, where : Option Span,
          scope : Scope) -> Core = blockFrom(l, stmts, 0, ty, where, scope)

fun blockFrom(l : Lowerer, stmts : Array Stmt, from : Int, ty : Ty.Type,
              where : Option Span, scope : Scope) -> Core {
    if from >= len(stmts) { return node(ty, where, CUnit) }
    let head = stmts[from]
    if from == len(stmts) - 1 { return tail(l, head, ty, where, scope) }
    match head.kind {
        -- A local `fun` may recurse and may call its siblings, so it binds as a
        -- group even when the group has one member.
        SFun(_) -> {
            var last = from
            while last + 1 < len(stmts) && isFun(stmts[last + 1]) {
                last = last + 1
            }
            let group = Array.slice(stmts, from, last + 1)
            -- Registered before any body is lowered, so a member calling itself
            -- or a sibling finds the group's binders.
            for stmt in group {
                match stmt.kind {
                    SFun(d) -> Map.put(scope.binders, d.name, funBinders(l, d))
                    _ -> {}
                }
            }
            let binds = Array.new(len(group))
            for stmt in group {
                match stmt.kind {
                    SFun(d) -> Array.push(binds, localFun(l, d, scope))
                    _ -> {}
                }
            }
            node(ty, where, CLetRec(binds,
                blockFrom(l, stmts, last + 1, ty, where, scope)))
        }
        -- The rest of the block is lowered *by* `bind` rather than before it,
        -- and that ordering is load-bearing: a `var` becomes a reference cell,
        -- and every mention of it after the binding has to become a read of that
        -- cell. Lowering the rest first would lower those mentions while the
        -- name was still an ordinary one.
        _ -> bind(l, head, fun() = blockFrom(l, stmts, from + 1, ty, where, scope),
                  ty, where, scope)
    }
}

fun funBinders(l : Lowerer, decl : FunDecl) -> Array VarCell =
    match localScheme(l, decl) {
        Some(s) -> s.quantified
        None -> Array.new(0)
    }

-- A `fun` bound inside a body, with whatever it generalized over.
--
-- Its scheme is not in the environment -- a local binding's is defined into a
-- scope the solver pops -- so it comes from the group's own abstraction, which
-- is where the solver recorded it, and failing that from the type the generator
-- bound it at.
fun localFun(l : Lowerer, decl : FunDecl, scope : Scope) -> CBind {
    let s = localScheme(l, decl)
    let (fnTy, binders) = match s {
        Some(sc) -> (Typed.resolve(l.types, sc.body), sc.quantified)
        None -> (declType(l, decl), Array.new(0))
    }
    let dicts = abstractionOf(l, decl.id)
    let b = newBind(decl.name, abstracted(l, fnTy, dicts), binders,
                    function(l, decl, fnTy, scope, None, None), Some(decl.span))
    b.equations = equationsOf(s)
    b
}

fun localScheme(l : Lowerer, decl : FunDecl) -> Option Scheme =
    match abstractionOf(l, decl.id) {
        None -> None
        Some(a) -> Map.get(a.schemes, decl.name)
    }

fun declType(l : Lowerer, decl : FunDecl) -> Ty.Type =
    match Typed.ofDecl(l.types, decl.id) {
        Some(t) -> t
        None -> match schemeOf(l, decl.name) {
            Some(s) -> Typed.resolve(l.types, s.body)
            None -> oops("'" + decl.name + "' has no type", Some(decl.span))
        }
    }

-- The last statement of a block, whose value is the block's.
fun tail(l : Lowerer, stmt : Stmt, ty : Ty.Type, where : Option Span,
         scope : Scope) -> Core = match stmt.kind {
    SExpr(e) -> expr(l, e, scope)
    _ -> bind(l, stmt, fun() = node(ty, where, CUnit), ty, where, scope)
}

-- One non-final statement, as a `let` around everything after it.
--
-- `rest` is a thunk, not a term: what a statement binds may change how the
-- statements after it lower, and a `var` does.
fun bind(l : Lowerer, stmt : Stmt, rest : fun() -> Core, ty : Ty.Type,
         where : Option Span, scope : Scope) -> Core {
    let at = Some(stmt.span)
    match stmt.kind {
        SExpr(e) -> {
            let value = expr(l, e, scope)
            let body = rest()
            node(body.ty, at, CLet(freshName(l, "seq"), value.ty, value, body,
                                   Array.new(0)))
        }
        SLet(pat, value) -> bindPattern(l, stmt, pat, value, False, rest, scope)
        SVar(pat, value) -> bindPattern(l, stmt, pat, value, True, rest, scope)
        SAssign(target, value) -> {
            let lowered = assign(l, stmt, target, value, scope)
            let body = rest()
            node(body.ty, at, CLet(freshName(l, "seq"), lowered.ty, lowered,
                                   body, Array.new(0)))
        }
        SFun(d) -> {
            Map.put(scope.binders, d.name, funBinders(l, d))
            node(ty, at, CLetRec([localFun(l, d, scope)], rest()))
        }
    }
}

fun bindPattern(l : Lowerer, stmt : Stmt, pat : Pat, source : Expr,
                mutable : Bool, rest : fun() -> Core, scope : Scope) -> Core {
    let at = Some(stmt.span)
    let value = abstract(l, expr(l, source, scope), abstractionOf(l, stmt.id))
    let bare = unannot(pat)
    let binders = letBinders(l, stmt, pat)
    match bare.kind {
        PVar(name) -> {
            Map.put(scope.binders, name, binders)
            if mutable {
                Set.add(scope.cells, name)
                let cell = node(refOf(value.ty), at, CRef(value))
                let body = rest()
                node(body.ty, at, CLet(name, cell.ty, cell, body, binders))
            } else {
                let body = rest()
                node(body.ty, at, CLet(name, value.ty, value, body, binders))
            }
        }
        PWild -> {
            let body = rest()
            node(body.ty, at, CLet(freshName(l, "seq"), value.ty, value, body,
                                   Array.new(0)))
        }
        -- A destructuring binding is a one-armed match, which is what it is.
        _ -> {
            let body = rest()
            node(body.ty, at, CMatch(value, [CAlt { pat = bare, body = body }]))
        }
    }
}

-- Lower a pattern's body, making a cell of any binder it assigns.
--
-- A pattern binder is reassignable like any other: reassigning a destructured
-- parameter rebinds the local name and nothing else, because patterns bind, they
-- do not alias. A cell is exactly that, and nothing more.
fun celled(l : Lowerer, pat : Pat, body : Expr, lower : fun(Scope) -> Core,
           scope : Scope, where : Option Span) -> Core {
    let types = Typed.ofPattern(l.types, pat)
    let written = assignedIn(ChExpr(body))
    let here = Array.new(2)
    for name in Map.keys(types) {
        if Set.contains(written, name) { Array.push(here, name) }
    }
    if len(here) == 0 { return lower(scope) }
    let inner = childScope(scope)
    for name in here { Set.add(inner.cells, name) }
    var out = lower(inner)
    for name in Array.reverse(Array.sort(here)) {
        let ty = rigidly(l, Map.getOr(types, name, TBottom))
        out = node(out.ty, where, CLet(name, refOf(ty),
            node(refOf(ty), where, CRef(node(ty, where, CVar(name)))),
            out, Array.new(0)))
    }
    out
}

-- What a local `let` generalized over, if it generalized.
fun letBinders(l : Lowerer, stmt : Stmt, pat : Pat) -> Array VarCell {
    let bare = unannot(pat)
    let name = match bare.kind {
        PVar(n) -> n
        _ -> return Array.new(0)
    }
    match abstractionOf(l, stmt.id) {
        None -> Array.new(0)
        Some(a) -> match Map.get(a.schemes, name) {
            Some(s) -> s.quantified
            None -> Array.new(0)
        }
    }
}

-- A statement in a position that wants an expression: a C-style `for`'s init and
-- step, which are statements in the grammar.
fun stmtValue(l : Lowerer, stmt : Stmt, scope : Scope) -> Core {
    let at = Some(stmt.span)
    match stmt.kind {
        SExpr(e) -> expr(l, e, scope)
        SAssign(target, value) -> assign(l, stmt, target, value, scope)
        -- `for var i = 0; ...`: the binding is the init, and its scope is the
        -- loop, so it is expressed as the cell it already is.
        SLet(pat, source) -> forInit(l, stmt, pat, source, False, scope)
        SVar(pat, source) -> forInit(l, stmt, pat, source, True, scope)
        _ -> oops("unhandled for-statement", at)
    }
}

fun forInit(l : Lowerer, stmt : Stmt, pat : Pat, source : Expr, mutable : Bool,
            scope : Scope) -> Core {
    let at = Some(stmt.span)
    let name = match unannot(pat).kind {
        PVar(n) -> n
        _ -> oops("a 'for' initializer must bind one name", at)
    }
    var value = expr(l, source, scope)
    if mutable {
        Set.add(scope.cells, name)
        value = node(refOf(value.ty), at, CRef(value))
    }
    node(value.ty, at, CLet(name, value.ty, value,
                            node(value.ty, at, CVar(name)), Array.new(0)))
}

fun assign(l : Lowerer, stmt : Stmt, target : Expr, source : Expr,
           scope : Scope) -> Core {
    let at = Some(stmt.span)
    let value = expr(l, source, scope)
    let unit = unitType()
    match target.kind {
        EVar(name, _) -> node(unit, at, CAssign(
            node(refOf(value.ty), Some(target.span), CVar(name)), value))
        EField(obj, name) -> {
            let o = expr(l, obj, scope)
            node(unit, at, CAssign(
                node(value.ty, Some(target.span), CField(o, name)), value))
        }
        EIndex(arr, index, _, _) -> {
            let a = expr(l, arr, scope)
            node(unit, at, CAssign(node(value.ty, Some(target.span),
                CIndex(a, expr(l, index, scope))), value))
        }
        _ -> oops("the parser should have rejected this target", at)
    }
}

-- -- looking things up --------------------------------------------------------------

-- The equalities a scheme's context states, as rewrite rules. The shape is
-- already guaranteed: a family application on the left, not mentioning itself on
-- the right, so the pair can be used as a rule exactly as reduction uses it.
fun equationsOf(s : Option Scheme) -> Array (Ty.Type, Ty.Type) = match s {
    None -> Array.new(0)
    Some(sc) -> {
        let out = Array.new(2)
        for p in sc.preds {
            if p.name == Ty.equals && len(p.args) == 2 {
                Array.push(out, (p.args[0], p.args[1]))
            }
        }
        out
    }
}

-- What a name abstracts over, local bindings before top-level ones.
fun bindersOf(l : Lowerer, name : String, scope : Scope) -> Array VarCell =
    match Map.get(scope.binders, name) {
        Some(local) -> local
        None -> match schemeOf(l, name) {
            Some(s) -> s.quantified
            None -> Array.new(0)
        }
    }

fun schemeOf(l : Lowerer, name : String) -> Option Scheme =
    match C.lookup_(l.env, name) {
        Some(b) -> Some(b.scheme)
        None -> None
    }

fun nameType(l : Lowerer, name : String) -> Ty.Type = match schemeOf(l, name) {
    Some(s) -> Typed.resolve(l.types, s.body)
    None -> oops("'" + name + "' has no type", None)
}

fun abstractionOf(l : Lowerer, id : Int) -> Option Abstraction =
    Map.get(l.types.abstractions, id)

fun classNamed(l : Lowerer, name : String) -> ClassInfo =
    match Map.get(l.classes.classes, name) {
        Some(i) -> i
        None -> oops("unknown class '" + name + "'", None)
    }

fun unannot(p : Pat) -> Pat {
    var out = p
    var going = True
    while going {
        match out.kind {
            PAnnot(inner, _) -> { out = inner }
            _ -> { going = False }
        }
    }
    out
}

-- -- the questions asked about a subtree ---------------------------------------------
--
-- A lambda and a local `fun` are their own functions: the `return` in one is
-- that one's, and it travels with it. Every question about what a body
-- *contains* stops there for that reason, so the stopping is written once.

fun stopsHere(c : Child) -> Bool = match c {
    ChExpr(e) -> match e.kind {
        ELambda(_, _, _) -> True
        _ -> False
    }
    ChStmt(s) -> match s.kind {
        SFun(_) -> True
        _ -> False
    }
    _ -> False
}

-- Whether a term holds a loop or a transfer outside any nested function. The
-- question `conv` exists to answer: a term for which this is false has nothing
-- in it that a join point is needed for.
fun special(c : Child) -> Bool {
    match c {
        ChExpr(e) -> match e.kind {
            EWhile(_, _) -> return True
            ELoop(_) -> return True
            EForC(_, _, _, _) -> return True
            EForIn(_, _, _, _, _) -> return True
            EReturn(_) -> return True
            EBreak(_) -> return True
            EContinue -> return True
            _ -> {}
        }
        _ -> {}
    }
    if stopsHere(c) { return False }
    for ch in children(c) {
        if special(ch) { return True }
    }
    False
}

-- Whether a function body needs a return join at all.
fun holdsReturn(c : Child) -> Bool {
    match c {
        ChExpr(e) -> match e.kind {
            EReturn(_) -> return True
            _ -> {}
        }
        _ -> {}
    }
    if stopsHere(c) { return False }
    for ch in children(c) {
        if holdsReturn(ch) { return True }
    }
    False
}

-- Every name assigned anywhere under a node, nested functions included.
--
-- Nested ones are the whole point: a closure that writes a captured `var` writes
-- through to it, so what decides whether a binding is a cell is whether
-- *anything* reachable from here writes it -- not whether this body does.
fun assignedIn(c : Child) -> Set.Set String {
    let out = Set.new()
    assignedInto(c, out)
    out
}

fun assignedInto(c : Child, out : Set.Set String) -> Unit {
    match c {
        ChStmt(s) -> match s.kind {
            SAssign(target, _) -> match target.kind {
                EVar(name, _) -> Set.add(out, name)
                _ -> {}
            }
            _ -> {}
        }
        _ -> {}
    }
    for ch in children(c) { assignedInto(ch, out) }
}

-- The subterms of a node that are evaluated, in the order they are evaluated.
--
-- Taken from what this pass lowers each node to, which is not the same as the
-- field order. A node absent from this table cannot be A-normalized, and a
-- transfer inside one is declined rather than guessed at -- including `&&` and
-- `||`, whose right operand is not unconditionally evaluated.
fun operands(e : Expr) -> Option (Array Expr) = match e.kind {
    ECall(fn, args) -> Some(Array.append([fn], args))
    ETuple(elems) -> Some(elems)
    EArray(elems) -> Some(elems)
    ERecord(_, fields) -> Some(Array.map(fields, fun(f) = {
        let (_, v) = f
        v
    }))
    EIndex(arr, index, _, _) -> Some([arr, index])
    EField(obj, _) -> Some([obj])
    EProject(obj, _) -> Some([obj])
    EUnary(_, operand, _) -> Some([operand])
    EBinary(op, left, right, _) ->
        if op == "&&" || op == "||" { None } else { Some([left, right]) }
    _ -> None
}

fun unsupported(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("unsupported", message), "")
    Some(s) -> Diag.fail(Diag.at("unsupported", message, s), s.file)
}
