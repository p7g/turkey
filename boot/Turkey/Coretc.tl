module Turkey.Coretc (
    CEnv(..), newCEnv, childCEnv, defineC, lookupC, globalsOf,
    Checker(..), newChecker, checkProgram, compatible, famsOf,
)

-- The Core typechecker: what makes evidence checked rather than trusted
-- (`turkey/coretc.py`).
--
-- `plan.txt` item 5 gives the reason for the whole milestone in one clause --
-- giving the elaborator a datatype "makes evidence checkable rather than
-- trusted". This is the module that does the checking. Without it the IR and
-- the lowering would be a rearrangement: the same facts in a nicer shape,
-- believed for the same reason as before, which is that nobody looked.
--
-- ## It is a checker, not an inferencer
--
-- Every binder in Core is annotated and every node carries its type, so there
-- is nothing to guess: no constraint, no solver, no ranks, no generalization.
-- Each rule computes what a node's type *must* be from its parts and compares
-- that against what the node claims.
--
-- Two deliberate slacknesses, both of them things the surface language decided:
-- **bottom absorbs**, so a branch that returns is compatible with one that does
-- not; and **a free variable matches anything once**, which keeps the checker
-- from rejecting a term for being *more* general than expected.
--
-- ## What it actually catches
--
-- The point is the dictionaries, and there the checks are exact: a dictionary
-- argument must have the class and head its parameter declares; a method
-- projection must come from a dictionary of the class that declares the method;
-- a superclass projection must name a real superclass; a type application must
-- have one argument per binder. Ordinary term structure is checked alongside,
-- because a checker that only checked dictionaries would be one a mis-lowering
-- could walk straight past.
--
-- ## What it derives rather than reads
--
-- A jump's *tail position* is derived. The checker carries a second environment
-- of join names beside the term environment, and passes it down only into tail
-- positions. Every other recursion gets an empty one. So there is no
-- tail-position predicate to write and none to get wrong: a jump in an argument,
-- in a scrutinee, in a `let`'s value or inside a lambda simply does not find its
-- name.
--
-- An alternative records no binding types, on purpose. The checker works out
-- what a pattern binds from the scrutinee's type and the constructor's
-- declaration, so a pattern that does not fit its scrutinee is a rejected term.
-- That is a check the elaborator never had, because in the surface language the
-- same fact was established by unification and then forgotten.

import Data.Set as Set
import Turkey.Ast
import Turkey.Ast as Ast
import Turkey.Classes as Classes
import Turkey.Classes (ClassTable(..), ClassInfo(..), MethodInfo(..))
import Turkey.Constraints as C
import Turkey.Constraints (Env(..))
import Turkey.Core
import Turkey.Core as Cr
import Turkey.Decls as Decls
import Turkey.Decls (DeclTable(..), ConInfo(..), TyconInfo(..))
import Turkey.Diag as Diag
import Turkey.Source (Span)
import Turkey.Typed as Typed
import Turkey.Types as Ty
import Turkey.Types (Kind(..), VarCell(..), Scheme(..), Pred(..),
                     TVar, TCon, TApp, TFam, TFun, TTuple, TBottom)

let dictPrefix = "%Dict."
let superPrefix = "%super."

-- -- the environment ---------------------------------------------------------------

-- Names in scope, each with what it abstracts over.
--
-- One shape for all three binders -- top-level, `let`, `letrec` -- because a
-- type application has to find its binders the same way whichever bound the
-- name, and because the only alternative is a second agreement to keep.
type CEnv = CEnv {
    names  : Map String (Array VarCell, Ty.Type),
    parent : Option CEnv,
}

fun newCEnv() -> CEnv = CEnv { names = Map.new(), parent = None }

fun childCEnv(e : CEnv) -> CEnv = CEnv { names = Map.new(), parent = Some(e) }

fun defineC(e : CEnv, name : String, binders : Array VarCell,
            ty : Ty.Type) -> Unit = Map.put(e.names, name, (binders, ty))

fun lookupC(e : CEnv, name : String) -> Option (Array VarCell, Ty.Type) {
    var here = e
    var going = True
    while going {
        match Map.get(here.names, name) {
            Some(found) -> return Some(found)
            None -> match here.parent {
                Some(up) -> { here = up }
                None -> { going = False }
            }
        }
    }
    None
}

-- The Core environment a solver environment implies.
--
-- Builtins reach Core the way they reach everything else -- through the type
-- environment -- so the checker is seeded from it rather than being told about
-- the primitives a second time. Outermost scope first, so an inner definition
-- shadows as it should.
fun globalsOf(env : Env) -> CEnv {
    let out = newCEnv()
    let chain = Array.new(4)
    var scope = Some(env)
    var going = True
    while going {
        match scope {
            Some(s) -> {
                Array.push(chain, s)
                scope = s.parent
            }
            None -> { going = False }
        }
    }
    for var i = len(chain) - 1; i >= 0; i = i - 1 {
        for (name, binding) in Map.entries(chain[i].names) {
            defineC(out, name, binding.scheme.quantified, binding.scheme.body)
        }
    }
    out
}

