module Turkey.Classes (
    Evidence(..), MethodImpl(..), InstancePlan(..), newInstancePlan,
    MethodInfo(..), hasDefault,
    ClassInfo(..), classKind,
    InstInfo(..), instCon, instHeadKey,
    ClassTable(..), newClassTable,
    registerAll, resolveContext,
    reduceFam, normalize, uncovered,
    bySuper, byInst, entail, isClass, method, simplify,
    match_, matches,
    Skolems(..), newSkolems, skolemBind, skolemApply, skolemApplyPred,
    surfaceMember, memberNamed,
    showClasses,
)

-- Single-parameter type classes: declaration, the instance table, entailment
-- (`turkey/classes.py`).
--
-- The shape is Jones's, from "Typing Haskell in Haskell": `bySuper` walks a
-- predicate's superclass closure, `byInst` matches it against the instance
-- table and answers that instance's own obligations, and `entail` puts the two
-- together.
--
-- Three restrictions hold it up, and each buys something:
--
-- * **One parameter.** With a single class variable, ambiguity is the plain
--   free-variable test -- a quantified variable a predicate mentions and the
--   type does not is unresolvable, full stop. Multi-parameter classes need
--   functional dependencies before that test says anything useful, and an
--   associated family makes the second parameter unnecessary: `Elem c` is the
--   element type of a container, computed rather than quantified.
-- * **An instance head is a constructor applied to distinct type variables.**
--   Matching is then a one-way structural walk that cannot fail to terminate,
--   and two instances of a class overlap exactly when they name the same
--   constructor -- so the check is a lookup rather than a unification test.
-- * **A superclass constrains the class variable itself.** `class Monoid a :
--   Semigroup a` may not say `Semigroup (Array a)`, so `bySuper` substitutes by
--   simply carrying the predicate's argument across.
--
-- **Skolems are nullary constructors.** Checking an instance method against the
-- class's signature requires the method to be polymorphic in everything the
-- instance does not fix, and a fresh unification variable would happily be
-- narrowed by a body that is *less* general than it claims. So the quantified
-- variables are replaced by rigid nullary constructors named after them --
-- `unify` already treats one as rigid and compares by name, so this needs no
-- new machinery, and the name means an error about one reads as though it were
-- still the type variable the author wrote.
--
-- **A family is `byInst` for types.** `reduceFam` matches a family application
-- against the same instance table, by the same one-way match, and answers what
-- that instance said the family is. Failing to match is *stuck*, never false:
-- the class predicate that travels beside every family application is what
-- reports a missing instance, with the better message.
--
-- ## Why the evidence types are here
--
-- `FromInstance` holds the instance whose dictionary it is, and an instance
-- holds the plan for building that dictionary, which is made of evidence. In
-- Python those are two files and the cycle is broken by typing one field
-- `object`. Turkey has no mutually recursive modules, so they are one module --
-- which design.md 9 says is the answer, and this is the first place the
-- compiler has had to take it. The *elaborator* is still separate: what is
-- merged here is the data, not the algorithm.

import Data.Set as Set
import Turkey.Ast
-- `Pred` below is the semantic predicate; the syntactic one a context is
-- written as is `Ast.Pred`.
import Turkey.Ast as Ast
import Turkey.Decls as Decls
import Turkey.Decls (DeclTable(..), FamilyInfo(..))
import Turkey.Diag as Diag
import Turkey.Source (Span)
import Turkey.Types as Ty
import Turkey.Types (Kind(..), VarCell(..), ConCell(..), Scheme(..), Pred(..),
                     TVar, TCon, TApp, TFam, TFun, TTuple, TLabel, TIndex,
                     TSet, TBottom)

-- -- evidence ----------------------------------------------------------------------

type Evidence
    -- A dictionary already in scope, reached through zero or more superclasses.
    -- The `Array String` is the chain of superclass names to walk: `Semigroup a`
    -- from a `Monoid a` parameter is `["Semigroup"]`. A superclass edge is a
    -- *selection*, not a lookup, which is what makes `class Monoid a : Semigroup
    -- a` mean the dictionary carries its superclass rather than the caller
    -- passing both. The predicate of the parameter itself is kept because
    -- looking its type up later by generated name is fragile when evidence is
    -- completed away from a use site.
    = FromDict(String, Array String, Option Pred)
    -- The dictionary of an instance, given evidence for that instance's
    -- context. `instance Semigroup (Array a)` is a *function* from a
    -- `Semigroup a` dictionary to a `Semigroup (Array a)` one.
    | FromInstance(InstInfo, Array Evidence)
    -- A predicate whose argument is bottom, and so has no instance to find. The
    -- mirror of the same rule in the solver: bottom is absorbed by whatever it
    -- meets, so there is no type to look up and no value that would ever ask
    -- for a method.
    | Absent(String)

-- One method body, and the names its dictionaries arrive under.
--
-- `selfName` is the dictionary the body belongs to -- the instance's own, or for
-- a default the one the class is being defaulted into. It is bound when the
-- dictionary is built, which is why a default method may call another method of
-- its own class without anything being passed.
--
-- `dictParams` are the method's *own* context, which is per call rather than per
-- instance and so arrives as arguments.
--
-- `skolems` records which rigid constant stands for which quantified variable
-- while this body is checked. A method body is checked against skolems -- the
-- instance's variables *and* the method's own -- so every type recorded inside
-- it names constants, while the type the class declares names variables. They
-- are the same types, and this is the only record of which is which.
type MethodImpl = MethodImpl {
    decl       : FunDecl,
    selfName   : String,
    dictParams : Array String,
    skolems    : Map Int Ty.Type,
}

