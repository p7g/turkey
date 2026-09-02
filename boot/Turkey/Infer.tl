module Turkey.Infer (
    Generator(..), newGenerator, generate, bindMethods, checkExhaustiveness,
)

-- Constraint generation (design.md 4 and 5, `turkey/infer.py`).
--
-- This walks the tree and *builds* a constraint. It decides nothing: it never
-- unifies, never generalizes, and never looks a name up in a type environment.
-- That is what makes it order-independent -- there is no partially-solved
-- substitution for a traversal decision to depend on, because there is no
-- substitution here at all.
--
-- What it does keep is everything genuinely syntactic: scopes, but only of names
-- and their mutability, since `cannot assign to 'x'` is a property of the
-- binding form; bottom, which is produced syntactically by `return`, `break` and
-- `continue` and never discovered by solving; annotation type variables, scoped
-- to the enclosing function so the `a` in a signature and the `a` in a body
-- annotation are one variable; and field access, which emits a demand and hands
-- back its result whether or not the receiver is known.
--
-- Which instance a method call means is *not* decided here. Each occurrence is
-- marked with a `Use` for elaboration to fill in later, because the answer
-- depends on what solving decides the name's predicates are about.
--
-- Variables invented here have no rank: generation has no idea what binder depth
-- it is under, which is the point. Each is recorded in the enclosing frame and
-- becomes part of an existential, and the solver stamps the rank when it
-- arrives.

import Data.Set as Set
import Turkey.Ast
-- `Pred` below is the semantic predicate; the syntactic one a context is
-- written as is `Ast.Pred`.
import Turkey.Ast as Ast
import Turkey.Classes as Classes
import Turkey.Classes (ClassTable(..), ClassInfo(..), InstInfo(..),
                       MethodInfo(..), MethodImpl(..), InstancePlan(..),
                       Skolems(..))
import Turkey.Constraints as C
import Turkey.Constraints (Constraint(..), Binding(..), Env(..), PredCell(..),
                           LetCell(..))
import Turkey.Decls as Decls
import Turkey.Decls (DeclTable(..), ConInfo(..), TyconInfo(..))
import Turkey.Deps as Deps
import Turkey.Diag as Diag
import Turkey.Exhaustive as Exhaustive
import Turkey.Prelude as P
import Turkey.Solved (Abstraction(..), Use(..), newAbstraction, newUse, dictName)
import Turkey.Source (Span, showSpan)
import Turkey.Typed as Typed
import Turkey.Typed (TypeTable(..))
import Turkey.Types as Ty
import Turkey.Types (VarCell(..), ConCell(..), Scheme(..), Pred(..),
                     TVar, TCon, TApp, TFam, TFun, TTuple, TLabel, TIndex,
                     TSet, TBottom)

-- Which literals get a set rather than a type. A string or a char has exactly
-- one type and is emitted as one; only the numeric kinds are open.
fun isNumericKind(kind : String) -> Bool = kind == "Int" || kind == "Float"

fun literalType(kind : String) -> Ty.Type = match kind {
    "Int" -> Ty.tInt()
    "Float" -> Ty.tFloat()
    "String" -> Ty.tString()
    _ -> Ty.tChar()
}

-- What is left of the operator table after every operator that names a value
-- became a class method. These two are not, and for a reason no class can
-- express: `&&` and `||` short-circuit, which no function call does.
fun binaryOp(op : String) -> Option (Ty.Type, Ty.Type, Ty.Type) = match op {
    "&&" -> Some((Ty.tBool(), Ty.tBool(), Ty.tBool()))
    "||" -> Some((Ty.tBool(), Ty.tBool(), Ty.tBool()))
    _ -> None
}

fun unaryOp(op : String) -> Option (Ty.Type, Ty.Type) = match op {
    "!" -> Some((Ty.tBool(), Ty.tBool()))
    _ -> None
}

type LoopCtx = LoopCtx {
    -- loop | while | for
    kind     : String,
    result   : Ty.Type,
    sawBreak : Bool,
}

-- One existential under construction: the variables and constraints so far.
type Frame = Frame {
    vars  : Array VarCell,
    parts : Array Constraint,
}

type Generator = Generator {
    decls       : DeclTable,
    -- Every expression's type, kept for the lowering rather than dropped once
    -- the constraint is built. Shared across modules the way the declaration
    -- table is: one program, one table.
    types       : TypeTable,
    -- Which module is being generated, for the orphan rule.
    module_     : String,
    classes     : ClassTable,
    env         : Env,
    frames      : Array Frame,
    -- Name -> mutable. Types are the solver's business; this is only what
    -- generation needs to reject an undefined name or a write to a `let`.
    scopes      : Array (Map String Bool),
    fnStack     : Array Ty.Type,
    loopStack   : Array LoopCtx,
    tyvarScopes : Array (Map String Ty.Type),
    warnings    : Array String,
    -- Exhaustiveness runs after solving, when scrutinee types are known.
    matchSites  : Array (Expr, Ty.Type),
}

fun newGenerator(decls : DeclTable, builtins : Env, classes : ClassTable,
                 owner : String, types : TypeTable) -> Generator {
    Typed.observe(types, classes)
    Generator {
        decls = decls, types = types, module_ = owner, classes = classes,
        env = builtins, frames = [Frame { vars = Array.new(8), parts = Array.new(8) }],
        scopes = [namesOf(builtins)], fnStack = Array.new(4),
        loopStack = Array.new(4), tyvarScopes = [Map.new()],
        warnings = Array.new(4), matchSites = Array.new(8),
    }
}

-- Every name an environment holds, with its mutability.
fun namesOf(env : Env) -> Map String Bool {
    let out = Map.new()
    var here = Some(env)
    var going = True
    while going {
        match here {
            None -> { going = False }
            Some(e) -> {
                for (name, b) in Map.entries(e.names) {
                    if !Map.contains(out, name) { Map.put(out, name, b.mutable) }
                }
                here = e.parent
            }
        }
    }
    out
}

-- -- building ----------------------------------------------------------------------

-- A variable with no rank yet, recorded in the enclosing existential.
fun fresh(g : Generator) -> Ty.Type {
    let v = Ty.freshVar(0)
    Array.push(g.frames[len(g.frames) - 1].vars, v)
    TVar(v)
}

fun emit(g : Generator, c : Constraint) -> Unit =
    Array.push(g.frames[len(g.frames) - 1].parts, c)

fun eq(g : Generator, a : Ty.Type, b : Ty.Type, where : Option Span,
       context : String) -> Unit = emit(g, CEq(a, b, where, context))

fun push(g : Generator) -> Unit =
    Array.push(g.frames, Frame { vars = Array.new(8), parts = Array.new(8) })

fun pop(g : Generator) -> Constraint = match Array.pop(g.frames) {
    Some(f) -> CExists(f.vars, CAnd(f.parts))
    None -> CAnd(Array.new(0))
}

-- Equate two types and answer the one that survives.
--
-- Bottom is absorbed by whatever it meets, so a bare equation cannot tell the
-- caller which type it ends up with. Every place two branches must agree goes
-- through this instead. It can be decided during generation -- with no solving
-- at all -- because bottom is produced *syntactically*, and unification never
-- binds a variable to it. So a type is bottom here or never will be.
fun join(g : Generator, a : Ty.Type, b : Ty.Type, where : Option Span,
         context : String) -> Ty.Type {
    match a {
        TBottom -> return b
        _ -> {}
    }
    match b {
        TBottom -> return a
        _ -> {}
    }
    eq(g, a, b, where, context)
    a
}

-- -- scopes ------------------------------------------------------------------------