-- -- the checker --------------------------------------------------------------------

-- A join name to what a jump to it must supply and what it answers. Copied when
-- extended: a join scope is a handful of names deep at most, and a map that is
-- never written after it is built cannot leak a join out of the branch that
-- bound it.
type Checker = Checker {
    decls     : DeclTable,
    classes   : ClassTable,
    -- The join points a jump here may name. Set by `check`, which gives every
    -- rule an empty one unless the rule asked otherwise -- so tail position is
    -- what a rule opts *into*, and forgetting is safe.
    joins     : Map String (Array CParam, Ty.Type),
    -- The equalities the binding being checked states. Consulted before the
    -- instance table, exactly as the solver consults its assumptions first and
    -- for the same reason: a family over a rigid variable will never reduce
    -- through an instance, and the signature that promised it is the only thing
    -- that can say what it is.
    equations : Array (Ty.Type, Ty.Type),
}

fun newChecker(decls : DeclTable, classes : ClassTable) -> Checker = Checker {
    decls = decls, classes = classes, joins = Map.new(),
    equations = Array.new(2),
}

fun checkProgram(decls : DeclTable, classes : ClassTable, prog : CProgram,
                 globals : CEnv) -> Unit {
    let ck = newChecker(decls, classes)
    let all = Array.append(prog.dicts, prog.binds)
    -- Every top-level name is in scope for every other: the dictionaries refer
    -- to each other, and the bindings were ordered by dependency but may still
    -- be mutually recursive within a component.
    for b in all { defineC(globals, b.name, b.binders, b.ty) }
    for b in all { checkBind(ck, b, globals) }
}

fun checkBind(ck : Checker, b : CBind, env : CEnv) -> Unit {
    let want = if b.mutable && isRef(b.ty) { refElem(b.ty) } else { b.ty }
    let saved = ck.equations
    ck.equations = Array.append(saved, b.equations)
    var got = check(ck, b.value, env, None)
    if b.mutable && isRef(b.ty) {
        got = if isRef(got) { refElem(got) } else { got }
    }
    -- Compared *under* the binding's own equations, which is the only place they
    -- hold. Checking answers a reduced type, so a body checked with
    -- `Elem a ~ Int` in scope hands back `Int` -- and comparing that against an
    -- unreduced `Elem a` outside the scope asks the two sides to agree on a
    -- rewrite only one of them was allowed to make.
    expect(ck, want, got, b.span, "the binding '" + b.name + "'")
    ck.equations = saved
}

-- -- expressions --------------------------------------------------------------------

-- The type this node must have, compared against the one it claims.
--
-- `joins` is the join scope this subterm is in, and it defaults to *empty*: a
-- rule that descends without saying anything descends out of tail position,
-- which is the common case and the safe one. The rules with tail positions pass
-- their own scope down explicitly.
fun check(ck : Checker, e : Core, env : CEnv,
          joins : Option (Map String (Array CParam, Ty.Type))) -> Ty.Type {
    let outer = ck.joins
    ck.joins = match joins {
        Some(j) -> j
        None -> Map.new()
    }
    let got = rule(ck, e, env)
    ck.joins = outer
    expect(ck, e.ty, got, e.span, describe(e))
    -- The *derived* type, not the claimed one. They agree or `expect` has
    -- already objected -- but "agree" is deliberately slack about variables, so
    -- a node claiming a bare variable would otherwise hand its parent a variable
    -- to reason from, and a parent that cannot tell what it is looking at checks
    -- nothing. Deriving is the whole method.
    --
    -- Reduced, too, and for the same reason: a parameter declared
    -- `Cursor (Array a)` is an `ArrayCursor`, and a parent asking it for a field
    -- has to be looking at the type rather than at the family that names it.
    Ty.prune(reduce(ck, got))
}

-- The join scope a named subterm inherits. The IR decides, so this rule and join
-- discovery are reading one statement of the discipline rather than two.
-- Anything not named there gets nothing, which is why a rule that simply forgets
-- to ask is safe: forgetting rejects jumps, it does not admit them.
fun tail(ck : Checker, e : Core,
         field : String) -> Option (Map String (Array CParam, Ty.Type)) =
    if Array.contains(tailFieldNames(e.kind), field) { Some(ck.joins) } else { None }

