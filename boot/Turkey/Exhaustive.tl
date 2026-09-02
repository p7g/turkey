module Turkey.Exhaustive (Witness(..), check, render)

-- Pattern-matching exhaustiveness checking (design.md 5.1,
-- `turkey/exhaustive.py`).
--
-- Maranget's usefulness algorithm ("Warnings for pattern matching", JFP 2007) in
-- its witness-producing form, so a warning can name a value the match does not
-- handle rather than merely saying one exists.
--
-- A non-exhaustive match is a warning, not an error -- design.md 5.1 makes an
-- unhandled case a runtime panic. The check runs after inference, when every
-- scrutinee type is known.

import Turkey.Ast
import Turkey.Ast as Ast
import Turkey.Decls as Decls
import Turkey.Decls (DeclTable(..), ConInfo(..), TyconInfo(..))
import Turkey.Types as Ty
import Turkey.Types (TVar, TCon, TApp, TFam, TFun, TTuple)

-- The four shapes the algorithm understands. Not the surface patterns: a record
-- pattern is reordered into declaration order and filled out with wildcards, so
-- that every row for a constructor has the same width.
type Witness
    = WWild
    | WCon(String, Array Witness)
    | WTuple(Array Witness)
    | WLit(Ast.Lit)

fun normalize(p : Pat, decls : DeclTable) -> Witness = match p.kind {
    PVar(_) -> WWild
    PWild -> WWild
    PAnnot(inner, _) -> normalize(inner, decls)
    PLit(l) -> WLit(l)
    PTuple(elems) -> WTuple(Array.map(elems, fun(e) = normalize(e, decls)))
    PCon(name, args) -> WCon(name, Array.map(args, fun(a) = normalize(a, decls)))
    PRecord(name, fields) -> {
        -- Reorder the fields into declaration order and fill in the ones the
        -- pattern left out, so every row for a constructor has the same width.
        let supplied = Map.new()
        for (label, sub) in fields { Map.put(supplied, label, sub) }
        let args = Array.new(4)
        match Decls.con(decls, name) {
            Some(info) -> match info.fields {
                Some(names) -> for n in names {
                    match Map.get(supplied, n) {
                        Some(sub) -> Array.push(args, normalize(sub, decls))
                        None -> Array.push(args, WWild)
                    }
                }
                None -> {}
            }
            None -> {}
        }
        WCon(name, args)
    }
}

-- The head constructor a pattern tests for. Python builds a tuple and compares
-- tuples; this is that tuple, and `sameKey` is that comparison. Encoding it as a
-- string would mean parsing it back out, which is how a key stops being a key.
type Key
    = KNone
    | KCon(String)
    | KTuple(Int)
    | KLit(String, String)

fun keyOf(w : Witness) -> Key = match w {
    WWild -> KNone
    WCon(name, _) -> KCon(name)
    WTuple(elems) -> KTuple(len(elems))
    WLit(l) -> KLit(Ast.litKind(l), litText(l))
}

fun sameKey(a : Key, b : Key) -> Bool = match (a, b) {
    (KNone, KNone) -> True
    (KCon(x), KCon(y)) -> x == y
    (KTuple(x), KTuple(y)) -> x == y
    (KLit(k1, v1), KLit(k2, v2)) -> k1 == k2 && v1 == v2
    _ -> False
}

fun isNone(k : Key) -> Bool = match k {
    KNone -> True
    _ -> False
}

fun holdsKey(keys : Array Key, wanted : Key) -> Bool {
    for k in keys {
        if sameKey(k, wanted) { return True }
    }
    False
}

fun litText(l : Ast.Lit) -> String = match l {
    LInt(n) -> Int.toString(n)
    LFloat(f) -> Float.toString(f)
    LString(s) -> s
    LChar(c) -> Char.toString(c)
}

-- -- type-directed signature information --------------------------------------------

-- Every head constructor of a type, or `None` when there are too many. `Int`,
-- `String` and friends have unboundedly many values, so a column of them is only
-- covered by a wildcard.
fun signature(decls : DeclTable, t : Ty.Type) -> Option (Array Key) {
    match Ty.prune(t) {
        TTuple(elems) -> return Some([KTuple(len(elems))])
        _ -> {}
    }
    match Ty.headCon(t) {
        None -> None
        Some(name) -> match Map.get(decls.tycons, name) {
            None -> None
            Some(info) -> {
                if len(info.variants) == 0 { return None }
                -- `Bool` needs no special case: it is `type Bool = False | True`
                -- in the Prelude, so its two variants come out of this table
                -- like any other type's.
                Some(Array.map(info.variants, fun(v) = KCon(v.name)))
            }
        }
    }
}

-- The column types produced by specializing a column of this type on this key.
fun subTypes(decls : DeclTable, key : Key, t : Ty.Type) -> Array Ty.Type =
    match key {
        KTuple(_) -> match Ty.prune(t) {
            TTuple(elems) -> elems
            _ -> Array.new(0)
        }
        KLit(_, _) -> Array.new(0)
        KNone -> Array.new(0)
        KCon(name) -> {
            -- Exhaustiveness runs after solving, so these variables belong to no
            -- pool: they exist only to carry the constructor's field types into
            -- the unification below, and are discarded with it.
            match Decls.instantiateCon(decls, name, fun() = Ty.freshVar(0), None) {
                TFun(params, ret) -> {
                    -- The pattern already type-checked, so this only propagates
                    -- the scrutinee's arguments into the field types.
                    Ty.unify(ret, t, None, "", None)
                    params
                }
                _ -> Array.new(0)
            }
        }
    }