fun bound(g : Generator, name : String) -> Bool {
    for var i = len(g.scopes) - 1; i >= 0; i = i - 1 {
        if Map.contains(g.scopes[i], name) { return True }
    }
    False
}

fun isMutable(g : Generator, name : String) -> Bool {
    for var i = len(g.scopes) - 1; i >= 0; i = i - 1 {
        match Map.get(g.scopes[i], name) {
            Some(m) -> return m
            None -> {}
        }
    }
    False
}

-- Translate an annotation, resolving its type variables in the innermost
-- function's scope.
fun typeOf(g : Generator, te : Type) -> Ty.Type =
    Decls.star(g.decls, te, g.tyvarScopes[len(g.tyvarScopes) - 1],
               fun() = freshCell(g))

fun freshCell(g : Generator) -> VarCell {
    let v = Ty.freshVar(0)
    Array.push(g.frames[len(g.frames) - 1].vars, v)
    v
}

-- -- the program -------------------------------------------------------------------

-- Build the whole program's constraint, and its evaluation order.
--
-- The evaluator reuses that order, so a binding is always initialized before
-- anything that reads it. The constraint has the same shape: one binder per
-- binding group, nested so that later groups are solved in the scope of earlier
-- ones.
fun generate(g : Generator, program : Program) -> (Array Stmt, Constraint) {
    let typeDecls = Array.new(8)
    let classDecls = Array.new(4)
    let instDecls = Array.new(4)
    let items = Array.new(16)
    for d in program.decls {
        match d {
            DType(t) -> Array.push(typeDecls, t)
            DClass(c) -> Array.push(classDecls, c)
            DInstance(i) -> Array.push(instDecls, i)
            DStmt(s) -> Array.push(items, s)
        }
    }
    Decls.registerAll(g.decls, typeDecls)
    Classes.registerAll(g.classes, classDecls, instDecls, g.module_)
    bindMethods(g)

    -- The graph is keyed by item, not by bound name: a single binding may
    -- introduce several names, and keying by name would split one item across
    -- two components and infer -- and evaluate -- its right-hand side twice.
    let namesFor = Map.new()
    let byKey = Map.new()
    for var i = 0; i < len(items); i = i + 1 {
        let key = "item" + Int.toString(i)
        Map.put(namesFor, key, itemNames(items[i]))
        Map.put(byKey, key, items[i])
    }
    let owner = Map.new()
    for var i = 0; i < len(items); i = i + 1 {
        let key = "item" + Int.toString(i)
        for name in Map.getOr(namesFor, key, Array.new(0)) {
            if Map.contains(owner, name) {
                oops("'" + name + "' is declared more than once",
                     Some(items[i].span))
            }
            match Map.get(g.classes.owner, name) {
                Some(cls) -> oops("'" + name + "' is already defined: it is a "
                    + "method of class '" + cls + "', and methods share the "
                    + "namespace of ordinary functions", Some(items[i].span))
                None -> {}
            }
            Map.put(owner, name, key)
        }
    }

    let graph = Map.new()
    for var i = 0; i < len(items); i = i + 1 {
        let edges = Set.new()
        for n in Set.elements(itemDeps(items[i])) {
            match Map.get(owner, n) {
                Some(key) -> Set.add(edges, key)
                None -> {}
            }
        }
        Map.put(graph, "item" + Int.toString(i), edges)
    }

    let groups = Array.new(8)
    let ordered = Array.new(16)
    for component in Deps.sccs(graph) {
        let group = Array.new(len(component))
        let boundNames = Array.new(4)
        for key in component {
            match Map.get(byKey, key) {
                Some(item) -> Array.push(group, item)
                None -> {}
            }
            for n in Map.getOr(namesFor, key, Array.new(0)) {
                Array.push(boundNames, n)
            }
        }
        checkGroupRecursion(group, Array.sort(boundNames), component, graph)
        Array.push(groups, group)
        for item in group { Array.push(ordered, item) }
    }

    nest(g, groups, 0, classDecls, instDecls)
    (ordered, pop(g))
}

fun nest(g : Generator, groups : Array (Array Stmt), index : Int,
         classDecls : Array ClassDecl, instDecls : Array InstanceDecl) -> Unit {
    if index < len(groups) {
        discard(bindGroup(g, groups[index],
            fun() = {
                nest(g, groups, index + 1, classDecls, instDecls)
                Ty.tUnit()
            }, True))
    } else {
        -- Method bodies are generated innermost, so that they may call any
        -- top-level binding. Nothing calls *them* by name, so their position in
        -- the nesting is otherwise free.
        genMethodBodies(g, classDecls, instDecls)
    }
}

fun itemNames(item : Stmt) -> Array String = match item.kind {
    SFun(d) -> [d.name]
    SLet(pat, _) -> Array.sort(patternVars(pat))
    SVar(pat, _) -> Array.sort(patternVars(pat))
    _ -> Array.new(0)
}

fun itemDeps(item : Stmt) -> Set.Set String = match item.kind {
    SFun(d) -> Deps.freeNamesOfFun(d, Set.new())
    SLet(_, value) -> Deps.freeNames(value, Set.new())
    SVar(_, value) -> Deps.freeNames(value, Set.new())
    _ -> Set.new()
}

fun checkGroupRecursion(group : Array Stmt, boundNames : Array String,
                        component : Array String,
                        graph : Map String (Set.Set String)) -> Unit {
    var recursive = len(component) > 1
    for k in component {
        if Set.contains(Map.getOr(graph, k, Set.new()), k) { recursive = True }
    }
    if !recursive { return {} }
    for item in group {
        match item.kind {
            SFun(_) -> {}
            _ -> oops("cyclic definition: " + String.join(boundNames, ", ")
                      + " depend on each other, but only functions may be "
                      + "mutually recursive", Some(group[0].span))
        }
    }
}

-- Put every class method in scope, under the scheme its class gives it.
fun bindMethods(g : Generator) -> Unit {
    for (_, info) in Map.entries(g.classes.classes) {
        for (_, m) in Map.entries(info.methods) {
            let alreadyOwned = Map.contains(g.classes.owner, m.name)
            if bound(g, m.name) {
                -- Already bound by an earlier module: a class method is global
                -- and unqualified, so every module sees the same one. Binding it
                -- again is a no-op, not a collision.
                if !alreadyOwned {
                    oops("'" + m.name + "' is already defined; a class method "
                         + "shares the namespace of ordinary functions",
                         Some(m.decl.span))
                }
            } else {
                C.define(g.env, m.name, Binding { scheme = m.scheme, mutable = False })
                Map.put(g.scopes[0], m.name, False)
            }
        }
    }
}