fun rule(ck : Checker, e : Core, env : CEnv) -> Ty.Type = match e.kind {
    -- Trivial forms: they are their own evidence.
    CLit(_) -> e.ty
    CUnit -> e.ty
    CPrim(_) -> e.ty
    CCon(name) -> checkCon(ck, e, name)
    CVar(name) -> checkVar(ck, e, name, env)
    CTuple(elems) -> TTuple(Array.map(elems, fun(x) = check(ck, x, env, None)))
    CArray(elems) -> {
        if len(elems) == 0 { return e.ty }
        Ty.rawArrayOf(check(ck, elems[0], env, None))
    }
    CTyApp(fn, args) -> checkTyApp(ck, e, fn, args, env)
    CField(target, name) -> checkField(ck, e, target, name, env)
    CProject(target, index) -> checkProject(ck, e, target, index, env)
    CRecord(con, fields) -> checkRecord(ck, e, con, fields, env)
    CLam(params, body, _) -> {
        let inner = childCEnv(env)
        for p in params { defineC(inner, p.name, Array.new(0), p.ty) }
        TFun(Array.map(params, fun(p) = p.ty), check(ck, body, inner, None))
    }
    CApp(fn, args) -> checkApp(ck, e, fn, args, env)
    CTyLam(_, body) -> check(ck, body, env, None)
    CLet(name, bound, value, body, binders) -> {
        let v = check(ck, value, env, None)
        expect(ck, bound, v, e.span, "the binding '" + name + "'")
        let inner = childCEnv(env)
        defineC(inner, name, binders, bound)
        check(ck, body, inner, tail(ck, e, "body"))
    }
    CLetRec(binds, body) -> {
        let inner = childCEnv(env)
        for b in binds { defineC(inner, b.name, b.binders, b.ty) }
        for b in binds { checkBind(ck, b, inner) }
        check(ck, body, inner, tail(ck, e, "body"))
    }
    CRef(value) -> refOf(check(ck, value, env, None))
    CDeref(target) -> {
        let t = check(ck, target, env, None)
        if !isRef(t) {
            fail(Ty.show(t) + " is not a reference cell", e.span)
        }
        refElem(t)
    }
    CAssign(target, value) -> {
        let v = check(ck, value, env, None)
        let t = check(ck, target, env, None)
        let want = if isRef(t) { refElem(t) } else { t }
        expect(ck, want, v, e.span, "the assigned value")
        Ty.tUnit()
    }
    CIndex(target, index) -> {
        let t = Ty.prune(check(ck, target, env, None))
        expect(ck, Ty.tInt(), check(ck, index, env, None), e.span, "an index")
        let (h, _) = Ty.spine(t)
        match h {
            TVar(_) -> e.ty
            TBottom -> e.ty
            _ -> fail(Ty.show(t) + " is not indexable", e.span)
        }
    }
    CIf(cond, then, otherwise) -> {
        expect(ck, Ty.tBool(), check(ck, cond, env, None), e.span, "a condition")
        let t = check(ck, then, env, tail(ck, e, "then"))
        match otherwise {
            None -> e.ty
            Some(o) -> joinTypes(t, check(ck, o, env, tail(ck, e, "otherwise")))
        }
    }
    CMatch(scrutinee, alts) -> {
        let joins = tail(ck, e, "alts")
        let s = check(ck, scrutinee, env, None)
        var result : Option Ty.Type = None
        for alt in alts {
            let inner = childCEnv(env)
            for (name, ty) in Map.entries(pattern(ck, alt.pat, s, e.span)) {
                defineC(inner, name, Array.new(0), ty)
            }
            let got = check(ck, alt.body, inner, joins)
            result = match result {
                None -> Some(got)
                Some(had) -> Some(joinTypes(had, got))
            }
        }
        match result {
            None -> e.ty
            Some(t) -> t
        }
    }
    CJoin(name, params, body, rest, recursive) ->
        checkJoin(ck, e, name, params, body, rest, recursive, env)
    CJump(name, args) -> checkJump(ck, e, name, args, env)
}

fun checkCon(ck : Checker, e : Core, name : String) -> Ty.Type {
    let info = match Map.get(ck.decls.constructors, name) {
        Some(i) -> i
        None -> fail("unknown constructor '" + name + "'", e.span)
    }
    -- A nullary constructor stands alone rather than being applied, so what it
    -- is *worth* is the result rather than the function -- `None` is an
    -- `Option a`, not a `fun() -> Option a`.
    var declared = info.scheme.body
    match declared {
        TFun(params, ret) -> if info.arity == 0 && len(params) == 0 {
            declared = ret
        }
        _ -> {}
    }
    if !instanceOf(info.scheme, e.ty) && !compatible(declared, reduce(ck, e.ty)) {
        fail("'" + name + "' is not usable at " + Ty.show(e.ty)
             + "; it is declared " + Ty.show(declared), e.span)
    }
    e.ty
}

fun checkVar(ck : Checker, e : Core, name : String, env : CEnv) -> Ty.Type {
    -- Not every name is in the Core environment: builtins arrive through the
    -- type environment the driver seeds this with, and a dictionary parameter is
    -- bound by the lambda that takes it. A name that reaches here unknown is a
    -- real hole, so say which name.
    let (binders, ty) = match lookupC(env, name) {
        Some(found) -> found
        None -> fail("'" + name + "' is not in scope", e.span)
    }
    if len(binders) > 0 {
        -- A polymorphic name used with no type application: legitimate only
        -- under a type application, which does not check its operand this way.
        -- Reaching here means the application went missing.
        fail("'" + name + "' is polymorphic in "
             + String.join(Array.map(binders, fun(b) = Ty.show(TVar(b))), " ")
             + " and is used without a type application", e.span)
    }
    ty
}