-- Everything the evaluator needs to build one instance's dictionary.
type InstancePlan = InstancePlan {
    params  : Array String,
    methods : Map String MethodImpl,
    supers  : Map String Evidence,
}

fun newInstancePlan(params : Array String) -> InstancePlan =
    InstancePlan { params = params, methods = Map.new(), supers = Map.new() }

-- -- the tables --------------------------------------------------------------------

-- One method of one class, as a scheme any use site can instantiate.
--
-- The scheme's first predicate is the class's own -- `combine` is
-- `[Semigroup a] fun(a, a) -> a` -- which is what makes a method call demand an
-- instance without the caller doing anything special.
type MethodInfo = MethodInfo {
    name     : String,
    cls      : String,
    scheme   : Scheme,
    decl     : FunDecl,
    classVar : VarCell,
    -- Quantified variable id -> the name it was written as.
    names    : Map Int String,
}

fun hasDefault(m : MethodInfo) -> Bool = Option.isSome(m.decl.body)

type ClassInfo = ClassInfo {
    name     : String,
    param    : String,
    -- The class variable, shared by every method's scheme. Not `var`, which
    -- is a reserved word and so cannot name a field.
    cvar     : VarCell,
    supers   : Array Pred,
    methods  : Map String MethodInfo,
    span     : Option Span,
    -- Which module declared it, for the orphan rule.
    module_  : String,
    -- Family name -> the parameter it was written over. The types themselves
    -- live in the declaration table, because a family is a name in type
    -- position and one table decides what those mean.
    families : Map String String,
    -- Method name -> the elaborated default body, shared by every instance that
    -- does not override it. Filled in by the generator.
    defaults : Map String MethodImpl,
}

fun classKind(info : ClassInfo) -> Kind = info.cvar.kind

type InstInfo = InstInfo {
    cls      : String,
    head     : Ty.Type,
    context  : Array Pred,
    decl     : InstanceDecl,
    names    : Map Int String,
    -- Family name -> what it is at this head, over the head's own variables.
    families : Map String Ty.Type,
    -- What the evaluator needs to build this dictionary. Set by the generator
    -- once the method bodies are checked.
    plan     : Option InstancePlan,
    -- The module that declared it, for the orphan rule (SPEC-DELTAS.md 43).
    module_  : String,
}

fun instCon(inst : InstInfo) -> String {
    let (h, _) = Ty.spine(inst.head)
    match h {
        TCon(c) -> c.name
        TTuple(elems) -> "Tuple" + Int.toString(len(elems))
        _ -> oops("validated instance head has no stable key", None)
    }
}

fun instHeadKey(t : Ty.Type) -> String {
    let (h, _) = Ty.spine(t)
    match h {
        TCon(c) -> "con " + c.name
        TTuple(elems) -> "tuple " + Int.toString(len(elems))
        _ -> oops("instance head was not shape-checked", None)
    }
}

type ClassTable = ClassTable {
    decls     : DeclTable,
    classes   : Map String ClassInfo,
    instances : Map String (Array InstInfo),
    -- Method name -> owning class. Methods share the value namespace with
    -- ordinary functions, so this is also what detects a collision.
    owner     : Map String String,
}

fun newClassTable(decls : DeclTable) -> ClassTable = ClassTable {
    decls = decls, classes = Map.new(), instances = Map.new(),
    owner = Map.new(),
}

-- -- registration ------------------------------------------------------------------

-- Declare every class before resolving any signature.
--
-- A method's context may name a class declared further down the file --
-- `foldMap[Monoid m]` above `class Monoid` -- and a superclass may too, so names
-- come first and bodies second. Kinds ride along: the class variable starts as
-- an undecided kind and every signature that applies it narrows it, which is how
-- `Foldable t` discovers `t :: * -> *`.
fun registerAll(t : ClassTable, classes : Array ClassDecl,
                instances : Array InstanceDecl, owner : String) -> Unit {
    for d in classes {
        if Map.contains(t.classes, d.name) {
            oops("class '" + d.name + "' is declared more than once", Some(d.span))
        }
        Map.put(t.classes, d.name, ClassInfo {
            name = d.name, param = d.param, cvar = Ty.freshVar(1),
            supers = Array.new(2), methods = Map.new(), span = Some(d.span),
            module_ = owner, families = Map.new(), defaults = Map.new(),
        })
        declareFamilies(t, d)
    }

    for d in classes { resolveClass(t, d) }
    for d in classes { checkSuperAcyclic(t, d.name, Array.new(4)) }
    for (_, fam) in Map.entries(t.decls.families) {
        discard(Ty.defaultKind(fam.resKind))
    }
    for (_, info) in Map.entries(t.classes) {
        discard(Ty.defaultKind(info.cvar.kind))
        for (_, m) in Map.entries(info.methods) {
            for v in m.scheme.quantified { discard(Ty.defaultKind(v.kind)) }
        }
    }

    let registered = Array.map(instances, fun(d) = resolveInstance(t, d, owner))
    -- Coverage is checked only once every instance is in the table, since
    -- `instance Monoid (Array a)` is entitled to be satisfied by a
    -- `Semigroup (Array a)` written further down the file.
    for inst in registered { checkInstance(t, inst) }
}