-- Check every defaulted class method and every instance method.
--
-- Each is checked against the type its *class* states, with everything the
-- declaration does not fix made rigid. That is the whole difference between
-- checking a method and inferring a function: a method has a type to live up to,
-- and a fresh unification variable would let a body that is less general than
-- the signature pass by narrowing the signature to fit.
fun genMethodBodies(g : Generator, classDecls : Array ClassDecl,
                    instDecls : Array InstanceDecl) -> Unit {
    for decl in classDecls {
        let info = classNamed(g, decl.name)
        for (_, m) in Map.entries(info.methods) {
            match m.decl.body {
                None -> {}
                Some(_) -> {
                    let sk = Classes.newSkolems()
                    discard(Classes.skolemBind(sk, info.cvar, info.param))
                    -- One elaboration of a default serves every instance: its own
                    -- class dictionary arrives under a name each instance rebinds
                    -- as its dictionary is built.
                    Map.put(info.defaults, m.name, checkMethod(g, m, sk,
                        Array.new(0), Array.new(0),
                        "the default definition of '" + m.name + "'", None, None))
                }
            }
        }
    }
    for decl in instDecls {
        let inst = instanceFor(g, decl)
        let info = classNamed(g, decl.cls)
        let params = Array.new(len(inst.context))
        for q in inst.context { Array.push(params, dictName(q.name)) }
        let plan = Classes.newInstancePlan(params)
        inst.plan = Some(plan)
        -- The instance's own dictionary and its context are named once and
        -- shared by every method, because that is how they are bound: once, when
        -- the dictionary is built.
        let selfName = dictName(decl.cls)
        for methodDecl in decl.methods {
            let m = match Map.get(info.methods, methodDecl.name) {
                Some(x) -> x
                None -> oops("'" + methodDecl.name + "' is not a method of '"
                             + decl.cls + "'", Some(methodDecl.span))
            }
            let sk = Classes.newSkolems()
            for v in Ty.varsOf([inst.head]) {
                discard(Classes.skolemBind(sk, v,
                    Map.getOr(inst.names, v.id, "a")))
            }
            -- The class variable is not made rigid: the instance fixes it.
            Map.put(sk.mapping, m.classVar.id, Classes.skolemApply(sk, inst.head))
            let context = Array.map(inst.context, fun(q) =
                Ty.pred(q.name, [Classes.skolemApply(sk, q.args[0])]))
            Map.put(plan.methods, methodDecl.name, checkMethod(g, m, sk, context,
                plan.params, "instance '" + decl.cls + " " + Ty.show(inst.head)
                + "'", Some(methodDecl), Some(selfName)))
        }
    }
}

fun instanceFor(g : Generator, decl : InstanceDecl) -> InstInfo {
    for inst in Map.getOr(g.classes.instances, decl.cls, Array.new(0)) {
        if inst.decl.span.line == decl.span.line
           && inst.decl.span.col == decl.span.col
           && inst.decl.span.file == decl.span.file { return inst }
    }
    oops("no registered instance for this declaration", Some(decl.span))
}

fun classNamed(g : Generator, name : String) -> ClassInfo =
    match Map.get(g.classes.classes, name) {
        Some(i) -> i
        None -> oops("unknown class '" + name + "'", None)
    }

fun checkMethod(g : Generator, m : MethodInfo, sk : Skolems,
                context : Array Pred, contextNames : Array String,
                what : String, override : Option FunDecl,
                selfNameIn : Option String) -> MethodImpl {
    for v in m.scheme.quantified {
        if !Map.contains(sk.mapping, v.id) {
            discard(Classes.skolemBind(sk, v, Map.getOr(m.names, v.id, "a")))
        }
    }
    let expected = Classes.skolemApply(sk, m.scheme.body)
    -- A method's scheme states its class's predicate first, then its own
    -- context. The two are given to the body for different reasons and arrive by
    -- different routes: the first is the dictionary the body belongs to, the
    -- rest are parameters of every call.
    let own = Array.new(len(m.scheme.preds))
    for var i = 1; i < len(m.scheme.preds); i = i + 1 {
        Array.push(own, Classes.skolemApplyPred(sk, m.scheme.preds[i]))
    }
    let selfName = match selfNameIn {
        Some(n) -> n
        None -> dictName(m.cls)
    }
    let params = Array.map(own, fun(p) = dictName(p.name))
    let givens = Array.new(4)
    Array.push(givens, (selfName, Classes.skolemApplyPred(sk, m.scheme.preds[0])))
    for var i = 0; i < len(own); i = i + 1 {
        Array.push(givens, (params[i], own[i]))
    }
    for var i = 0; i < len(context) && i < len(contextNames); i = i + 1 {
        Array.push(givens, (contextNames[i], context[i]))
    }

    let decl = match override {
        Some(d) -> d
        None -> m.decl
    }
    push(g)
    let inferred = genFunction(g, decl.params, decl.ret, decl.body, decl.span,
                               decl.context, None)
    eq(g, inferred, expected, Some(decl.span),
       "the type class '" + m.cls + "' declares for '" + m.name + "'")
    let defn = pop(g)
    -- A binder for the rank, not for the polymorphism: the expected type is
    -- rigid, so nothing generalizes. What the rank buys is that a predicate the
    -- body raised is settled here, against the assumptions, instead of escaping
    -- to the end of the program.
    emit(g, CAssume(givens, CLet(LetCell {
        binds = [("%" + what + "." + m.name, expected)], defn = defn,
        body = CAnd(Array.new(0)), span = Some(decl.span), topLevel = False,
        dicts = None, skolems = sk.made,
    })))
    -- Only the constants this scope actually *made*. The mapping also holds the
    -- class variable, whose entry is the instance head rather than a skolem, and
    -- inverting that one would turn every real `Option` in the body back into a
    -- variable.
    let made = Set.new()
    for con in sk.made { Set.add(made, con.name) }
    let skolems = Map.new()
    for (k, v) in Map.entries(sk.mapping) {
        match v {
            TCon(c) -> if Set.contains(made, c.name) { Map.put(skolems, k, v) }
            _ -> {}
        }
    }
    MethodImpl { decl = decl, selfName = selfName, dictParams = params,
                 skolems = skolems }
}

-- -- signatures --------------------------------------------------------------------

-- The type a `fun` states outright, or `None` if it states none.
--
-- A signature is *complete* or it is nothing: every parameter annotated and a
-- return type written. The all-or-nothing rule is not a limitation of the
-- machinery but the point of it -- a half-stated type has no scheme for a
-- recursive call to instantiate, and a reader can tell by eye which functions
-- are checked and which are inferred.
--
-- The quantified variables are read off the annotation scope rather than found
-- by generalization, because generalizing is a *solving* step and nothing here
-- has a rank yet.
fun declaredScheme(g : Generator,
                   decl : FunDecl) -> Option (Scheme, Map String Ty.Type) {
    match decl.body {
        None -> return None
        Some(_) -> {}
    }
    let ret = match decl.ret {
        None -> return None
        Some(r) -> r
    }
    let annots = Array.new(len(decl.params))
    for p in decl.params {
        match p.kind {
            PAnnot(_, te) -> Array.push(annots, te)
            _ -> return None
        }
    }
    let tyvars = Map.new()
    let params = Array.map(annots, fun(a) =
        Decls.star(g.decls, a, tyvars, fun() = freshCell(g)))
    let retType = Decls.star(g.decls, ret, tyvars, fun() = freshCell(g))
    let preds = Classes.resolveContext(g.classes, decl.context, tyvars,
                                       Some(fun() = freshCell(g)))
    let quantified = Array.new(4)
    for (_, v) in Map.entries(tyvars) {
        match v {
            TVar(cell) -> Array.push(quantified, cell)
            _ -> {}
        }
    }
    let body = TFun(params, retType)
    -- The ambiguity test the inference path applies at generalization, run here
    -- instead, where it is purely syntactic: a written context that constrains a
    -- variable the type does not reach can never be decided by a use site,
    -- because there is nothing at a use site to decide it with.
    let cells = Array.map(preds, fun(p) =
        PredCell { pred = p, span = Some(decl.span), context = "read" })
    let ids = C.reach(cells, [body])
    -- Blamed in the names the signature wrote, not in the letters printing would
    -- invent: the whole point of a declared type is that it is the programmer's
    -- text, so a complaint about it should quote that text.
    let written = Ty.namer("")
    for (name, v) in Map.entries(tyvars) {
        match v {
            TVar(cell) -> Map.put(written.names, cell.id, name)
            _ -> {}
        }
    }
    for var i = 0; i < len(preds) && i < len(decl.context); i = i + 1 {
        var touches = False
        for v in Ty.varsOf(preds[i].args) {
            if Set.contains(ids, v.id) { touches = True }
        }
        if !touches {
            oops("'" + Ty.showPredWith(preds[i], written) + "' constrains a type "
                 + "that the declared type of '" + decl.name + "' does not "
                 + "mention, so no use of '" + decl.name + "' could decide it",
                 Some(decl.context[i].span))
        }
    }
    Some((Ty.scheme(quantified, body, preds), tyvars))
}