-- A type application: one argument per binder, then substitute.
--
-- The operand is not checked the ordinary way. Nothing else in this IR is
-- polymorphic, so the operand is a name or a method projection, and its
-- polymorphic type comes from where it was bound rather than from its own type
-- -- which is why a variable refuses to be used polymorphically anywhere else.
fun checkTyApp(ck : Checker, e : Core, fn : Core, args : Array Ty.Type,
               env : CEnv) -> Ty.Type {
    let (binders, poly) = polymorphic(ck, fn, env)
    if len(binders) != len(args) {
        fail("type application has " + Int.toString(len(args))
             + " arguments for " + Int.toString(len(binders)) + " binders",
             e.span)
    }
    let mapping = Map.new()
    for var i = 0; i < len(binders); i = i + 1 {
        Map.put(mapping, binders[i].id, args[i])
    }
    Decls.substitute(poly, mapping)
}

-- The binders and body of whatever is being type-applied.
fun polymorphic(ck : Checker, fn : Core,
                env : CEnv) -> (Array VarCell, Ty.Type) = match fn.kind {
    CVar(name) -> match lookupC(env, name) {
        Some(found) -> found
        None -> fail("'" + name + "' is not in scope", fn.span)
    }
    CField(target, name) -> methodScheme(ck, fn, target, name, env)
    _ -> fail("this cannot be type-applied", fn.span)
}

-- A method projected from a dictionary, as a polymorphic thing.
--
-- This is where a wrong dictionary stops being possible: the class comes from
-- the *dictionary's own type*, so a `Monoid` dictionary cannot answer a
-- `Semigroup` projection, and the method's remaining binders are its own
-- scheme's less the class variable the dictionary just fixed.
fun methodScheme(ck : Checker, e : Core, target : Core, name : String,
                 env : CEnv) -> (Array VarCell, Ty.Type) {
    let dictTy = check(ck, target, env, None)
    let (cls, head) = dictParts(ck, dictTy, e.span)
    let info = classNamed(ck, cls, e.span)
    let m = match classMethod(info, name) {
        Some(x) -> x
        None -> fail("class '" + cls + "' has no method '" + name + "'", e.span)
    }
    let binders = Array.new(4)
    for q in m.scheme.quantified {
        if q.id != m.classVar.id { Array.push(binders, q) }
    }
    (binders, methodType(ck, m, head))
}

-- A method's type as it sits in a dictionary, at one head.
--
-- Derived here rather than read off the term, and derived the same way the
-- lowering derives it -- which is the point of a checker: two independent
-- computations of one type, compared. A method's own context is per call, so
-- those dictionaries are the field's parameters and not the dictionary's.
fun methodType(ck : Checker, m : MethodInfo, head : Ty.Type) -> Ty.Type {
    let mapping = Map.new()
    Map.put(mapping, m.classVar.id, head)
    let ty = Decls.substitute(m.scheme.body, mapping)
    let own = Array.slice(m.scheme.preds, 1, len(m.scheme.preds))
    if len(own) == 0 { return ty }
    TFun(Array.map(own, fun(p) =
        dictType(ck, p.name, Decls.substitute(p.args[0], mapping))), ty)
}

-- Read `%Dict.C t` back as the class `C` and the type `t`.
fun dictParts(ck : Checker, ty : Ty.Type, where : Option Span) -> (String, Ty.Type) {
    let (h, args) = Ty.spine(Ty.prune(ty))
    let name = match h {
        TCon(c) -> c.name
        _ -> fail(Ty.show(ty) + " is not a dictionary", where)
    }
    if !String.startsWith(name, dictPrefix) || len(args) != 1 {
        fail(Ty.show(ty) + " is not a dictionary", where)
    }
    let cls = match String.stripPrefix(name, dictPrefix) {
        Some(c) -> c
        None -> fail(Ty.show(ty) + " is not a dictionary", where)
    }
    if !Map.contains(ck.classes.classes, cls) {
        fail("no such class '" + cls + "'", where)
    }
    (cls, args[0])
}

-- Three things wear this shape: a superclass, a method, a record field.
--
-- Which one it is is decided by the *target's type*, not by the name -- that is
-- what makes the check a check. A superclass selection off a record, or a field
-- access on a dictionary, is rejected here.
fun checkField(ck : Checker, e : Core, target : Core, name : String,
               env : CEnv) -> Ty.Type {
    let t = check(ck, target, env, None)
    if !isDict(t) { return recordField(ck, t, name, e.span, Some(e.ty)) }
    let (cls, head) = dictParts(ck, t, e.span)
    if String.startsWith(name, superPrefix) {
        let want = match String.stripPrefix(name, superPrefix) {
            Some(w) -> w
            None -> name
        }
        if !hasSuper(ck, cls, want) {
            fail("'" + want + "' is not a superclass of '" + cls + "'", e.span)
        }
        return dictType(ck, want, head)
    }
    let (binders, body) = methodScheme(ck, e, target, name, env)
    if len(binders) > 0 {
        fail("method '" + name + "' is polymorphic in "
             + String.join(Array.map(binders, fun(b) = Ty.show(TVar(b))), " ")
             + " and is used without a type application", e.span)
    }
    body
}