-- Register a class's families before any signature is read.
--
-- They go in the declaration table, because that is what `toType` consults, and
-- a family shares the type namespace with constructors and aliases -- `Elem`
-- cannot also be a data type. The argument kind is the class variable's own kind
-- cell, so a family applied in a signature constrains the class exactly as a
-- method's use of the parameter does; the result kind starts undecided and is
-- settled by use.
fun declareFamilies(t : ClassTable, d : ClassDecl) -> Unit {
    let info = classNamed(t, d.name)
    for fam in d.families {
        if fam.param != d.param {
            oops("'" + fam.name + "' must be a family over '" + d.param
                 + "', the parameter of class '" + d.name + "': a family is "
                 + "determined by the class variable and there is nothing else "
                 + "in scope", Some(fam.span))
        }
        match Map.get(t.decls.families, fam.name) {
            Some(other) -> oops("'" + fam.name + "' is already a type family of "
                + "class '" + other.cls + "'", Some(fam.span))
            None -> {}
        }
        if Map.contains(t.decls.tycons, fam.name) {
            oops("'" + fam.name + "' is already a type; a type family cannot "
                 + "share its name", Some(fam.span))
        }
        Map.put(t.decls.families, fam.name, FamilyInfo {
            name = fam.name, cls = d.name, argKind = info.cvar.kind,
            resKind = Ty.kvar(), span = Some(fam.span),
        })
        Map.put(info.families, fam.name, fam.param)
    }
}

fun checkSuperAcyclic(t : ClassTable, name : String, seen : Array String) -> Unit {
    if Array.contains(seen, name) {
        oops("class '" + name + "' is its own superclass", classNamed(t, name).span)
    }
    let inner = Array.append(seen, [name])
    for sup in classNamed(t, name).supers { checkSuperAcyclic(t, sup.name, inner) }
}

fun resolveClass(t : ClassTable, d : ClassDecl) -> Unit {
    let info = classNamed(t, d.name)
    for sup in d.supers {
        match sup.kind {
            PEq(_, _) -> oops("a superclass must be a class constraint", Some(sup.span))
            PClass(name, arg) -> {
                let supInfo = match Map.get(t.classes, name) {
                    Some(i) -> i
                    None -> oops("unknown class '" + name + "'", Some(sup.span))
                }
                let ok = match arg.kind {
                    TEVar(v) -> v == d.param
                    _ -> False
                }
                if !ok {
                    oops("the superclasses of '" + d.name + "' must constrain "
                         + "its own parameter '" + d.param + "'", Some(sup.span))
                }
                if !Ty.unifyKinds(supInfo.cvar.kind, info.cvar.kind) {
                    oops("'" + d.param + "' cannot be both a '" + name + "' and "
                         + "a '" + d.name + "': the two disagree about its kind",
                         Some(sup.span))
                }
                Array.push(info.supers, Ty.pred(name, [TVar(info.cvar)]))
            }
        }
    }

    for decl in d.methods {
        match Map.get(t.owner, decl.name) {
            Some(other) -> oops("'" + decl.name + "' is already a method of "
                + "class '" + other + "'", Some(decl.span))
            None -> {}
        }
        let tyvars = Map.new()
        Map.put(tyvars, d.param, TVar(info.cvar))
        let body = methodType(t, decl, tyvars)
        let preds = Array.append([Ty.pred(d.name, [TVar(info.cvar)])],
                                 resolveContext(t, decl.context, tyvars, None))
        let s = Ty.generalize(body, 0, preds)
        let names = Map.new()
        for (name, stood) in Map.entries(tyvars) {
            match stood {
                TVar(cell) -> Map.put(names, cell.id, name)
                _ -> {}
            }
        }
        Map.put(info.methods, decl.name, MethodInfo {
            name = decl.name, cls = d.name, scheme = s, decl = decl,
            classVar = info.cvar, names = names,
        })
        Map.put(t.owner, decl.name, d.name)
    }
}

-- A method's declared type, read off its annotations.
--
-- A signature and a defaulted method are read the same way, because a
-- signature's parameters are stored as anonymous annotated binders. Both must be
-- fully annotated: a class fixes its methods' types, so there is nothing here
-- for inference to discover.
fun methodType(t : ClassTable, decl : FunDecl,
                tyvars : Map String Ty.Type) -> Ty.Type {
    let fresh = fun() = Ty.freshVar(1)
    let params = Array.new(len(decl.params))
    for p in decl.params {
        match p.kind {
            PAnnot(_, te) -> Array.push(params, Decls.star(t.decls, te, tyvars, fresh))
            _ -> oops("parameter of method '" + decl.name + "' needs a type: a "
                      + "class fixes its methods' types, so none is inferred",
                      Some(p.span))
        }
    }
    match decl.ret {
        None -> oops("method '" + decl.name + "' must state a return type",
                     Some(decl.span))
        Some(ret) -> TFun(params, Decls.star(t.decls, ret, tyvars, fresh))
    }
}