fun keyArity(decls : DeclTable, key : Key, t : Ty.Type) -> Int = match key {
    KTuple(n) -> n
    KLit(_, _) -> 0
    KNone -> 0
    KCon(name) -> match Decls.con(decls, name) {
        Some(info) -> info.arity
        None -> 0
    }
}

fun rebuild(key : Key, args : Array Witness) -> Witness = match key {
    KTuple(_) -> WTuple(args)
    KLit(kind, _) -> WWild
    KNone -> WWild
    KCon(name) -> WCon(name, args)
}

-- -- the algorithm ------------------------------------------------------------------

-- A row of patterns no row of the matrix covers, or `None` if it is exhaustive.
fun witness(decls : DeclTable, matrix : Array (Array Witness),
            types : Array Ty.Type) -> Option (Array Witness) {
    if len(types) == 0 {
        return if len(matrix) > 0 { None } else { Some(Array.new(0)) }
    }
    let headType = types[0]
    let restTypes = Array.slice(types, 1, len(types))
    let used = Array.new(4)
    for row in matrix {
        let k = keyOf(row[0])
        if !isNone(k) && !holdsKey(used, k) { Array.push(used, k) }
    }
    let sig = signature(decls, headType)

    match sig {
        Some(keys) -> {
            var complete = True
            for k in keys {
                if !holdsKey(used, k) { complete = False }
            }
            if complete {
                -- The match is exhaustive only if it is exhaustive under every
                -- constructor.
                for k in keys {
                    let (specialized, argTypes) =
                        specialize(decls, matrix, k, headType)
                    match witness(decls, specialized,
                                  Array.append(argTypes, restTypes)) {
                        Some(found) -> {
                            let width = len(argTypes)
                            let built = rebuild(k, Array.slice(found, 0, width))
                            return Some(Array.append([built],
                                Array.slice(found, width, len(found))))
                        }
                        None -> {}
                    }
                }
                return None
            }
        }
        None -> {}
    }

    -- Incomplete: a value built from a constructor nobody tested reaches the
    -- default matrix, so recurse there and report one of the missing heads.
    let default_ = Array.new(len(matrix))
    for row in matrix {
        if isNone(keyOf(row[0])) {
            Array.push(default_, Array.slice(row, 1, len(row)))
        }
    }
    match witness(decls, default_, restTypes) {
        None -> None
        Some(found) -> {
            let missing = match sig {
                None -> WWild
                Some(keys) -> {
                    var chosen = WWild
                    var picked = False
                    for k in keys {
                        if !holdsKey(used, k) && !picked {
                            picked = True
                            chosen = rebuild(k, wilds(keyArity(decls, k, headType)))
                        }
                    }
                    chosen
                }
            }
            Some(Array.append([missing], found))
        }
    }
}

fun wilds(n : Int) -> Array Witness {
    let out = Array.new(n)
    for var i = 0; i < n; i = i + 1 { Array.push(out, WWild) }
    out
}

fun specialize(decls : DeclTable, matrix : Array (Array Witness), key : Key,
               headType : Ty.Type) -> (Array (Array Witness), Array Ty.Type) {
    let argTypes = subTypes(decls, key, headType)
    let width = len(argTypes)
    let rows = Array.new(len(matrix))
    for row in matrix {
        let h = row[0]
        let rest = Array.slice(row, 1, len(row))
        let k = keyOf(h)
        if isNone(k) {
            Array.push(rows, Array.append(wilds(width), rest))
        } else if sameKey(k, key) {
            let args = match h {
                WCon(_, a) -> a
                WTuple(elems) -> elems
                _ -> Array.new(0)
            }
            Array.push(rows, Array.append(args, rest))
        }
    }
    (rows, argTypes)
}

-- -- reporting ----------------------------------------------------------------------

-- The witness a match fails to handle, rendered, or `None` when it is
-- exhaustive.
fun check(decls : DeclTable, arms : Array Arm, scrutinee : Ty.Type) -> Option String {
    let matrix = Array.new(8)
    for arm in arms {
        for p in arm.patterns { Array.push(matrix, [normalize(p, decls)]) }
    }
    match witness(decls, matrix, [scrutinee]) {
        None -> None
        Some(found) -> Some(render(found[0]))
    }
}

fun render(w : Witness) -> String = match w {
    WWild -> "_"
    -- Unreachable in practice: a literal key never appears in a signature, so a
    -- witness is never rebuilt from one. Spelled the language's way rather than
    -- the host's regardless -- Python reaches for `repr` here, which is the
    -- same host dependency the token and tree dumps had to be rid of.
    WLit(l) -> litText(l)
    WTuple(elems) -> "(" + String.join(Array.map(elems, render), ", ") + ")"
    -- A witness is shown the way the author would write it, so the constructor
    -- loses the module qualification resolution gave it (SPEC-DELTAS.md 43).
    WCon(name, args) -> {
        let short = Ty.shortName(name)
        if len(args) == 0 { short }
        -- The paren form is self-delimiting, so a nested witness needs no extra
        -- grouping: `Some(Some(_))`, not `Some (Some _)`.
        else { short + "(" + String.join(Array.map(args, render), ", ") + ")" }
    }
}