fun checkProject(ck : Checker, e : Core, target : Core, index : Int,
                 env : CEnv) -> Ty.Type {
    let t = Ty.prune(check(ck, target, env, None))
    match t {
        TVar(_) -> return e.ty
        TBottom -> return e.ty
        _ -> {}
    }
    let choices = match t {
        TTuple(elems) -> Some(elems)
        _ -> Decls.projectionTypes(ck.decls, t)
    }
    match choices {
        Some(elems) -> if index < len(elems) { elems[index] } else {
            fail("invalid projection ." + Int.toString(index) + " from "
                 + Ty.show(t), e.span)
        }
        None -> fail("invalid projection ." + Int.toString(index) + " from "
                     + Ty.show(t), e.span)
    }
}

-- A field of a single-variant record.
fun recordField(ck : Checker, target : Ty.Type, name : String,
                where : Option Span, erased : Option Ty.Type) -> Ty.Type {
    let (h, _) = Ty.spine(Ty.prune(target))
    -- The lowering kept the type inference gave it; if that is still a variable
    -- the field was resolved by a demand the solver discharged and erased, and
    -- there is nothing left here to check. Hand back the type inference recorded
    -- for the selection rather than a fresh variable: a fresh variable is not
    -- "unknown" to the callers, it is a rigid nothing.
    match h {
        TVar(_) -> return match erased {
            Some(t) -> t
            None -> freshType()
        }
        TBottom -> return match erased {
            Some(t) -> t
            None -> freshType()
        }
        _ -> {}
    }
    let headName = match h {
        TCon(c) -> c.name
        _ -> fail(Ty.show(target) + " has no fields", where)
    }
    if !Map.contains(ck.decls.tycons, headName) {
        fail("unknown type '" + headName + "'", where)
    }
    for (_, con) in Map.entries(ck.decls.constructors) {
        if con.tycon == headName && Decls.isRecord(con) {
            match con.fields {
                Some(names) -> match Array.indexOf(names, name) {
                    Some(index) -> match con.scheme.body {
                        TFun(params, _) -> {
                            let mapping = headMapping(con.scheme, target)
                            return Decls.substitute(params[index], mapping)
                        }
                        _ -> {}
                    }
                    None -> {}
                }
                None -> {}
            }
        }
    }
    fail("'" + headName + "' has no field '" + name + "'", where)
}

fun isDict(ty : Ty.Type) -> Bool {
    let (h, args) = Ty.spine(Ty.prune(ty))
    match h {
        TCon(c) -> String.startsWith(c.name, dictPrefix) && len(args) == 1
        _ -> False
    }
}

fun dictType(ck : Checker, cls : String, arg : Ty.Type) -> Ty.Type {
    let info = classNamed(ck, cls, None)
    TApp(Ty.tcon(dictPrefix + cls, KFun(Classes.classKind(info), KStar),
                 Ty.noScope), arg, KStar)
}

fun hasSuper(ck : Checker, cls : String, want : String) -> Bool {
    for p in classNamed(ck, cls, None).supers {
        if p.name == want { return True }
    }
    False
}

-- A record, which is also how a dictionary is built.
fun checkRecord(ck : Checker, e : Core, con : String,
                fields : Array (String, Core), env : CEnv) -> Ty.Type {
    if String.startsWith(con, dictPrefix) {
        let (cls, head) = dictParts(ck, e.ty, e.span)
        dictionary(ck, e, cls, head, fields, env)
        return e.ty
    }
    let info = match Map.get(ck.decls.constructors, con) {
        Some(i) -> i
        None -> fail("unknown constructor '" + con + "'", e.span)
    }
    let mapping = headMapping(info.scheme, e.ty)
    let params = match info.scheme.body {
        TFun(ps, _) -> ps
        _ -> Array.new(0)
    }
    let names = match info.fields {
        Some(ns) -> ns
        None -> Array.new(0)
    }
    for (name, value) in fields {
        match Array.indexOf(names, name) {
            None -> fail("'" + con + "' has no field '" + name + "'", e.span)
            Some(index) -> {
                let want = Decls.substitute(params[index], mapping)
                expect(ck, want, check(ck, value, env, None), e.span,
                       "field '" + name + "' of '" + con + "'")
            }
        }
    }
    e.ty
}