-- Translate a written `[C a, D b]` into predicates over `tyvars`.
--
-- `tyvars` is the enclosing declaration's annotation scope, so the `a` in
-- `[Ord a]` and the `a` in `xs : Array a` are the same variable
-- (SPEC-DELTAS.md 13) -- a context constrains, it does not bind.
fun resolveContext(t : ClassTable, context : Array Ast.Pred, tyvars : Map String Ty.Type,
                   supplied : Option Ty.Fresh) -> Array Pred {
    let fresh = match supplied {
        Some(f) -> f
        None -> fun() = Ty.freshVar(1)
    }
    let out = Array.new(len(context))
    let answers = Map.new()
    for p in context {
        match p.kind {
            PEq(left, right) -> {
                let equality = resolveEquality(t, p, left, right, tyvars, fresh)
                -- A family is a function of its argument, so a context may give
                -- it one answer. Two are caught here rather than by improvement,
                -- which only sees the *deferred* queue: a written equality
                -- becomes an assumption and never joins it.
                let key = Ty.typeKey(equality.args[0])
                match Map.get(answers, key) {
                    Some(had) -> oops("'" + Ty.show(equality.args[0])
                        + "' is already required to be '" + Ty.show(had)
                        + "' here, and a family has one answer for one argument",
                        Some(p.span))
                    None -> {}
                }
                Map.put(answers, key, equality.args[1])
                Array.push(out, equality)
            }
            PClass(name, argTe) -> {
                let info = match Map.get(t.classes, name) {
                    Some(i) -> i
                    None -> oops("unknown class '" + name + "'", Some(p.span))
                }
                let arg = Decls.toType(t.decls, argTe, tyvars, fresh)
                if !Ty.unifyKinds(Ty.kindOf(arg), info.cvar.kind) {
                    oops("'" + Ty.show(arg) + "' has kind "
                         + Ty.showKind(Ty.kindOf(arg)) + ", but '" + name
                         + "' constrains a type of kind "
                         + Ty.showKind(info.cvar.kind), Some(p.span))
                }
                Array.push(out, Ty.pred(name, [arg]))
            }
        }
    }
    out
}

-- Translate a written `Item c ~ Op` (SPEC-DELTAS.md 39).
--
-- The left side must be a family application, and that restriction is what keeps
-- the rule sound as a *given*. A given equality is used by rewriting -- `Item c`
-- has to actually become `Op` for a `match` on the element to find its
-- constructors -- so each one is read as a reduction rule for the family it
-- names. Requiring the family on the left gives every rule a single,
-- syntactically evident left-hand side; requiring the right side not to mention
-- it keeps rewriting terminating.
--
-- A general equality between two arbitrary types would be neither. It is also
-- not needed: `Int ~ a` says nothing a plain annotation cannot.
fun resolveEquality(t : ClassTable, p : Ast.Pred, leftTe : Type, rightTe : Type,
                    tyvars : Map String Ty.Type, fresh : Ty.Fresh) -> Pred {
    let left = Decls.toType(t.decls, leftTe, tyvars, fresh)
    let right = Decls.toType(t.decls, rightTe, tyvars, fresh)
    match left {
        TFam(_, _, _) -> {}
        _ -> oops("'" + Ty.show(left) + "' is not a type family, and the left "
                  + "side of a '~' must be one -- an equality says what a family "
                  + "answers, not that two types happen to agree", Some(p.span))
    }
    for sub in Ty.subterms(right) {
        if Ty.sameType(sub, left) {
            oops("'" + Ty.show(right) + "' mentions '" + Ty.show(left)
                 + "', so the equality defines it in terms of itself", Some(p.span))
        }
    }
    if !Ty.unifyKinds(Ty.kindOf(left), Ty.kindOf(right)) {
        oops("'" + Ty.show(left) + "' has kind " + Ty.showKind(Ty.kindOf(left))
             + ", but '" + Ty.show(right) + "' has kind "
             + Ty.showKind(Ty.kindOf(right)), Some(p.span))
    }
    Ty.pred(Ty.equals, [left, right])
}

fun resolveInstance(t : ClassTable, d : InstanceDecl, owner : String) -> InstInfo {
    let info = match Map.get(t.classes, d.cls) {
        Some(i) -> i
        None -> oops("unknown class '" + d.cls + "'", Some(d.span))
    }
    let tyvars = Map.new()
    let fresh = fun() = Ty.freshVar(1)
    let h = Decls.toType(t.decls, d.head, tyvars, fresh)
    if !Ty.unifyKinds(Ty.kindOf(h), info.cvar.kind) {
        oops("'" + Ty.show(h) + "' has kind " + Ty.showKind(Ty.kindOf(h))
             + ", but '" + d.cls + "' constrains a type of kind "
             + Ty.showKind(info.cvar.kind), Some(d.span))
    }
    checkHeadShape(d, h)

    let key = instHeadKey(h)
    for other in instancesOf(t, d.cls) {
        if instHeadKey(other.head) == key {
            oops("overlapping instances: '" + d.cls + " " + Ty.show(h)
                 + "' and '" + other.cls + " " + Ty.show(other.head)
                 + "' both apply", Some(d.span))
        }
    }
    let context = resolveContext(t, d.context, tyvars, None)
    let families = resolveFamilies(t, d, info, h, tyvars, fresh)
    let names = Map.new()
    for (name, stood) in Map.entries(tyvars) {
        match stood {
            TVar(cell) -> {
                discard(Ty.defaultKind(cell.kind))
                Map.put(names, cell.id, name)
            }
            _ -> {}
        }
    }
    let inst = InstInfo {
        cls = d.cls, head = h, context = context, decl = d, names = names,
        families = families, plan = None, module_ = owner,
    }
    checkOrphan(t, inst, d)
    match Map.get(t.instances, d.cls) {
        Some(existing) -> Array.push(existing, inst)
        None -> Map.put(t.instances, d.cls, [inst])
    }
    inst
}