-- Check a body against the type its `fun` declared.
--
-- The same procedure as a method's, for the same reason: a stated type has to be
-- lived up to, and a fresh unification variable would let a body that is *less*
-- general than the signature pass by narrowing the signature to fit.
fun checkSignature(g : Generator, decl : FunDecl, s : Scheme,
                   tyvars : Map String Ty.Type) -> Constraint {
    let sk = Classes.newSkolems()
    let rigid = Map.new()
    for (name, v) in Map.entries(tyvars) {
        match v {
            TVar(cell) -> Map.put(rigid, name, Classes.skolemBind(sk, cell, name))
            _ -> {}
        }
    }
    let expected = Classes.skolemApply(sk, s.body)
    -- *Every* declared predicate is a fact the body may use, so every one becomes
    -- a given -- an equality included, since reduction reads the assumptions for
    -- the rewrite that makes `Item s` become `Op`. Only a class predicate arrives
    -- as a *dictionary*, though; the erased kinds are facts the body never asks
    -- for evidence of.
    let givens = Array.new(len(s.preds))
    for p in s.preds {
        Array.push(givens, (dictName(p.name), Classes.skolemApplyPred(sk, p)))
    }
    let passedNames = Array.new(4)
    let passedPreds = Array.new(4)
    for var i = 0; i < len(s.preds); i = i + 1 {
        if Classes.isClass(g.classes, s.preds[i].name) {
            let (n, _) = givens[i]
            Array.push(passedNames, n)
            Array.push(passedPreds, s.preds[i])
        }
    }
    let dicts = newAbstraction()
    dicts.params = passedNames
    dicts.preds = passedPreds
    -- Which constant stands for which of the signature's variables: this body's
    -- recorded types name the constants, and the scheme names the variables.
    for (_, v) in Map.entries(tyvars) {
        match v {
            TVar(cell) -> match Map.get(sk.mapping, cell.id) {
                Some(con) -> Map.put(dicts.skolems, cell.id, con)
                None -> {}
            }
            _ -> {}
        }
    }
    Map.put(g.types.abstractions, decl.id, dicts)

    push(g)
    let inferred = genFunction(g, decl.params, decl.ret, decl.body, decl.span,
                               decl.context, Some(rigid))
    eq(g, inferred, expected, Some(decl.span),
       "the declared type of '" + decl.name + "'")
    let defn = pop(g)
    CAssume(givens, CLet(LetCell {
        binds = [("%sig." + decl.name, expected)], defn = defn,
        body = CAnd(Array.new(0)), span = Some(decl.span), topLevel = False,
        dicts = None, skolems = sk.made,
    }))
}

-- design.md 5.1: a non-exhaustive match is a warning, not an error -- reaching
-- an unhandled case is a runtime panic. Runs after solving, since it needs the
-- scrutinee types resolved.
fun checkExhaustiveness(g : Generator) -> Unit {
    for (site, scrutinee) in g.matchSites {
        let arms = match site.kind {
            EMatch(_, a) -> a
            _ -> Array.new(0)
        }
        -- Normalized, not merely pruned: the element of a `for ... in` loop is
        -- `Item xs`, and a family names a type only once it has reduced.
        match Exhaustive.check(g.decls, arms, Typed.resolve(g.types, scrutinee)) {
            None -> {}
            Some(missing) -> {
                -- A bare `_` witness means the scrutinee's type has too many
                -- values to name one -- an `Int`, say -- so there is nothing
                -- useful to quote.
                let detail = if missing == "_" { "it needs a catch-all arm" }
                             else { "'" + missing + "' is not handled" }
                Array.push(g.warnings, showSpan(site.span)
                    + ": warning: this match is not exhaustive; " + detail)
            }
        }
    }
}

-- -- binders -----------------------------------------------------------------------

-- Generate a binding group, scoping it over whatever `rest` generates.
--
-- The only place a binder is built, so the only place that has to get the
-- nesting right. `rest` is a callback rather than a list of statements because
-- the scope of a `let` is not always "the remaining statements" -- a C-style
-- `for`'s initializer scopes over the condition and step too.
fun bindGroup(g : Generator, group : Array Stmt, rest : fun() -> a,
              top : Bool) -> a {
    let (binds, defn, generalizes, mutable, sigs) = buildDefinition(g, group)
    -- What each `fun` in the group was bound at. A generalizing group's schemes
    -- are recorded by the solver, which is the only place they exist; this covers
    -- the group that does not generalize, whose names are monomorphic.
    let boundTypes = Map.new()
    for (n, t) in binds { Map.put(boundTypes, n, t) }
    for (n, s) in sigs { Map.put(boundTypes, n, s.body) }
    for item in group {
        match item.kind {
            SFun(d) -> match Map.get(boundTypes, d.name) {
                Some(t) -> Typed.recordDecl(g.types, d.id, t)
                None -> {}
            }
            _ -> {}
        }
    }

    let scope = Map.new()
    for (n, _) in binds { Map.put(scope, n, mutable) }
    for (n, _) in sigs { Map.put(scope, n, False) }
    Array.push(g.scopes, scope)
    push(g)
    let out = rest()
    let body = pop(g)
    discard(Array.pop(g.scopes))

    var node = CAnd(Array.new(0))
    if len(binds) == 0 {
        -- Every member states its own type, so there is nothing left to
        -- generalize and no rank to push for.
        node = CAnd([defn, body])
    } else if generalizes {
        -- One abstraction for the group, hung on each declaration in it so the
        -- evaluator can find it, and handed to the binder so the solver can fill
        -- it in when it decides what the schemes retain. A member with a
        -- signature is not in the group's binds and got its own abstraction.
        let dicts = newAbstraction()
        let names = Set.new()
        for (n, _) in binds { Set.add(names, n) }
        for item in group {
            match item.kind {
                SFun(d) -> if Set.contains(names, d.name) {
                    Map.put(g.types.abstractions, d.id, dicts)
                }
                _ -> Map.put(g.types.abstractions, item.id, dicts)
            }
        }
        node = CLet(LetCell {
            binds = binds, defn = defn, body = body, span = Some(group[0].span),
            topLevel = top, dicts = Some(dicts), skolems = Array.new(0),
        })
    } else {
        -- No generalization means no new rank, so the definition is solved right
        -- here and only the names are scoped. Nothing needs its rank lowered
        -- afterwards, because nothing ever raised it.
        node = CAnd([defn, CDef(binds, body, top)])
    }
    -- The declared schemes scope over the whole group *and* over `rest`, which is
    -- what makes a recursive occurrence an instantiation.
    if len(sigs) > 0 { node = CBind(sigs, node, top) }
    emit(g, node)
    out
}

-- The bound names, the definition's constraint, and how it binds.
fun buildDefinition(g : Generator, group : Array Stmt)
        -> (Array (String, Ty.Type), Constraint, Bool, Bool,
            Array (String, Scheme)) {
    var allFuns = True
    for item in group {
        match item.kind {
            SFun(_) -> {}
            _ -> { allFuns = False }
        }
    }
    if allFuns {
        let decls = Array.new(len(group))
        for item in group {
            match item.kind {
                SFun(d) -> Array.push(decls, d)
                _ -> {}
            }
        }
        let (binds, defn, sigs) = buildFunGroup(g, decls)
        -- A `fun` is syntactically a value, so the value restriction never blocks
        -- generalization here. What can is a declaration saying it does not want
        -- generalizing -- which no program can say, and only a lifted loop's
        -- helper does.
        var mono = False
        for d in decls {
            if d.monomorphic { mono = True }
        }
        return (binds, defn, !mono, False, sigs)
    }
    let stmt = group[0]
    match stmt.kind {
        SLet(pat, value) -> letDefinition(g, stmt, pat, value, False)
        SVar(pat, value) -> letDefinition(g, stmt, pat, value, True)
        _ -> oops("only functions may share a binding group", Some(stmt.span))
    }
}