-- Every field of a dictionary, against what the class declares.
--
-- A missing method is as much an error as a wrong one: a dictionary with a hole
-- in it is exactly what a crash from inside the evaluator used to mean.
fun dictionary(ck : Checker, e : Core, cls : String, head : Ty.Type,
               fields : Array (String, Core), env : CEnv) -> Unit {
    let info = classNamed(ck, cls, e.span)
    let seen = Set.new()
    for (name, value) in fields {
        Set.add(seen, name)
        if String.startsWith(name, superPrefix) {
            let sup = match String.stripPrefix(name, superPrefix) {
                Some(s) -> s
                None -> name
            }
            expect(ck, dictType(ck, sup, head), check(ck, value, env, None),
                   e.span, "the '" + name + "' of a '" + cls + "' dictionary")
        } else {
            let m = match classMethod(info, name) {
                Some(x) -> x
                None -> fail("class '" + cls + "' has no method '" + name + "'",
                             e.span)
            }
            expect(ck, methodType(ck, m, head), check(ck, value, env, None),
                   e.span, "method '" + name + "' of '" + cls + "'")
        }
    }
    for name in Map.keys(info.methods) {
        let surface = Classes.surfaceMember(name)
        if !Set.contains(seen, surface) {
            fail("the '" + cls + "' dictionary for " + Ty.show(head)
                 + " has no '" + surface + "'", e.span)
        }
    }
    for sup in info.supers {
        if !Set.contains(seen, superPrefix + sup.name) {
            fail("the '" + cls + "' dictionary for " + Ty.show(head)
                 + " has no '" + sup.name + "' superclass", e.span)
        }
    }
}

fun checkApp(ck : Checker, e : Core, fn : Core, args : Array Core,
             env : CEnv) -> Ty.Type {
    let fnTy = check(ck, fn, env, None)
    let argTys = Array.map(args, fun(a) = check(ck, a, env, None))
    match Ty.prune(fnTy) {
        TBottom -> e.ty
        TFun(params, ret) -> {
            if len(params) != len(argTys) {
                fail("a function of " + Int.toString(len(params))
                     + " parameters applied to " + Int.toString(len(argTys))
                     + " arguments", e.span)
            }
            for var i = 0; i < len(params); i = i + 1 {
                expect(ck, params[i], argTys[i], e.span,
                       "argument " + Int.toString(i + 1))
            }
            ret
        }
        _ -> fail(Ty.show(fnTy) + " is not a function", e.span)
    }
}

-- `join j(params) = body in rest`.
--
-- The join's result type is the node's own, and both halves are checked against
-- it: a jump replaces the value of this whole expression, so the body answers
-- what the rest answers or the label would not be one.
--
-- The name is in scope for the rest always, and for the body only if the join
-- says it is recursive -- which is what stops a non-recursive join being a loop
-- nobody declared.
fun checkJoin(ck : Checker, e : Core, name : String, params : Array CParam,
              body : Core, rest : Core, recursive : Bool,
              env : CEnv) -> Ty.Type {
    let joins = ck.joins
    let innerJoins = Map.new()
    for (k, v) in Map.entries(joins) { Map.put(innerJoins, k, v) }
    Map.put(innerJoins, name, (params, e.ty))
    let bodyEnv = childCEnv(env)
    for p in params { defineC(bodyEnv, p.name, Array.new(0), p.ty) }
    let bodyTy = check(ck, body, bodyEnv,
                       if recursive { Some(innerJoins) } else { Some(joins) })
    expect(ck, e.ty, bodyTy, e.span, "the join point '" + name + "'")
    joinTypes(bodyTy, check(ck, rest, env, Some(innerJoins)))
}

-- `jump j(args)`.
--
-- Reaching here at all is the tail-position check: the join scope is empty
-- everywhere but in a tail position, so a jump anywhere else fails to find its
-- name. The message says so, since the two ways to fail -- no such join, and not
-- in tail position -- are worth telling apart.
fun checkJump(ck : Checker, e : Core, name : String, args : Array Core,
              env : CEnv) -> Ty.Type {
    let found = Map.get(ck.joins, name)
    let argTys = Array.map(args, fun(a) = check(ck, a, env, None))
    let (params, _) = match found {
        Some(f) -> f
        None -> fail("'" + name + "' is not a join point in scope here; a jump "
                     + "must be in tail position of the join that binds it",
                     e.span)
    }
    if len(params) != len(argTys) {
        fail("a jump to '" + name + "' passes " + Int.toString(len(argTys))
             + " arguments for " + Int.toString(len(params)) + " parameters",
             e.span)
    }
    for var i = 0; i < len(params); i = i + 1 {
        expect(ck, params[i].ty, argTys[i], e.span,
               "argument " + Int.toString(i + 1) + " of the jump")
    }
    match Ty.prune(e.ty) {
        TBottom -> {}
        _ -> fail("a jump to '" + name + "' claims " + Ty.show(e.ty)
                  + "; it never yields to its context, so it is bottom", e.span)
    }
    e.ty
}

-- -- patterns, derived rather than read ----------------------------------------------