-- An instance belongs to its class's module or its head's (SPEC-DELTAS.md 43).
--
-- Instances are global -- every module sees every one, because a predicate has
-- to mean the same thing wherever it is solved. Global and *unrestricted* is
-- what would make coherence a matter of luck: two libraries could each write
-- `instance Show Point` over someone else's `Show` and someone else's `Point`,
-- and whichever loaded second would be the error, in a file neither author
-- wrote. The orphan rule makes the overlap check a local obligation instead: an
-- instance can only clash with one its author was in a position to see.
fun checkOrphan(t : ClassTable, inst : InstInfo, d : InstanceDecl) -> Unit {
    let home = classNamed(t, inst.cls).module_
    let name = instCon(inst)
    let headModule = moduleOf(name)
    -- A public facade owns types declared in its private child module
    -- (`Data.Bool.Type` -> `Data.Bool`) as well as types declared directly.
    let headFacade = match String.rsplitOnce(headModule, ".") {
        Some((before, _)) -> before
        None -> ""
    }
    let builtinHome = builtinHomeOf(name)
    let tupleHome = if String.startsWith(name, "Tuple") { "Data.Tuple" } else { "" }
    let allowed = [home, headModule, headFacade, builtinHome, tupleHome]
    if Array.contains(allowed, inst.module_) { return {} }
    oops("orphan instance: '" + d.cls + " " + Ty.show(inst.head)
         + "' is declared in '" + inst.module_ + "', but '" + d.cls
         + "' belongs to '" + orElse(home, "the library") + "' and '"
         + Ty.show(inst.head) + "' to '"
         + orElse(headModule, "the language itself")
         + "'. An instance must live with its class or with its type, so that "
         + "no two modules can each claim it.", Some(d.span))
}

fun orElse(s : String, fallback : String) -> String =
    if s == "" { fallback } else { s }

fun builtinHomeOf(name : String) -> String = match name {
    "Int" -> "Data.Int"
    "Byte" -> "Data.Byte"
    "Float" -> "Data.Float"
    "String" -> "Data.String"
    "Char" -> "Data.Char"
    _ -> ""
}

-- `type Elem = a`: what each of the class's families is, at this head.
--
-- The right-hand side is read in the *head's* scope, so its variables are the
-- head's and nothing else may appear -- an unbound one would be a type the
-- instance never fixed, which is a family with no definition rather than a
-- polymorphic one.
fun resolveFamilies(t : ClassTable, d : InstanceDecl, info : ClassInfo,
                    h : Ty.Type, tyvars : Map String Ty.Type,
                    fresh : Ty.Fresh) -> Map String Ty.Type {
    let bound = Map.new()
    for fb in d.families {
        let resolved = match memberNamed(Map.keys(info.families), fb.name) {
            Some(r) -> r
            None -> oops("'" + fb.name + "' is not a type family of class '"
                         + d.cls + "'", Some(fb.span))
        }
        fb.name = resolved
        if Map.contains(bound, resolved) {
            oops("'" + fb.name + "' is defined twice in this instance",
                 Some(fb.span))
        }
        let known = Set.new()
        for k in Map.keys(tyvars) { Set.add(known, k) }
        let body = Decls.toType(t.decls, fb.body, tyvars, fresh)
        let escaped = Array.new(2)
        for k in Map.keys(tyvars) {
            if !Set.contains(known, k) { Array.push(escaped, k) }
        }
        if len(escaped) > 0 {
            oops("'" + Array.sort(escaped)[0] + "' is not bound by the instance "
                 + "head '" + Ty.show(h) + "'", Some(fb.span))
        }
        let fam = match Map.get(t.decls.families, resolved) {
            Some(f) -> f
            None -> oops("unknown type family '" + resolved + "'", Some(fb.span))
        }
        if !Ty.unifyKinds(Ty.kindOf(body), fam.resKind) {
            oops("'" + Ty.show(body) + "' has kind "
                 + Ty.showKind(Ty.kindOf(body)) + ", but '" + fb.name
                 + "' produces a type of kind " + Ty.showKind(fam.resKind),
                 Some(fb.span))
        }
        checkFamDecreasing(fb, body)
        Map.put(bound, resolved, body)
    }
    let missing = Array.new(2)
    for n in Map.keys(info.families) {
        if !Map.contains(bound, n) { Array.push(missing, surfaceMember(n)) }
    }
    if len(missing) > 0 {
        oops("instance '" + d.cls + " " + Ty.show(h) + "' does not define "
             + String.join(Array.sort(missing), ", "), Some(d.span))
    }
    bound
}

-- A family's definition may only apply a family to a *variable*.
--
-- That is what makes reduction terminate. An instance head is a constructor over
-- distinct variables, so a family applied to one of them is applied to a proper
-- subterm of the argument that selected this instance, and every step is a
-- strict decrease. `type Elem = Elem (Array a)` is the rule's whole point: it
-- would reduce forever.
fun checkFamDecreasing(fb : FamBind, body : Ty.Type) -> Unit = match Ty.prune(body) {
    TFam(_, arg, _) -> {
        match Ty.prune(arg) {
            TVar(_) -> {}
            _ -> oops("'" + Ty.show(body) + "' cannot appear here: a type family "
                      + "definition may apply a family only to a type variable "
                      + "of the instance head, so that reduction terminates",
                      Some(fb.span))
        }
        checkFamDecreasing(fb, arg)
    }
    TApp(fn, arg, _) -> {
        checkFamDecreasing(fb, fn)
        checkFamDecreasing(fb, arg)
    }
    TFun(params, ret) -> {
        for p in params { checkFamDecreasing(fb, p) }
        checkFamDecreasing(fb, ret)
    }
    TTuple(elems) -> for e in elems { checkFamDecreasing(fb, e) }
    _ -> {}
}