fun letDefinition(g : Generator, stmt : Stmt, pat : Pat, value : Expr,
                  isVar : Bool) -> (Array (String, Ty.Type), Constraint, Bool,
                                    Bool, Array (String, Scheme)) {
    push(g)
    let t = genExpr(g, value)
    let bound = matchPattern(g, pat, t)
    let binds = Array.new(4)
    for (n, ty) in Map.entries(bound) { Array.push(binds, (n, ty)) }
    let defn = pop(g)
    -- design.md 4.4: only a `let` bound to a syntactic value generalizes.
    let generalizes = !isVar && isNonexpansive(g, value)
    (binds, defn, generalizes, isVar, Array.new(0))
}

-- design.md 5.2 step 3: one strongly-connected group at a time.
--
-- A group splits by whether a member states its own type. An inferred member's
-- placeholder is bound *monomorphically* inside the definition, which is what
-- keeps polymorphic recursion out of inference, where it is undecidable; the
-- enclosing binder generalizes it only once every body has been solved. A member
-- with a signature is bound to that signature by the `CBind` wrapped around the
-- whole group, so a recursive use of it instantiates instead.
fun buildFunGroup(g : Generator, decls : Array FunDecl)
        -> (Array (String, Ty.Type), Constraint, Array (String, Scheme)) {
    let sigs = Array.new(4)
    let checks = Array.new(4)
    let inferred = Array.new(4)
    for decl in decls {
        match declaredScheme(g, decl) {
            None -> Array.push(inferred, decl)
            Some((s, tyvars)) -> {
                Array.push(sigs, (decl.name, s))
                Array.push(checks, (decl, s, tyvars))
            }
        }
    }

    push(g)
    let binds = Array.new(len(inferred))
    for decl in inferred { Array.push(binds, (decl.name, fresh(g))) }
    let scope = Map.new()
    for (n, _) in binds { Map.put(scope, n, False) }
    for (n, _) in sigs { Map.put(scope, n, False) }
    Array.push(g.scopes, scope)
    push(g)
    for (decl, s, tyvars) in checks {
        emit(g, checkSignature(g, decl, s, tyvars))
    }
    for var i = 0; i < len(inferred); i = i + 1 {
        let decl = inferred[i]
        let (_, placeholder) = binds[i]
        eq(g, placeholder,
           genFunction(g, decl.params, decl.ret, decl.body, decl.span,
                       decl.context, None),
           Some(decl.span), "")
    }
    let inner = pop(g)
    discard(Array.pop(g.scopes))
    emit(g, CDef(binds, inner, False))
    (binds, pop(g), sigs)
}

-- A function's own constraint. Parameters bind monomorphically.
--
-- No rank is pushed: a lambda is not a generalization point, so its parameters
-- live at whatever rank the enclosing binder established.
--
-- `rigid` is the annotation scope to work in, and passing one is what
-- distinguishes checking from inferring. It maps each name the signature
-- quantifies to a skolem constant, so every annotation in the body reads the
-- *same* rigid type the signature promised -- and, since the context is then a
-- fact rather than an obligation, it is not emitted here.
fun genFunction(g : Generator, params : Array Pat, retAnnot : Option Type,
                bodyExpr : Option Expr, where : Span, context : Array Ast.Pred,
                rigid : Option (Map String Ty.Type)) -> Ty.Type {
    let scope = Map.new()
    match rigid {
        Some(r) -> for (n, t) in Map.entries(r) { Map.put(scope, n, t) }
        None -> {}
    }
    Array.push(g.tyvarScopes, scope)
    push(g)

    let paramTypes = Array.new(len(params))
    let binds = Map.new()
    for param in params {
        let tv = fresh(g)
        mergeInto(binds, matchPattern(g, param, tv), param.span)
        Array.push(paramTypes, tv)
    }

    let ret = fresh(g)
    match retAnnot {
        Some(r) -> eq(g, ret, typeOf(g, r), Some(where), "the return type annotation")
        None -> {}
    }

    -- On the inference path a declared context is emitted as an ordinary demand
    -- rather than attached to the scheme by hand. It then travels the same road
    -- as one the body raised. Asking for a context is not the same as being
    -- granted one, and a `fun` with an incomplete annotation has no signature to
    -- be granted anything by. A complete one does, and there the context has
    -- already become a given, so re-emitting it would demand of the body exactly
    -- what the caller has been made to promise.
    match rigid {
        None -> for p in Classes.resolveContext(g.classes, context,
                    g.tyvarScopes[len(g.tyvarScopes) - 1],
                    Some(fun() = freshCell(g))) {
            emit(g, C.cpredOf(p, Some(where), "read"))
        }
        Some(_) -> {}
    }

    -- Parameters are reassignable. They bind monomorphically, so the value
    -- restriction that makes a `var` binding special has nothing to say about
    -- them; reassigning one rebinds this local slot and does not write through
    -- to the caller.
    let paramScope = Map.new()
    for (n, _) in Map.entries(binds) { Map.put(paramScope, n, True) }
    Array.push(g.scopes, paramScope)
    push(g)
    Array.push(g.fnStack, ret)
    -- `break` cannot cross a function.
    let loops = g.loopStack
    g.loopStack = Array.new(4)
    let bodyType = match bodyExpr {
        Some(b) -> genExpr(g, b)
        None -> Ty.tUnit()
    }
    g.loopStack = loops
    discard(Array.pop(g.fnStack))
    -- A body of type bottom leaves `ret` to be fixed by the `return`s.
    eq(g, ret, bodyType, Some(where), "the function body")
    let inner = pop(g)
    discard(Array.pop(g.scopes))

    let bindList = Array.new(4)
    for (n, t) in Map.entries(binds) { Array.push(bindList, (n, t)) }
    emit(g, CDef(bindList, inner, False))
    emit(g, pop(g))
    discard(Array.pop(g.tyvarScopes))
    TFun(paramTypes, ret)
}

-- -- patterns ----------------------------------------------------------------------

-- Constrain a pattern against the type it scrutinizes, and name its binders.
-- Binding them is the caller's job, since only the caller knows whether they are
-- monomorphic.
fun matchPattern(g : Generator, pat : Pat, t : Ty.Type) -> Map String Ty.Type {
    let out = matchPatternInner(g, pat, t)
    -- Kept for the lowering, which needs a pattern binder's type wherever a
    -- binder becomes a reference cell. The Core checker derives these
    -- independently from the scrutinee, so recording them here is a second
    -- opinion rather than the only one.
    Typed.recordPattern(g.types, pat, out)
    out
}