-- What a pattern binds, worked out from what it is matched against.
--
-- Reduced first, and here rather than at the call sites: a scrutinee may be
-- `Item a`, and whether its constructors are `Op`'s is exactly what the equality
-- in scope decides. Matching against an unreduced family would reject a program
-- the checker had been told about.
fun pattern(ck : Checker, p : Pat, scrutinee : Ty.Type,
            where : Option Span) -> Map String Ty.Type {
    let target = Ty.prune(reduce(ck, scrutinee))
    let bare = unannot(p)
    match bare.kind {
        PVar(name) -> {
            let out = Map.new()
            Map.put(out, name, target)
            out
        }
        PWild -> Map.new()
        PLit(_) -> Map.new()
        PTuple(elems) -> match target {
            TTuple(tys) -> {
                let out = Map.new()
                for var i = 0; i < len(elems) && i < len(tys); i = i + 1 {
                    for (n, t) in Map.entries(pattern(ck, elems[i], tys[i], where)) {
                        Map.put(out, n, t)
                    }
                }
                out
            }
            TVar(_) -> freshBinders(bare)
            TBottom -> freshBinders(bare)
            _ -> fail("a tuple pattern against " + Ty.show(target), where)
        }
        PCon(name, args) -> conPattern(ck, bare, name, Some(args), None, target, where)
        PRecord(name, fields) -> conPattern(ck, bare, name, None, Some(fields), target, where)
        _ -> fail("no rule for this pattern", where)
    }
}

fun conPattern(ck : Checker, p : Pat, name : String, args : Option (Array Pat),
               fields : Option (Array (String, Pat)), target : Ty.Type,
               where : Option Span) -> Map String Ty.Type {
    let info = match Map.get(ck.decls.constructors, name) {
        Some(i) -> i
        None -> fail("unknown constructor '" + name + "'", where)
    }
    match target {
        TVar(_) -> return freshBinders(p)
        TBottom -> return freshBinders(p)
        _ -> {}
    }
    let (h, _) = Ty.spine(target)
    let headName = match h {
        TCon(c) -> c.name
        _ -> fail("'" + name + "' is a constructor of '" + info.tycon
                  + "', matched against " + Ty.show(target), where)
    }
    if headName != info.tycon {
        fail("'" + name + "' is a constructor of '" + info.tycon
             + "', matched against " + Ty.show(target), where)
    }
    let mapping = headMapping(info.scheme, target)
    let params = match info.scheme.body {
        TFun(ps, _) -> ps
        _ -> Array.new(0)
    }
    let fieldTypes = Array.map(params, fun(t) = Decls.substitute(t, mapping))
    let out = Map.new()
    match args {
        Some(sub) -> {
            if len(sub) != len(fieldTypes) {
                fail("'" + name + "' takes " + Int.toString(len(fieldTypes))
                     + " arguments, matched with " + Int.toString(len(sub)),
                     where)
            }
            for var i = 0; i < len(sub); i = i + 1 {
                for (n, t) in Map.entries(pattern(ck, sub[i], fieldTypes[i], where)) {
                    Map.put(out, n, t)
                }
            }
        }
        None -> {}
    }
    match fields {
        Some(fs) -> {
            let names = match info.fields {
                Some(ns) -> ns
                None -> Array.new(0)
            }
            for (label, sub) in fs {
                match Array.indexOf(names, label) {
                    None -> fail("'" + name + "' has no field '" + label + "'",
                                 where)
                    Some(index) -> for (n, t) in Map.entries(
                            pattern(ck, sub, fieldTypes[index], where)) {
                        Map.put(out, n, t)
                    }
                }
            }
        }
        None -> {}
    }
    out
}

fun freshBinders(p : Pat) -> Map String Ty.Type {
    let out = Map.new()
    for n in patternVars(p) { Map.put(out, n, freshType()) }
    out
}

-- -- comparison ---------------------------------------------------------------------

-- Compare, after reducing both sides' families throughout.
--
-- A class declares `iter` as `fun(a) -> Cursor a`, and at `Array a` that is
-- `Cursor (Array a)` -- which is `ArrayCursor`, the type the body was inferred
-- at. The two are one type and only one of them is reduced, so reducing is part
-- of comparing rather than something the lowering was supposed to have done.
fun expect(ck : Checker, want : Ty.Type, got : Ty.Type, where : Option Span,
           what : String) -> Unit {
    let w = reduce(ck, want)
    let g = reduce(ck, got)
    if !compatible(w, g) {
        fail(what + " should be " + Ty.show(w) + " but is " + Ty.show(g), where)
    }
}

fun reduce(ck : Checker, ty : Ty.Type) -> Ty.Type =
    Typed.reduceDeep(ty, Some(famsOf(ck.classes, ck.equations)))

-- The binding's equalities first, then the instances.
--
-- The order is the solver's, and copied deliberately. A given equality is a
-- reduction rule for the family it names, and it has to be consulted first
-- because a family over a rigid variable never reduces through the instance
-- table at all.
fun famsOf(classes : ClassTable,
           equations : Array (Ty.Type, Ty.Type)) -> Ty.Families = Ty.Families {
    reduce = fun(t) = {
        let key = Ty.typeKey(t)
        for (left, right) in equations {
            if Ty.typeKey(left) == key { return Some(right) }
        }
        match t {
            TFam(name, arg, _) -> Classes.reduceFam(classes, name, arg)
            _ -> None
        }
    },
    defer = fun(_, _, _, _) = {},
}