-- Haskell 98: a constructor applied to distinct type variables.
--
-- `instance Functor (Either Int)` is rejected here, not because it could not be
-- given a meaning, but because allowing it makes instance selection depend on
-- how much of a type is known yet -- and that is what turns a missing instance
-- into a silently different one.
fun checkHeadShape(d : InstanceDecl, h : Ty.Type) -> Unit {
    let (c, spineArgs) = Ty.spine(h)
    let args = match c {
        TTuple(elems) -> elems
        TCon(_) -> spineArgs
        _ -> oops("'" + Ty.show(h) + "' cannot be an instance head: it must be a "
                  + "type constructor applied to distinct type variables",
                  Some(d.head.span))
    }
    let seen = Set.new()
    for arg in args {
        match Ty.prune(arg) {
            TVar(cell) -> {
                if Set.contains(seen, cell.id) {
                    oops("'" + Ty.show(h) + "' cannot be an instance head: '"
                         + d.cls + "' must be given a type constructor applied "
                         + "to distinct type variables", Some(d.head.span))
                }
                Set.add(seen, cell.id)
            }
            _ -> oops("'" + Ty.show(h) + "' cannot be an instance head: '"
                      + d.cls + "' must be given a type constructor applied to "
                      + "distinct type variables", Some(d.head.span))
        }
    }
}

-- Superclass coverage, and the method roster.
fun checkInstance(t : ClassTable, inst : InstInfo) -> Unit {
    let info = classNamed(t, inst.cls)
    for sup in info.supers {
        let demand = Ty.pred(sup.name, [inst.head])
        if !entail(t, inst.context, demand) {
            oops("'" + Ty.showPred(demand) + "' is required by '" + inst.cls
                 + "', but there is no such instance", Some(inst.decl.span))
        }
    }
    let defined = Set.new()
    for m in inst.decl.methods {
        let resolved = match memberNamed(Map.keys(info.methods), m.name) {
            Some(r) -> r
            None -> oops("'" + m.name + "' is not a method of class '"
                         + inst.cls + "'", Some(m.span))
        }
        m.name = resolved
        if Set.contains(defined, resolved) {
            oops("'" + m.name + "' is defined twice in this instance", Some(m.span))
        }
        if Option.isNone(m.body) {
            oops("'" + m.name + "' needs a body: an instance method's type comes "
                 + "from the class, so it does not restate it", Some(m.span))
        }
        Set.add(defined, resolved)
    }
    let missing = Array.new(2)
    for (name, m) in Map.entries(info.methods) {
        if !Set.contains(defined, name) && !hasDefault(m) {
            Array.push(missing, surfaceMember(name))
        }
    }
    if len(missing) > 0 {
        oops("instance '" + inst.cls + " " + Ty.show(inst.head)
             + "' does not define " + String.join(Array.sort(missing), ", "),
             Some(inst.decl.span))
    }
}

-- -- reduction ---------------------------------------------------------------------

-- A family application's definition, or `None` if no instance decides it yet.
--
-- This is `byInst` for types instead of predicates, and it is the same one-way
-- match against the same instance table -- a family is a function defined by
-- cases on the instance head, so there is nothing else it could be. `None` is
-- *stuck*, never failure: an argument no instance covers is a missing instance,
-- and the class predicate that travels beside every family application is what
-- reports it, with the better message and the right span.
fun reduceFam(t : ClassTable, name : String, arg : Ty.Type) -> Option Ty.Type {
    let target = normalize(t, arg)
    let cls = match Map.get(t.decls.families, name) {
        Some(f) -> f.cls
        None -> return None
    }
    for inst in instancesOf(t, cls) {
        match match_(inst.head, target) {
            Some(mapping) -> match Map.get(inst.families, name) {
                Some(body) -> return Some(Decls.substitute(body, mapping))
                None -> {}
            }
            None -> {}
        }
    }
    None
}

-- Reduce family applications at the head of `t` until one sticks.
fun normalize(t : ClassTable, ty : Ty.Type) -> Ty.Type {
    var out = Ty.prune(ty)
    var going = True
    while going {
        match out {
            TFam(name, arg, _) -> match reduceFam(t, name, arg) {
                None -> { going = False }
                Some(reduced) -> { out = Ty.prune(reduced) }
            }
            _ -> { going = False }
        }
    }
    out
}

-- The instance a stuck family application is missing, if its argument is rigid
-- enough. A family over a variable is waiting; one over a constructor that no
-- instance names will wait forever, and saying so where the equation was written
-- beats a stranded-predicate report at the end of the group.
fun uncovered(t : ClassTable, name : String, arg : Ty.Type) -> Option Pred {
    let target = normalize(t, arg)
    let (h, _) = Ty.spine(target)
    match h {
        TCon(_) -> {}
        _ -> return None
    }
    let cls = match Map.get(t.decls.families, name) {
        Some(f) -> f.cls
        None -> return None
    }
    let p = Ty.pred(cls, [target])
    if Option.isSome(byInst(t, p)) { None } else { Some(p) }
}