fun matchPatternInner(g : Generator, pat : Pat, t : Ty.Type) -> Map String Ty.Type =
    match pat.kind {
        PWild -> Map.new()
        PVar(name) -> {
            let out = Map.new()
            Map.put(out, name, t)
            out
        }
        PLit(l) -> {
            let kind = litKind(l)
            if isNumericKind(kind) {
                eq(g, t, numeric(g, kind, l, Some(pat.span), "a literal pattern"),
                   Some(pat.span), "a literal pattern")
            } else {
                eq(g, t, literalType(kind), Some(pat.span), "a literal pattern")
            }
            Map.new()
        }
        PAnnot(inner, te) -> {
            eq(g, t, typeOf(g, te), Some(pat.span), "a pattern annotation")
            matchPattern(g, inner, t)
        }
        PTuple(elems) -> {
            let types = Array.map(elems, fun(_) = fresh(g))
            eq(g, t, TTuple(types), Some(pat.span), "a tuple pattern")
            let out = Map.new()
            for var i = 0; i < len(elems); i = i + 1 {
                mergeInto(out, matchPattern(g, elems[i], types[i]), pat.span)
            }
            out
        }
        -- A record variant matches positionally too: the constructor's parameters
        -- are in declaration order for both forms, so nothing here cares which
        -- form declared it. Only the record form may omit fields, which is why
        -- the arity check stays unconditional.
        PCon(name, args) -> match Decls.instantiateCon(g.decls, name,
                fun() = freshCell(g), Some(pat.span)) {
            TFun(conParams, conRet) -> {
                if len(args) != len(conParams) {
                    oops("constructor '" + name + "' takes "
                         + Int.toString(len(conParams)) + " argument(s), but the "
                         + "pattern supplies " + Int.toString(len(args)),
                         Some(pat.span))
                }
                eq(g, t, conRet, Some(pat.span), "the pattern '" + name + "'")
                let out = Map.new()
                for var i = 0; i < len(args); i = i + 1 {
                    mergeInto(out, matchPattern(g, args[i], conParams[i]), pat.span)
                }
                out
            }
            _ -> Map.new()
        }
        PRecord(name, fields) -> match Decls.instantiateCon(g.decls, name,
                fun() = freshCell(g), Some(pat.span)) {
            TFun(conParams, conRet) -> {
                let info = match Decls.con(g.decls, name) {
                    Some(i) -> i
                    None -> oops("unknown constructor '" + name + "'", Some(pat.span))
                }
                let names = match info.fields {
                    Some(ns) -> ns
                    None -> oops("constructor '" + name + "' has positional "
                                 + "arguments, not fields", Some(pat.span))
                }
                eq(g, t, conRet, Some(pat.span), "the pattern '" + name + "'")
                let out = Map.new()
                for (label, sub) in fields {
                    match Array.indexOf(names, label) {
                        None -> oops("'" + name + "' has no field '" + label + "'",
                                     Some(pat.span))
                        Some(index) -> mergeInto(out,
                            matchPattern(g, sub, conParams[index]), pat.span)
                    }
                }
                out
            }
            _ -> Map.new()
        }
    }

fun mergeInto(into : Map String Ty.Type, new : Map String Ty.Type,
              where : Span) -> Unit {
    for (name, ty) in Map.entries(new) {
        if Map.contains(into, name) {
            oops("'" + name + "' is bound twice in the same pattern", Some(where))
        }
        Map.put(into, name, ty)
    }
}

-- -- statements --------------------------------------------------------------------

-- design.md 6.8: a block's value is its last statement's, and a declaration
-- contributes Unit. A binder scopes over the statements that follow it, so it is
-- generated as a binder *around* them rather than as one more statement.
fun genSequence(g : Generator, stmts : Array Stmt, from : Int) -> Ty.Type {
    if from >= len(stmts) { return Ty.tUnit() }
    let stmt = stmts[from]
    match stmt.kind {
        SLet(_, _) -> return bindGroup(g, [stmt],
            fun() = genSequence(g, stmts, from + 1), False)
        SVar(_, _) -> return bindGroup(g, [stmt],
            fun() = genSequence(g, stmts, from + 1), False)
        SFun(_) -> return bindGroup(g, [stmt],
            fun() = genSequence(g, stmts, from + 1), False)
        _ -> {}
    }
    let value = genStmt(g, stmt)
    if from + 1 < len(stmts) { genSequence(g, stmts, from + 1) } else { value }
}

-- A statement that binds nothing. Binders go through `bindGroup`.
fun genStmt(g : Generator, stmt : Stmt) -> Ty.Type = match stmt.kind {
    SExpr(e) -> genExpr(g, e)
    SAssign(target, value) -> {
        genAssign(g, stmt.span, target, value)
        Ty.tUnit()
    }
    _ -> oops("a binder reached the statement generator", Some(stmt.span))
}

fun genAssign(g : Generator, where : Span, target : Expr, value : Expr) -> Unit =
    match target.kind {
        EVar(name, _) -> {
            if !bound(g, name) {
                oops("'" + name + "' is not defined", Some(where))
            }
            if !isMutable(g, name) {
                oops("cannot assign to '" + name + "': it was bound with 'let'. "
                     + "Use 'var' to make it reassignable.", Some(where))
            }
            eq(g, use(g, name, Some(where), None), genExpr(g, value), Some(where),
               "the assignment to '" + name + "'")
        }
        EField(_, _) -> {
            let fieldType = genField(g, target, True)
            eq(g, fieldType, genExpr(g, value), Some(where), "the assigned value")
        }
        EIndex(arr, index, _, _) -> {
            let element = fresh(g)
            eq(g, genExpr(g, arr), Ty.arrayOf(element), Some(where),
               "an indexed assignment")
            eq(g, genExpr(g, index), Ty.tInt(), Some(where), "an array index")
            eq(g, element, genExpr(g, value), Some(where), "the assigned value")
        }
        _ -> oops("the parser should have rejected this assignment target",
                  Some(where))
    }

-- -- expressions -------------------------------------------------------------------

fun genExpr(g : Generator, e : Expr) -> Ty.Type {
    let ty = genExprKind(g, e)
    -- One place, so no case can forget. What is recorded is the variable itself,
    -- not a snapshot of it: solving fills it in later.
    Typed.record(g.types, e, ty)
    ty
}

-- `name <= t` for a fresh `t`. No lookup: that is the solver's job.
--
-- A `Use` rides along when there is an occurrence to attach it to, so that
-- elaboration has somewhere to put the evidence this site turns out to need.
-- What that is cannot be known here -- it depends on what the solver decides the
-- name's predicates are about -- which is why the site is marked rather than
-- resolved.
fun use(g : Generator, name : String, where : Option Span,
        node : Option Expr) -> Ty.Type {
    let t = fresh(g)
    let marker = match node {
        Some(n) -> {
            let u = newUse(name, where, None)
            Map.put(g.types.uses, n.id, u)
            Some(u)
        }
        None -> None
    }
    emit(g, CInstance(name, t, where, marker))
    t
}

-- A fresh variable, plus the set of types a numeric literal could have.
--
-- A numeric literal is not given a type here. Which type it has is a decision,
-- and decisions belong to the solver -- the set is all the syntax actually
-- determines. `1` is not an `Int` that a later rule might widen; it is a numeral
-- whose set happens to contain `Int`.
fun numeric(g : Generator, kind : String, l : Lit, where : Option Span,
            context : String) -> Ty.Type {
    let t = fresh(g)
    let names = if kind == "Int" {
        match l {
            LInt(v) -> Ty.intLiteralSet(v)
            _ -> Ty.floatLiteralSet()
        }
    } else { Ty.floatLiteralSet() }
    emit(g, C.cpredOf(Ty.pred(C.oneOf, [t, TSet(names)]), where, context))
    t
}