-- Whether `ty` is an instantiation of a scheme. One-way: the scheme's variables
-- may be bound, the type's may not.
fun instanceOf(s : Scheme, ty : Ty.Type) -> Bool =
    Classes.matches(s.body, Ty.prune(ty)) || compatible(s.body, ty)

-- Whether two types may stand for each other in a Core term.
--
-- Structural, with the two slacknesses named above: bottom absorbs, and a
-- variable on either side binds rather than fails. Binding is per-comparison and
-- unrecorded, which is deliberate -- this is a check that a term is well-typed,
-- not an inference that decides what its variables are.
fun compatible(want : Ty.Type, got : Ty.Type) -> Bool {
    let w = Ty.prune(want)
    let g = Ty.prune(got)
    match w {
        TBottom -> return True
        TVar(_) -> return True
        _ -> {}
    }
    match g {
        TBottom -> return True
        TVar(_) -> return True
        _ -> {}
    }
    if Ty.typeKey(w) == Ty.typeKey(g) { return True }
    match (w, g) {
        (TFun(wp, wr), TFun(gp, gr)) -> {
            if len(wp) != len(gp) { return False }
            for var i = 0; i < len(wp); i = i + 1 {
                if !compatible(wp[i], gp[i]) { return False }
            }
            compatible(wr, gr)
        }
        (TTuple(we), TTuple(ge)) -> {
            if len(we) != len(ge) { return False }
            for var i = 0; i < len(we); i = i + 1 {
                if !compatible(we[i], ge[i]) { return False }
            }
            True
        }
        (TApp(wf, wa, _), TApp(gf, ga, _)) ->
            compatible(wf, gf) && compatible(wa, ga)
        -- Two stuck families. Neither reduced, so what is left to compare is the
        -- family and its argument -- and the arguments are variables the binder
        -- made rigid, which the rule above already lets match.
        (TFam(wn, wa, _), TFam(gn, ga, _)) -> wn == gn && compatible(wa, ga)
        _ -> False
    }
}

-- -- helpers ------------------------------------------------------------------------

-- The type of two branches. Bottom is the one that yields.
fun joinTypes(a : Ty.Type, b : Ty.Type) -> Ty.Type = match Ty.prune(a) {
    TBottom -> b
    _ -> a
}

fun freshType() -> Ty.Type = TVar(Ty.freshVar(0))

fun classNamed(ck : Checker, name : String, where : Option Span) -> ClassInfo =
    match Map.get(ck.classes.classes, name) {
        Some(i) -> i
        None -> fail("no such class '" + name + "'", where)
    }

fun classMethod(info : ClassInfo, written : String) -> Option MethodInfo =
    match Classes.memberNamed(Map.keys(info.methods), written) {
        Some(resolved) -> Map.get(info.methods, resolved)
        None -> None
    }

-- Bind a constructor's own variables to the type it was used at.
--
-- By matching its declared *result* against the target, not by zipping its
-- quantified variables against the target's arguments. The two are not the same
-- list: a scheme's variables are in the order the scheme was built in, which for
-- `Ret(r) : Flow a b r` puts `r` first. Zipping would have bound `a` where `r`
-- belongs -- and did, until the checker said so.
fun headMapping(s : Scheme, target : Ty.Type) -> Map Int Ty.Type {
    let result = match s.body {
        TFun(_, ret) -> ret
        _ -> s.body
    }
    match Classes.match_(result, Ty.prune(target)) {
        Some(mapping) -> mapping
        -- The target is not an instance of the result -- a variable somewhere,
        -- or a bottom. Nothing can be derived, and the caller's own checks
        -- report it.
        None -> Map.new()
    }
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

-- A node, said in a way a reader can find in the term. "CVar" names the rule
-- that failed; "the variable 'x'" names the thing that is wrong, which is what
-- someone chasing a mis-lowering needs.
fun describe(e : Core) -> String = match e.kind {
    CVar(name) -> "the variable '" + name + "'"
    CField(_, name) -> "the field '" + name + "'"
    CProject(_, index) -> "the projection '." + Int.toString(index) + "'"
    CCon(name) -> "the constructor '" + name + "'"
    CPrim(name) -> "the builtin '" + name + "'"
    CLet(name, _, _, _, _) -> "the body of 'let " + name + "'"
    CApp(_, _) -> "the result of an application"
    CTyApp(_, _) -> "the result of a type application"
    _ -> "this term"
}

-- A Core term that does not typecheck.
--
-- This is never the author's fault. Their program was accepted by inference; if
-- the term it lowered to does not check, the lowering or the elaboration is
-- wrong. So it says so, and says which node.
fun fail(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("internal error", message), "")
    Some(s) -> Diag.fail(Diag.at("internal error", message, s), s.file)
}