-- -- entailment --------------------------------------------------------------------

-- A predicate together with everything a class's superclasses imply of it.
--
-- A superclass constrains the class variable itself, so the substitution is just
-- carrying the predicate's argument across.
fun bySuper(t : ClassTable, p : Pred) -> Array Pred {
    let out = [p]
    match Map.get(t.classes, p.name) {
        None -> {}
        Some(info) -> for sup in info.supers {
            for q in bySuper(t, Ty.pred(sup.name, p.args)) { Array.push(out, q) }
        }
    }
    out
}

-- The obligations of the instance that covers `p`, or `None` if none does.
fun byInst(t : ClassTable, p : Pred) -> Option (Array Pred) {
    for inst in instancesOf(t, p.name) {
        match match_(inst.head, p.args[0]) {
            Some(mapping) -> return Some(Array.map(inst.context, fun(q) =
                Ty.pred(q.name, [Decls.substitute(q.args[0], mapping)])))
            None -> {}
        }
    }
    None
}

-- Does `given` prove `p`? Superclasses first, then the instance table.
fun entail(t : ClassTable, given : Array Pred, p : Pred) -> Bool {
    let key = Ty.predKey(p)
    for a in given {
        for q in bySuper(t, a) {
            if Ty.predKey(q) == key { return True }
        }
    }
    match byInst(t, p) {
        None -> False
        Some(qs) -> {
            for q in qs {
                if !entail(t, given, q) { return False }
            }
            True
        }
    }
}

fun isClass(t : ClassTable, name : String) -> Bool = Map.contains(t.classes, name)

-- The method a name denotes, if it denotes one rather than a function.
fun method(t : ClassTable, name : String) -> Option MethodInfo =
    match Map.get(t.owner, name) {
        None -> None
        Some(cls) -> Map.get(classNamed(t, cls).methods, name)
    }

-- Drop a class predicate that the others already imply.
--
-- `[Ord a, Eq a]` is `[Ord a]` when `Eq` is a superclass of `Ord`. This is
-- cosmetic for soundness and load-bearing for legibility: an inferred context
-- that restates its own superclasses is noise.
fun simplify(t : ClassTable, preds : Array Pred) -> Array Pred {
    let out = Array.new(len(preds))
    let equalities = Set.new()
    for var i = 0; i < len(preds); i = i + 1 {
        let p = preds[i]
        if !isClass(t, p.name) {
            if p.name == Ty.equals && len(p.args) == 2 {
                -- Equality is symmetric. Inference can meet both directions when
                -- two associated families meet through a mutable local. Carrying
                -- both into a scheme creates a cyclic rewrite system for Core
                -- checking, although they state one fact.
                let key = symmetricKey(p)
                if Set.contains(equalities, key) { continue }
                Set.add(equalities, key)
            }
            Array.push(out, p)
            continue
        }
        var implied = False
        for var j = 0; j < len(preds) && !implied; j = j + 1 {
            let q = preds[j]
            if i != j && isClass(t, q.name) {
                if Ty.predKey(q) == Ty.predKey(p) {
                    -- A duplicate implies itself; keep the first of the pair.
                    if j < i { implied = True }
                } else {
                    let supers = bySuper(t, q)
                    for var k = 1; k < len(supers); k = k + 1 {
                        if Ty.predKey(supers[k]) == Ty.predKey(p) { implied = True }
                    }
                }
            }
        }
        if !implied { Array.push(out, p) }
    }
    out
}

fun symmetricKey(p : Pred) -> String {
    let parts = Array.sort([Ty.typeKey(p.args[0]), Ty.typeKey(p.args[1])])
    String.join(parts, " ~ ")
}

-- -- matching ----------------------------------------------------------------------

-- One-way matching: bind the pattern's variables so it becomes the target.
--
-- Not unification. An instance head is a *pattern*, and letting it bind
-- variables in the type being classified would let instance selection invent
-- facts about the program rather than discover them.
fun match_(pattern : Ty.Type, target : Ty.Type) -> Option (Map Int Ty.Type) {
    let mapping = Map.new()
    if matchInto(pattern, target, mapping) { Some(mapping) } else { None }
}

fun matches(pattern : Ty.Type, target : Ty.Type) -> Bool =
    Option.isSome(match_(pattern, target))

fun matchInto(p : Ty.Type, t : Ty.Type, mapping : Map Int Ty.Type) -> Bool {
    let pat = Ty.prune(p)
    let target = Ty.prune(t)
    match pat {
        TVar(cell) -> match Map.get(mapping, cell.id) {
            None -> {
                Map.put(mapping, cell.id, target)
                True
            }
            Some(bound) -> Ty.sameType(bound, target)
        }
        TCon(c) -> match target {
            TCon(other) -> c.name == other.name
            _ -> False
        }
        TApp(pf, pa, _) -> match target {
            TApp(tf, ta, _) -> matchInto(pf, tf, mapping) && matchInto(pa, ta, mapping)
            _ -> False
        }
        TFun(pp, pr) -> match target {
            TFun(tp, tr) -> {
                if len(pp) != len(tp) { return False }
                for var i = 0; i < len(pp); i = i + 1 {
                    if !matchInto(pp[i], tp[i], mapping) { return False }
                }
                matchInto(pr, tr, mapping)
            }
            _ -> False
        }
        TTuple(pe) -> match target {
            TTuple(te) -> {
                if len(pe) != len(te) { return False }
                for var i = 0; i < len(pe); i = i + 1 {
                    if !matchInto(pe[i], te[i], mapping) { return False }
                }
                True
            }
            _ -> False
        }
        _ -> False
    }
}

