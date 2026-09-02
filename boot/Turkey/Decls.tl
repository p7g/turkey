module Turkey.Decls (
    ConInfo(..), isRecord,
    TyconInfo(..), isMutableRecord,
    FamilyInfo(..),
    DeclTable(..), newDeclTable,
    head, registerAll, settleKinds,
    star, toType, instantiateCon, con, recordFields, fieldType, projectionTypes,
    substitute,
    showDeclarations,
)

-- Type and constructor declarations, resolved into semantic types
-- (`turkey/decls.py`).
--
-- The bridge between what the author wrote (`Turkey.Ast`'s type syntax) and
-- what inference manipulates (`Turkey.Types`). It holds the program's type
-- constructors, its value constructors, and the translation of type syntax --
-- including alias expansion, which is transparent per design.md 7.

import Data.Set as Set
import Turkey.Ast
import Turkey.Diag as Diag
import Turkey.Source (Span)
import Turkey.Types as Ty
-- The *constructors* of the semantic type, without the name itself: in this
-- file `Type` is the syntax the author wrote (`Turkey.Ast`) and `Ty.Type` is
-- what inference manipulates, and translating between them is the whole job.
import Turkey.Types (Kind(..), VarCell(..), Scheme(..),
                     TVar, TCon, ConCell(..), TApp, TFam, TFun, TTuple,
                     TLabel, TIndex,
                     TSet, TBottom)

-- One value constructor. `fields` is `None` for the positional form.
type ConInfo = ConInfo {
    name   : String,
    tycon  : String,
    fields : Option (Array String),
    arity  : Int,
    -- Always `forall params. fun(args...) -> tycon params`.
    scheme : Scheme,
}

fun isRecord(c : ConInfo) -> Bool = Option.isSome(c.fields)

type TyconInfo = TyconInfo {
    name      : String,
    params    : Array String,
    variants  : Array ConInfo,
    isAlias   : Bool,
    aliasBody : Option Type,
    span      : Option Span,
    kind      : Kind,
}

-- design.md 4.5: exactly one variant, carrying a record payload.
fun isMutableRecord(t : TyconInfo) -> Bool =
    len(t.variants) == 1 && isRecord(t.variants[0])

-- An associated type family, as the type language sees it.
--
-- Families are declared inside classes, but they are *type* names, so they are
-- resolved here alongside constructors and aliases -- one place decides what a
-- name in type position means. Both kinds start undecided and are narrowed by
-- use, exactly as a class variable's is: `argKind` is the owning class's
-- parameter kind, and `resKind` is what the family produces.
type FamilyInfo = FamilyInfo {
    name    : String,
    cls     : String,
    argKind : Kind,
    resKind : Kind,
    span    : Option Span,
}

type DeclTable = DeclTable {
    tycons       : Map String TyconInfo,
    constructors : Map String ConInfo,
    -- One head per name, so a constructor's kind cannot be recorded twice and
    -- disagree with itself. Unification compares names, but everything that
    -- reads a kind reads it off the head.
    heads        : Map String Ty.Type,
    -- Filled in by the class table, before any signature is resolved, since a
    -- method's type may mention a family of a class declared further down.
    families     : Map String FamilyInfo,
    -- Short name -> the qualified name that claimed it first. Two modules may
    -- each declare a `Node`; this is what notices, so that both print qualified
    -- rather than both printing `Node` (SPEC-DELTAS.md 43). It is also what the
    -- class and family namespaces are checked against, since a class name is
    -- global and unqualified.
    shorts       : Map String String,
    -- Registration order, for anything that has to walk the table the way it
    -- was built. A `Map` here is insertion-ordered, but the primitives and
    -- `Prim.Array` are in it before any module is read.
    order        : Array String,
}

fun newDeclTable() -> DeclTable {
    Ty.clearQualified()
    let t = DeclTable {
        tycons = Map.new(), constructors = Map.new(), heads = Map.new(),
        families = Map.new(), shorts = Map.new(), order = Array.new(16),
    }
    for name in Ty.primitives() {
        Map.put(t.heads, name, Ty.tcon(name, KStar, Ty.noScope))
        Map.put(t.tycons, name, TyconInfo {
            name = name, params = Array.new(0), variants = Array.new(0),
            isAlias = False, aliasBody = None, span = None, kind = KStar,
        })
        Map.put(t.shorts, name, name)
        Array.push(t.order, name)
    }
    let rawKind = KFun(KStar, KStar)
    Map.put(t.tycons, "Prim.Array", TyconInfo {
        name = "Prim.Array", params = ["a"], variants = Array.new(0),
        isAlias = False, aliasBody = None, span = None, kind = rawKind,
    })
    Map.put(t.heads, "Prim.Array", Ty.tcon("Prim.Array", rawKind, Ty.noScope))
    Array.push(t.order, "Prim.Array")
    t
}

fun head(t : DeclTable, name : String) -> Ty.Type = match Map.get(t.heads, name) {
    Some(h) -> h
    None -> oops("unknown type '" + name + "'", None)
}

-- -- registration -------------------------------------------------------------------

-- Declare every type before resolving any, so they may refer to each other in
-- any order.
fun registerAll(t : DeclTable, decls : Array TypeDecl) -> Unit {
    for d in decls {
        let declared = Set.new()
        for p in d.params { Set.add(declared, p) }
        for te in declarationTypes(d) {
            for v in typeVariables(te) {
                match v.kind {
                    TEVar(name) -> if !Set.contains(declared, name) {
                        oops("type variable '" + name + "' is not declared by "
                             + "type '" + d.name + "'", Some(v.span))
                    }
                    _ -> {}
                }
            }
        }
    }
    for d in decls {
        let short = Ty.shortName(d.name)
        if Ty.isPrimitive(short) {
            oops("cannot redefine the built-in type '" + short + "'", Some(d.span))
        }
        if Map.contains(t.tycons, d.name) {
            oops("type '" + d.name + "' is declared more than once", Some(d.span))
        }
        -- Every declaration is given its kind *skeleton* up front -- one arrow
        -- per parameter, over kind variables. Arity is syntactic, so the
        -- skeleton is exact and only the parameters' own kinds are left to
        -- discover; that is why mutual recursion needs no dependency pass here,
        -- unlike the value level. Kinds are not polymorphic, so there is
        -- nothing to generalize between one declaration and the next.
        let kind = Ty.kindArrow(len(d.params), KStar)
        Map.put(t.tycons, d.name, TyconInfo {
            name = d.name, params = d.params, variants = Array.new(2),
            isAlias = Option.isSome(d.alias), aliasBody = d.alias,
            span = Some(d.span), kind = kind,
        })
        Map.put(t.heads, d.name, Ty.tcon(d.name, kind, Ty.noScope))
        Array.push(t.order, d.name)
        match Map.get(t.shorts, short) {
            Some(claimed) -> if claimed != d.name {
                -- Two modules declare this short name. Neither may keep it.
                Ty.qualify(claimed)
                Ty.qualify(d.name)
            }
            None -> Map.put(t.shorts, short, d.name)
        }
    }
    for d in decls {
        if Option.isNone(d.alias) { resolveVariants(t, d) }
    }
    for d in decls {
        if Option.isSome(d.alias) {
            checkAliasAcyclic(t, d.name, d.name, Set.new())
            kindCheckAlias(t, tyconOf(t, d.name))
        }
    }
    settleKinds(t)
}

fun tyconOf(t : DeclTable, name : String) -> TyconInfo = match Map.get(t.tycons, name) {
    Some(info) -> info
    None -> oops("unknown type '" + name + "'", None)
}

-- The declaration's parameters as variables carrying the kinds its own kind
-- assigns them, so applying one in the body constrains the header.
fun paramVars(info : TyconInfo, level : Int) -> Map String Ty.Type {
    let out = Map.new()
    var k = info.kind
    for p in info.params {
        match Ty.kprune(k) {
            KFun(arg, res) -> {
                Map.put(out, p, TVar(Ty.newVar(level, arg)))
                k = res
            }
            _ -> oops("type '" + info.name + "' has fewer arrows than parameters",
                      info.span)
        }
    }
    out
}

-- Resolve an alias body once, for its kinds alone.
--
-- Aliases are expanded at each use rather than stored, so nothing else would
-- ever look at the body with the header's parameters in scope -- and
-- `type Id f a = f a` would leave `f` defaulted to `*`.
--
-- The body must classify values. An alias has to be saturated where it is used,
-- so an alias *to* an unapplied constructor could never be written down, and
-- rejecting it here says so where the mistake is.
fun kindCheckAlias(t : DeclTable, info : TyconInfo) -> Unit = match info.aliasBody {
    None -> {}
    Some(body) -> discard(star(t, body, paramVars(info, 1), fun() = Ty.freshVar(1)))
}

-- Haskell 98's defaulting rule: a kind still undecided is `*`.
--
-- Kinds are first-order here -- no polymorphism, no `forall k` -- so a variable
-- left over after the declarations have been read is one nothing constrained,
-- and `*` is the only choice that keeps the type usable.
fun settleKinds(t : DeclTable) -> Unit {
    for (_, info) in Map.entries(t.tycons) { discard(Ty.defaultKind(info.kind)) }
    for (_, c) in Map.entries(t.constructors) {
        for v in c.scheme.quantified { discard(Ty.defaultKind(v.kind)) }
    }
}

fun resolveVariants(t : DeclTable, decl : TypeDecl) -> Unit {
    let info = tyconOf(t, decl.name)
    -- The type's own parameters become variables shared by every variant, so
    -- `Some a` and `None` both land in `Option a`.
    let tyvars = paramVars(info, 1)
    let args = Array.new(len(decl.params))
    for p in decl.params { Array.push(args, Map.getOr(tyvars, p, TBottom)) }
    let result = Ty.applyTo(head(t, decl.name), args, Some(decl.span))

    match decl.variants {
        None -> {}
        Some(variants) -> for c in variants {
            match Map.get(t.constructors, c.name) {
                Some(other) -> oops("constructor '" + c.name + "' is already "
                    + "declared by type '" + other.tycon + "'", Some(c.span))
                None -> {}
            }
            let names = match c.fields {
                Some(fs) -> Some(Array.map(fs, firstOfPair))
                None -> None
            }
            let argTypes = Array.new(4)
            match c.fields {
                Some(fs) -> for (_, te) in fs {
                    Array.push(argTypes, star(t, te, tyvars, undeclared))
                }
                None -> for te in c.args {
                    Array.push(argTypes, star(t, te, tyvars, undeclared))
                }
            }
            let s = Ty.generalize(TFun(argTypes, result), 0, Array.new(0))
            let cinfo = ConInfo {
                name = c.name, tycon = decl.name, fields = names,
                arity = len(argTypes), scheme = s,
            }
            Array.push(info.variants, cinfo)
            Map.put(t.constructors, c.name, cinfo)
        }
    }
}

fun firstOfPair(p : (String, Type)) -> String {
    let (name, _) = p
    name
}

-- design.md 7: type aliases may not be recursive.
fun checkAliasAcyclic(t : DeclTable, root : String, name : String,
                      seen : Set.Set String) -> Unit {
    match Map.get(t.tycons, name) {
        None -> return {}
        Some(info) -> {
            if !info.isAlias || Set.contains(seen, name) { return {} }
            let inner = Set.new()
            for s in Set.elements(seen) { Set.add(inner, s) }
            Set.add(inner, name)
            for referenced in referencedTycons(info.aliasBody) {
                if referenced == root {
                    oops("type alias '" + root + "' is recursive", info.span)
                }
                checkAliasAcyclic(t, root, referenced, inner)
            }
        }
    }
}

-- -- translation --------------------------------------------------------------------

-- `toType`, where a type that classifies values is required.
--
-- Every annotation and every constructor field is such a position: `x : Array`
-- names a constructor, not a type, and nothing can have it. This is the check
-- that used to be an arity comparison, moved to the places that actually need
-- saturation -- `toType` itself no longer does, because a partially applied
-- constructor is exactly what a higher-kinded argument is made of.
fun star(t : DeclTable, te : Type, tyvars : Map String Ty.Type,
         fresh : Ty.Fresh) -> Ty.Type {
    let out = toType(t, te, tyvars, fresh)
    if !Ty.unifyKinds(Ty.kindOf(out), KStar) {
        oops("'" + Ty.show(out) + "' has kind " + Ty.showKind(Ty.kindOf(out))
             + ", but a type of kind * is needed here", Some(te.span))
    }
    out
}

-- Translate type syntax into a semantic type.
--
-- `tyvars` maps annotation type-variable names to the types they stand for --
-- unification variables while a type is being inferred, and rigid skolem
-- constants while a body is being checked against a signature -- and is shared
-- across an entire function, so the `a` in a parameter and the `a` in a body
-- annotation are the same variable (SPEC-DELTAS.md 13).
fun toType(t : DeclTable, te : Type, tyvars : Map String Ty.Type,
           fresh : Ty.Fresh) -> Ty.Type = match te.kind {
    TEVar(name) -> match Map.get(tyvars, name) {
        Some(found) -> found
        None -> {
            let made = TVar(fresh())
            Map.put(tyvars, name, made)
            made
        }
    }
    TETuple(elems) -> TTuple(Array.map(elems, fun(e) = toType(t, e, tyvars, fresh)))
    TEFun(params, ret) -> TFun(
        Array.map(params, fun(p) = toType(t, p, tyvars, fresh)),
        toType(t, ret, tyvars, fresh))
    TEApp(fn, args) -> Ty.applyTo(
        toType(t, fn, tyvars, fresh),
        Array.map(args, fun(a) = toType(t, a, tyvars, fresh)),
        Some(te.span))
    TECon(name, args) -> {
        if Map.contains(t.families, name) {
            return family(t, te, name, args, tyvars, fresh)
        }
        let info = match Map.get(t.tycons, name) {
            Some(i) -> i
            None -> oops("unknown type '" + name + "'", Some(te.span))
        }
        let resolved = Array.map(args, fun(a) = toType(t, a, tyvars, fresh))
        if info.isAlias {
            -- Aliases are transparent: expand rather than build a constructor.
            -- They must be *saturated* first. An alias is the one head that is
            -- not rigid, so a partially applied one would make `f a ~ g b`
            -- decomposition unsound -- and it is why type-level lambdas are out.
            if len(resolved) != len(info.params) {
                oops("type alias '" + name + "' expects "
                     + Int.toString(len(info.params)) + " argument(s), but "
                     + Int.toString(len(resolved)) + " were given; an alias "
                     + "cannot be partially applied", Some(te.span))
            }
            let substitution = Map.new()
            for var i = 0; i < len(info.params); i = i + 1 {
                Map.put(substitution, info.params[i], resolved[i])
            }
            expandAlias(t, info, substitution, fresh)
        } else {
            Ty.applyTo(head(t, name), resolved, Some(te.span))
        }
    }
}

-- `Elem c` -- a family application, which is not an application.
--
-- A family is saturated where it is written, for the same reason an alias is:
-- it is the one head that is not rigid, and `unify` decomposes an application
-- pointwise. Any further arguments *are* an ordinary application, of whatever
-- the family returns.
fun family(t : DeclTable, te : Type, name : String, args : Array Type,
           tyvars : Map String Ty.Type, fresh : Ty.Fresh) -> Ty.Type {
    let fam = match Map.get(t.families, name) {
        Some(f) -> f
        None -> oops("unknown type family '" + name + "'", Some(te.span))
    }
    if len(args) == 0 {
        oops("'" + name + "' is a type family of class '" + fam.cls
             + "' and must be applied to a type", Some(te.span))
    }
    let resolved = Array.map(args, fun(a) = toType(t, a, tyvars, fresh))
    if !Ty.unifyKinds(Ty.kindOf(resolved[0]), fam.argKind) {
        oops("'" + Ty.show(resolved[0]) + "' has kind "
             + Ty.showKind(Ty.kindOf(resolved[0])) + ", but '" + name
             + "' is a family over a type of kind " + Ty.showKind(fam.argKind),
             Some(te.span))
    }
    Ty.applyTo(TFam(name, resolved[0], fam.resKind),
               Array.slice(resolved, 1, len(resolved)), Some(te.span))
}

fun expandAlias(t : DeclTable, info : TyconInfo, substitution : Map String Ty.Type,
                fresh : Ty.Fresh) -> Ty.Type {
    -- The alias body's own parameter names are bound to the supplied arguments;
    -- anything else in it is a fresh variable.
    let local = Map.new()
    let body = match info.aliasBody {
        Some(b) -> toType(t, b, local, fresh)
        None -> oops("type alias '" + info.name + "' has no body", info.span)
    }
    let mapping = Map.new()
    for (name, stood) in Map.entries(local) {
        match (Map.get(substitution, name), stood) {
            (Some(arg), TVar(cell)) -> Map.put(mapping, cell.id, arg)
            _ -> {}
        }
    }
    substitute(body, mapping)
}

-- -- constructor helpers ------------------------------------------------------------

fun instantiateCon(t : DeclTable, name : String, fresh : Ty.Fresh,
                   where : Option Span) -> Ty.Type =
    match Map.get(t.constructors, name) {
        Some(info) -> Ty.instantiate(info.scheme, fresh)
        None -> oops("unknown constructor '" + name + "'", where)
    }

fun con(t : DeclTable, name : String) -> Option ConInfo = Map.get(t.constructors, name)

-- -- record field lookup ------------------------------------------------------------

-- The named type's field names, or `None` if it is not a mutable record.
fun recordFields(t : DeclTable, name : String) -> Option (Array String) =
    match Map.get(t.tycons, name) {
        None -> None
        Some(info) -> if isMutableRecord(info) { info.variants[0].fields } else { None }
    }

-- The type of `receiver.label`, for a receiver already resolved.
--
-- The receiver's arguments *are* the constructor's parameters, so substituting
-- them directly is exact. Instantiating the constructor's scheme and unifying
-- its result with the receiver -- which is what `HasField` solving would
-- otherwise do -- gives the same type but introduces fresh variables first, and
-- unification would then impose their level on the receiver's. Solving happens
-- after generation, at whatever level the solver has reached rather than the
-- one the field access was written at, so that level is not ours to impose.
fun fieldType(t : DeclTable, receiver : Ty.Type, label : String) -> Ty.Type {
    let (h, args) = Ty.spine(receiver)
    let name = match h {
        TCon(c) -> c.name
        _ -> oops("a field was read from something that is not a record", None)
    }
    let c = tyconOf(t, name).variants[0]
    match c.scheme.body {
        TFun(params, ret) -> {
            let mapping = paramMapping(ret, args)
            let index = match c.fields {
                Some(fs) -> match Array.indexOf(fs, label) {
                    Some(i) -> i
                    None -> oops("'" + name + "' has no field '" + label + "'", None)
                }
                None -> oops("'" + name + "' is not a record", None)
            }
            substitute(params[index], mapping)
        }
        _ -> oops("constructor '" + c.name + "' is not a function", None)
    }
}

-- Payload types for an immutable, single positional variant.
fun projectionTypes(t : DeclTable, receiver : Ty.Type) -> Option (Array Ty.Type) {
    let (h, args) = Ty.spine(receiver)
    match h {
        TCon(c) -> match Map.get(t.tycons, c.name) {
            None -> None
            Some(info) -> {
                if len(info.variants) != 1 || isRecord(info.variants[0]) {
                    return None
                }
                match info.variants[0].scheme.body {
                    TFun(params, ret) -> {
                        let mapping = paramMapping(ret, args)
                        Some(Array.map(params, fun(p) = substitute(p, mapping)))
                    }
                    _ -> None
                }
            }
        }
        _ -> None
    }
}

-- The constructor result's spine arguments are its type's parameters, in order,
-- so pairing them with the receiver's arguments is the substitution.
fun paramMapping(ret : Ty.Type, args : Array Ty.Type) -> Map Int Ty.Type {
    let (_, params) = Ty.spine(ret)
    let mapping = Map.new()
    var i = 0
    while i < len(params) && i < len(args) {
        match params[i] {
            TVar(cell) -> Map.put(mapping, cell.id, args[i])
            _ -> {}
        }
        i = i + 1
    }
    mapping
}

fun substitute(t : Ty.Type, mapping : Map Int Ty.Type) -> Ty.Type = match Ty.prune(t) {
    TVar(cell) -> Map.getOr(mapping, cell.id, TVar(cell))
    TApp(fn, arg, k) -> TApp(substitute(fn, mapping), substitute(arg, mapping), k)
    TFam(name, arg, k) -> TFam(name, substitute(arg, mapping), k)
    TFun(params, ret) -> TFun(Array.map(params, fun(p) = substitute(p, mapping)),
                              substitute(ret, mapping))
    TTuple(elems) -> TTuple(Array.map(elems, fun(e) = substitute(e, mapping)))
    _ -> Ty.prune(t)
}

-- -- walks over type syntax ---------------------------------------------------------

fun referencedTycons(te : Option Type) -> Array String {
    let out = Array.new(4)
    match te {
        Some(t) -> referencedInto(t, out)
        None -> {}
    }
    out
}

fun referencedInto(te : Type, out : Array String) -> Unit = match te.kind {
    TECon(name, args) -> {
        Array.push(out, name)
        for a in args { referencedInto(a, out) }
    }
    TEApp(fn, args) -> {
        referencedInto(fn, out)
        for a in args { referencedInto(a, out) }
    }
    TETuple(elems) -> for e in elems { referencedInto(e, out) }
    TEFun(params, ret) -> {
        for p in params { referencedInto(p, out) }
        referencedInto(ret, out)
    }
    TEVar(_) -> {}
}

fun declarationTypes(decl : TypeDecl) -> Array Type = match decl.alias {
    Some(body) -> [body]
    None -> {
        let out = Array.new(4)
        match decl.variants {
            Some(variants) -> for c in variants {
                match c.fields {
                    Some(fs) -> for (_, te) in fs { Array.push(out, te) }
                    None -> for te in c.args { Array.push(out, te) }
                }
            }
            None -> {}
        }
        out
    }
}

fun typeVariables(te : Type) -> Array Type {
    let out = Array.new(4)
    typeVariablesInto(te, out)
    out
}

fun typeVariablesInto(te : Type, out : Array Type) -> Unit = match te.kind {
    TEVar(_) -> Array.push(out, te)
    TETuple(elems) -> for e in elems { typeVariablesInto(e, out) }
    TEFun(params, ret) -> {
        for p in params { typeVariablesInto(p, out) }
        typeVariablesInto(ret, out)
    }
    TEApp(fn, args) -> {
        typeVariablesInto(fn, out)
        for a in args { typeVariablesInto(a, out) }
    }
    TECon(_, args) -> for a in args { typeVariablesInto(a, out) }
}

-- A constructor field mentioning a variable the header did not declare is
-- caught before any of this runs, so reaching here is a bug rather than a
-- program's mistake.
fun undeclared() -> VarCell =
    oops("type declaration validation missed an undeclared variable", None)

fun oops(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("type error", message), "")
    Some(s) -> Diag.fail(Diag.at("type error", message, s), s.file)
}

fun discard(x : a) -> Unit = {}

-- -- the dump -----------------------------------------------------------------------

-- The declaration table, one line per entity, in registration order. The
-- milestone's diff target: it is where a kind, a generalization and the
-- qualified-name rule all become something two implementations can disagree
-- about in writing.
fun showDeclarations(t : DeclTable) -> String {
    let b = String.builder()
    for name in t.order {
        let info = tyconOf(t, name)
        String.push(b, "type " + name)
        for p in info.params { String.push(b, " " + p) }
        String.push(b, " :: " + Ty.showKind(info.kind))
        if info.isAlias { String.push(b, " = alias") }
        String.push(b, "\n")
        for c in info.variants {
            String.push(b, "  con " + c.name + "/" + Int.toString(c.arity))
            match c.fields {
                Some(fs) -> String.push(b, " {" + String.join(fs, ", ") + "}")
                None -> {}
            }
            String.push(b, " : " + Ty.showScheme(c.scheme) + "\n")
        }
    }
    String.build(b)
}