fun genExprKind(g : Generator, e : Expr) -> Ty.Type = match e.kind {
    ELit(l) -> {
        let kind = litKind(l)
        if isNumericKind(kind) { numeric(g, kind, l, Some(e.span), "") }
        else { literalType(kind) }
    }
    EUnit -> Ty.tUnit()
    EVar(name, _) -> {
        if !bound(g, name) {
            oops("'" + name + "' is not defined", Some(e.span))
        }
        use(g, name, Some(e.span), Some(e))
    }
    ECon(name) -> match Decls.instantiateCon(g.decls, name, fun() = freshCell(g),
                                             Some(e.span)) {
        -- A nullary constructor is a value; anything else is a function.
        TFun(params, ret) -> if len(params) == 0 { ret } else { TFun(params, ret) }
        other -> other
    }
    ETuple(elems) -> TTuple(Array.map(elems, fun(x) = genExpr(g, x)))
    EArray(elems) -> {
        var element = fresh(g)
        for item in elems {
            element = join(g, element, genExpr(g, item), Some(item.span),
                           "an array literal")
        }
        Ty.arrayOf(element)
    }
    ERecord(conName, fields) -> genRecord(g, e, conName, fields)
    ELambda(params, ret, body) ->
        genFunction(g, params, ret, Some(body), e.span, Array.new(0), None)
    ECall(fn, args) -> {
        let fnType = genExpr(g, fn)
        let argTypes = Array.map(args, fun(a) = genExpr(g, a))
        let result = fresh(g)
        eq(g, fnType, TFun(argTypes, result), Some(e.span), "a function call")
        result
    }
    EIndex(arr, index, _, _) -> {
        let element = fresh(g)
        eq(g, genExpr(g, arr), Ty.arrayOf(element), Some(e.span), "an index")
        eq(g, genExpr(g, index), Ty.tInt(), Some(index.span), "an array index")
        element
    }
    EField(_, _) -> genField(g, e, False)
    EProject(obj, index) -> {
        let receiver = genExpr(g, obj)
        let result = fresh(g)
        emit(g, C.cpredOf(Ty.pred(C.hasProjection,
            [TIndex(index), receiver, result]), Some(e.span), "read"))
        result
    }
    EUnary(op, operand, fn) -> match fn {
        Some(f) -> genOperator(g, f, [operand], op, e.span)
        None -> match unaryOp(op) {
            Some((want, result)) -> {
                eq(g, genExpr(g, operand), want, Some(e.span),
                   "the operand of '" + op + "'")
                result
            }
            None -> oops("no rule for unary '" + op + "'", Some(e.span))
        }
    }
    EBinary(op, left, right, fn) -> match fn {
        Some(f) -> genOperator(g, f, [left, right], op, e.span)
        None -> match binaryOp(op) {
            Some((wantLeft, wantRight, result)) -> {
                eq(g, genExpr(g, left), wantLeft, Some(left.span),
                   "the left operand of '" + op + "'")
                eq(g, genExpr(g, right), wantRight, Some(right.span),
                   "the right operand of '" + op + "'")
                result
            }
            None -> oops("no rule for binary '" + op + "'", Some(e.span))
        }
    }
    EAnnot(inner, te) -> {
        let annotated = typeOf(g, te)
        join(g, genExpr(g, inner), annotated, Some(e.span), "a type annotation")
    }
    EBlock(stmts) -> genSequence(g, stmts, 0)
    EIf(cond, then, otherwise) -> {
        eq(g, genExpr(g, cond), Ty.tBool(), Some(cond.span), "an 'if' condition")
        let thenType = genExpr(g, then)
        match otherwise {
            -- design.md 6.7: statement-style `if` has no value.
            None -> Ty.tUnit()
            Some(o) -> join(g, thenType, genExpr(g, o), Some(e.span),
                            "the branches of an 'if'")
        }
    }
    EWhile(cond, body) -> {
        eq(g, genExpr(g, cond), Ty.tBool(), Some(cond.span), "a 'while' condition")
        Array.push(g.loopStack, LoopCtx {
            kind = "while", result = Ty.tUnit(), sawBreak = False,
        })
        discard(genExpr(g, body))
        discard(Array.pop(g.loopStack))
        Ty.tUnit()
    }
    EForIn(pat, iterable, body, iterFn, nextFn) ->
        genForIn(g, e, pat, iterable, body, iterFn, nextFn)
    EForC(init, cond, step, body) -> {
        let rest = fun() = {
            eq(g, genExpr(g, cond), Ty.tBool(), Some(cond.span), "a 'for' condition")
            Array.push(g.loopStack, LoopCtx {
                kind = "for", result = Ty.tUnit(), sawBreak = False,
            })
            discard(genExpr(g, body))
            match step {
                Some(s) -> discard(genStmt(g, s))
                None -> {}
            }
            discard(Array.pop(g.loopStack))
            Ty.tUnit()
        }
        match init {
            -- The initializer's scope is the condition, body and step -- not a
            -- list of following statements, which is why `bindGroup` takes a
            -- callback.
            Some(s) -> match s.kind {
                SLet(_, _) -> bindGroup(g, [s], rest, False)
                SVar(_, _) -> bindGroup(g, [s], rest, False)
                _ -> {
                    discard(genStmt(g, s))
                    rest()
                }
            }
            None -> rest()
        }
    }
    ELoop(body) -> {
        let ctx = LoopCtx { kind = "loop", result = fresh(g), sawBreak = False }
        Array.push(g.loopStack, ctx)
        discard(genExpr(g, body))
        discard(Array.pop(g.loopStack))
        -- design.md 6.7: a `loop` never falls through, so its value comes only
        -- from its breaks. With no break at all it never produces one.
        if ctx.sawBreak { ctx.result } else { TBottom }
    }
    EMatch(scrutinee, arms) -> genMatch(g, e, scrutinee, arms)
    EReturn(value) -> {
        if len(g.fnStack) == 0 {
            oops("'return' is only valid inside a function", Some(e.span))
        }
        let t = match value {
            Some(v) -> genExpr(g, v)
            None -> Ty.tUnit()
        }
        eq(g, g.fnStack[len(g.fnStack) - 1], t, Some(e.span), "a 'return'")
        TBottom
    }
    EBreak(value) -> {
        if len(g.loopStack) == 0 {
            oops("'break' is only valid inside a loop", Some(e.span))
        }
        let ctx = g.loopStack[len(g.loopStack) - 1]
        ctx.sawBreak = True
        match value {
            None -> eq(g, ctx.result, Ty.tUnit(), Some(e.span),
                       "a valueless 'break'")
            Some(v) -> if ctx.kind != "loop" {
                oops("'break' cannot carry a value out of a '" + ctx.kind
                     + "' loop; only 'loop' produces one", Some(e.span))
            } else {
                ctx.result = join(g, ctx.result, genExpr(g, v), Some(e.span),
                                  "a 'break'")
            }
        }
        TBottom
    }
    EContinue -> {
        if len(g.loopStack) == 0 {
            oops("'continue' is only valid inside a loop", Some(e.span))
        }
        TBottom
    }
    EQuestion(_, _) -> oops("'?' survived desugaring", Some(e.span))
    EDo(_) -> oops("'do' survived desugaring", Some(e.span))
}

fun genRecord(g : Generator, e : Expr, conName : String,
              fields : Array (String, Expr)) -> Ty.Type =
    match Decls.instantiateCon(g.decls, conName, fun() = freshCell(g),
                               Some(e.span)) {
        TFun(conParams, conRet) -> {
            let info = match Decls.con(g.decls, conName) {
                Some(i) -> i
                None -> oops("unknown constructor '" + conName + "'", Some(e.span))
            }
            let names = match info.fields {
                Some(ns) -> ns
                None -> oops("constructor '" + conName + "' takes positional "
                             + "arguments; write '" + conName + "(...)'",
                             Some(e.span))
            }
            let given = Array.map(fields, fun(f) = {
                let (label, _) = f
                label
            })
            for f in given {
                if !Array.contains(names, f) {
                    oops("'" + conName + "' has no field '" + f + "'", Some(e.span))
                }
            }
            let missing = Array.new(2)
            for n in names {
                if !Array.contains(given, n) { Array.push(missing, n) }
            }
            if len(missing) > 0 {
                oops("'" + conName + "' is missing field(s): "
                     + String.join(missing, ", "), Some(e.span))
            }
            let seen = Set.new()
            for f in given {
                if Set.contains(seen, f) {
                    oops("a field is initialized twice in '" + conName + "'",
                         Some(e.span))
                }
                Set.add(seen, f)
            }
            -- design.md 6.1: fields evaluate left to right, so check them so.
            for (label, value) in fields {
                match Array.indexOf(names, label) {
                    Some(index) -> eq(g, conParams[index], genExpr(g, value),
                        Some(value.span),
                        "field '" + label + "' of '" + conName + "'")
                    None -> {}
                }
            }
            conRet
        }
        other -> other
    }