-- -- skolems ----------------------------------------------------------------------

-- Turns a scheme's quantified variables into rigid nullary constructors.
--
-- One of these per checking scope, so that a method variable and an instance
-- variable that happen to share a name still get distinct constants.
type Skolems = Skolems {
    used    : Set.Set String,
    mapping : Map Int Ty.Type,
    -- Every constant made here, for the solver to stamp with the rank of the
    -- binder they belong to. Generation cannot do it: ranks are the solver's,
    -- and it is the one that knows how deep this body sits.
    made    : Array ConCell,
}

fun newSkolems() -> Skolems =
    Skolems { used = Set.new(), mapping = Map.new(), made = Array.new(4) }

fun skolemBind(s : Skolems, cell : VarCell, name : String) -> Ty.Type {
    var candidate = name
    var n = 1
    while Set.contains(s.used, candidate) {
        n = n + 1
        candidate = name + Int.toString(n)
    }
    Set.add(s.used, candidate)
    let con = ConCell { name = candidate, kind = cell.kind, level = Ty.noScope }
    Map.put(s.mapping, cell.id, TCon(con))
    Array.push(s.made, con)
    TCon(con)
}

fun skolemApply(s : Skolems, t : Ty.Type) -> Ty.Type = Decls.substitute(t, s.mapping)

fun skolemApplyPred(s : Skolems, p : Pred) -> Pred =
    Ty.pred(p.name, Array.map(p.args, fun(a) = skolemApply(s, a)))

-- -- helpers ----------------------------------------------------------------------

fun classNamed(t : ClassTable, name : String) -> ClassInfo =
    match Map.get(t.classes, name) {
        Some(i) -> i
        None -> oops("unknown class '" + name + "'", None)
    }

fun instancesOf(t : ClassTable, cls : String) -> Array InstInfo =
    Map.getOr(t.instances, cls, Array.new(0))

-- Which module an internal name came from; empty for a built-in type.
fun moduleOf(name : String) -> String = match String.rsplitOnce(name, "#") {
    Some((before, _)) -> before
    None -> ""
}

-- Resolve an instance-body member by its surface short name.
--
-- Instance bodies declare `fun show`, not a qualified binding. The selected
-- class makes that spelling unambiguous even when another imported class has a
-- method with the same short name.
fun memberNamed(members : Array String, written : String) -> Option String {
    if Array.contains(members, written) { return Some(written) }
    let found = Array.new(2)
    for name in members {
        if surfaceMember(name) == written { Array.push(found, name) }
    }
    if len(found) == 1 { Some(found[0]) } else { None }
}

fun surfaceMember(name : String) -> String {
    let afterDot = match String.rsplitOnce(name, ".") {
        Some((_, after)) -> after
        None -> name
    }
    let afterHash = match String.rsplitOnce(afterDot, "#") {
        Some((_, after)) -> after
        None -> afterDot
    }
    if afterHash == "" { name } else { afterHash }
}

fun oops(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("type error", message), "")
    Some(s) -> Diag.fail(Diag.at("type error", message, s), s.file)
}

fun discard(x : a) -> Unit = {}

-- -- the dump -----------------------------------------------------------------------

-- The class table, in registration order.
--
-- Each class names the variable it abstracts over `a`, and its superclasses,
-- families and methods are printed against that one naming -- so the `a` in
-- `[Semigroup a]` and the `a` in `fun(a, a) -> a` are visibly the same variable.
-- An instance does the same over its head's variables.
fun showClasses(t : ClassTable) -> String {
    let b = String.builder()
    for (name, info) in Map.entries(t.classes) {
        let n = Ty.namerFrom([info.cvar], "")
        String.push(b, "class " + name + " " + info.param + " :: "
                       + Ty.showKind(classKind(info)) + "\n")
        for sup in info.supers {
            String.push(b, "  super " + Ty.showPredWith(sup, n) + "\n")
        }
        for fam in Map.keys(info.families) {
            match Map.get(t.decls.families, fam) {
                Some(fi) -> String.push(b, "  family " + fam + " :: "
                    + Ty.showKind(fi.argKind) + " -> " + Ty.showKind(fi.resKind)
                    + "\n")
                None -> {}
            }
        }
        for (mname, m) in Map.entries(info.methods) {
            String.push(b, "  method " + mname + " : " + Ty.showScheme(m.scheme)
                           + "\n")
        }
    }
    for (cls, insts) in Map.entries(t.instances) {
        for inst in insts {
            let n = Ty.namer("")
            String.push(b, "instance " + cls + " " + Ty.showAt(inst.head, n, 0)
                           + " [" + inst.module_ + "]\n")
            for p in inst.context {
                String.push(b, "  context " + Ty.showPredWith(p, n) + "\n")
            }
            for (fam, body) in Map.entries(inst.families) {
                String.push(b, "  type " + fam + " = " + Ty.showAt(body, n, 0)
                               + "\n")
            }
        }
    }
    String.build(b)
}