-- Emit `HasField "f" r a` for `r.f` and hand back the `a`.
--
-- Nothing is decided here. If the receiver turns out to be known the solver
-- discharges the predicate and unifies the result with the declared field type;
-- if not, the predicate waits, and may end up travelling in a scheme. That is
-- the whole of SPEC-DELTAS.md 7: the old code pruned the receiver and demanded a
-- record on the spot, which made `a.length` mean different things depending on
-- whether the walk had reached `a[0]` yet.
fun genField(g : Generator, e : Expr, assigning : Bool) -> Ty.Type =
    match e.kind {
        EField(obj, name) -> {
            let receiver = genExpr(g, obj)
            let result = fresh(g)
            emit(g, C.cpredOf(Ty.pred(C.hasField,
                [TLabel(name), receiver, result]), Some(e.span),
                if assigning { "mutate" } else { "read" }))
            result
        }
        _ -> oops("not a field access", Some(e.span))
    }

-- An operator is a call to the method it desugars to.
--
-- Nothing here knows that `add` is special. The method's own scheme --
-- `[Add a] fun(a, a) -> a` -- is what makes both operands agree and what raises
-- the class predicate, so an operator on a user's own type needs no case
-- anywhere: it needs an instance.
fun genOperator(g : Generator, fn : Expr, operands : Array Expr, op : String,
                where : Span) -> Ty.Type {
    let m = genExpr(g, fn)
    let args = Array.map(operands, fun(x) = genExpr(g, x))
    let result = fresh(g)
    let what = if len(args) > 1 { "the operands of '" + op + "'" }
               else { "the operand of '" + op + "'" }
    eq(g, m, TFun(args, result), Some(where), what)
    result
}

-- `for x in xs` walks a cursor, and `x` is `Item xs`.
--
-- `iter` makes the cursor and `next` advances it; both are generated as ordinary
-- uses, so the loop demands `Iterator` the same way any call does and
-- elaboration hands it a dictionary with no case of its own. Both the cursor and
-- the element are family applications, and are left to reduce like every other
-- one.
fun genForIn(g : Generator, e : Expr, pat : Pat, iterable : Expr, body : Expr,
             iterFn : Option Expr, nextFn : Option Expr) -> Ty.Type {
    let sequence = genExpr(g, iterable)
    let cursor = fresh(g)
    let element = fresh(g)
    let where = "the sequence of a 'for ... in' loop"
    let iter = match iterFn {
        Some(f) -> f
        None -> oops("this 'for' loop has no 'iter' in scope", Some(e.span))
    }
    let next = match nextFn {
        Some(f) -> f
        None -> oops("this 'for' loop has no 'next' in scope", Some(e.span))
    }
    eq(g, genExpr(g, iter), TFun([sequence], cursor), Some(iterable.span), where)
    eq(g, genExpr(g, next),
       TFun([sequence, cursor],
            Ty.applyTo(Decls.head(g.decls, P.optionType), [element], None)),
       Some(iterable.span), where)
    -- design.md 6.5: `x` is a fresh immutable binding each iteration.
    let binds = matchPattern(g, pat, element)
    let scope = Map.new()
    for (n, _) in Map.entries(binds) { Map.put(scope, n, False) }
    Array.push(g.scopes, scope)
    push(g)
    Array.push(g.loopStack, LoopCtx {
        kind = "for", result = Ty.tUnit(), sawBreak = False,
    })
    discard(genExpr(g, body))
    discard(Array.pop(g.loopStack))
    let inner = pop(g)
    discard(Array.pop(g.scopes))
    let bindList = Array.new(4)
    for (n, t) in Map.entries(binds) { Array.push(bindList, (n, t)) }
    emit(g, CDef(bindList, inner, False))
    Ty.tUnit()
}

fun genMatch(g : Generator, e : Expr, scrutinee : Expr,
             arms : Array Arm) -> Ty.Type {
    let scrutineeType = genExpr(g, scrutinee)
    Array.push(g.matchSites, (e, scrutineeType))
    var result = TBottom
    for arm in arms {
        let bindings = matchPattern(g, arm.patterns[0], scrutineeType)
        for var i = 1; i < len(arm.patterns); i = i + 1 {
            let alt = arm.patterns[i]
            let other = matchPattern(g, alt, scrutineeType)
            if !sameKeys(other, bindings) {
                oops("every alternative in a match arm must bind the same "
                     + "variables", Some(alt.span))
            }
            for (name, ty) in Map.entries(other) {
                eq(g, Map.getOr(bindings, name, ty), ty, Some(alt.span),
                   "the binding '" + name + "'")
            }
        }
        let scope = Map.new()
        for (n, _) in Map.entries(bindings) { Map.put(scope, n, False) }
        Array.push(g.scopes, scope)
        push(g)
        let body = genExpr(g, arm.body)
        let armC = pop(g)
        discard(Array.pop(g.scopes))
        let bindList = Array.new(4)
        for (n, t) in Map.entries(bindings) { Array.push(bindList, (n, t)) }
        emit(g, CDef(bindList, armC, False))
        result = join(g, result, body, Some(arm.span), "the arms of a 'match'")
    }
    result
}

fun sameKeys(a : Map String Ty.Type, b : Map String Ty.Type) -> Bool {
    if len(Map.keys(a)) != len(Map.keys(b)) { return False }
    for k in Map.keys(a) {
        if !Map.contains(b, k) { return False }
    }
    True
}

-- -- the value restriction ---------------------------------------------------------

-- design.md 4.4. Only a syntactic value may be generalized.
fun isNonexpansive(g : Generator, e : Expr) -> Bool = match e.kind {
    ELit(_) -> True
    EUnit -> True
    EVar(_, _) -> True
    ELambda(_, _, _) -> True
    ECon(_) -> True
    EAnnot(inner, _) -> isNonexpansive(g, inner)
    ETuple(elems) -> {
        for x in elems {
            if !isNonexpansive(g, x) { return False }
        }
        True
    }
    -- `C(v1, ..., vn)` for an immutable constructor is still a value.
    ECall(fn, args) -> match fn.kind {
        ECon(name) -> {
            if !immutableCon(g, name) { return False }
            for a in args {
                if !isNonexpansive(g, a) { return False }
            }
            True
        }
        _ -> False
    }
    ERecord(conName, fields) -> {
        if !immutableCon(g, conName) { return False }
        for (_, v) in fields {
            if !isNonexpansive(g, v) { return False }
        }
        True
    }
    _ -> False
}

fun immutableCon(g : Generator, name : String) -> Bool = match Decls.con(g.decls, name) {
    None -> False
    Some(info) -> match Map.get(g.decls.tycons, info.tycon) {
        Some(t) -> !Decls.isMutableRecord(t)
        None -> False
    }
}

fun oops(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("type error", message), "")
    Some(s) -> Diag.fail(Diag.at("type error", message, s), s.file)
}

fun discard(x : a) -> Unit = {}
