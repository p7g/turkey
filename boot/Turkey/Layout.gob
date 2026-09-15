module Turkey.Layout (
    layoutOf, transparent, share,
)

-- One compiled body per distinct *layout* of the type arguments
-- (`turkey/layout.py`).
--
-- `plan.txt` item 10's second tier, and the pass that makes unboxing total.
--
-- Monomorphization specializes a binding per distinct tuple of type arguments,
-- and it is partial **by decision**: Turkey admits polymorphic recursion, so the
-- set of types a binding is used at can be infinite -- `fun depth(x : a, n :
-- Int)` recursing at `Pair a` asks for `Pair Int`, `Pair (Pair Int)`, and so on
-- without end -- and item 6 caps it. Past the cap a call site keeps its `CTyApp`
-- and calls a body that is still generic.
--
-- That body is what `Turkey.Mono.checkLayouts` refuses, and rightly: it may take
-- polymorphic data apart, and the layout it would read a field at is decided
-- where it is compiled while the layout the field was written at was decided at
-- the construction site. Nothing makes the two agree.
--
-- This pass is the same specialization keyed on the *layout* of each type
-- argument rather than on the type, and it needs no cap for the reason the cap
-- existed. There are eight layouts and infinitely many types; `layout(Pair a)`
-- is `ptr` whatever `a` is, so the chain that diverged under a type key --
-- `Pair Int`, `Pair (Pair Int)`, ... -- collapses to a single key. The thing
-- that could not be bounded was the type index. The layout index is bounded by
-- construction, so the worklist below terminates on any program.
--
-- **The copies stay polymorphic.** A copy is *not* the body with a type
-- substituted into it. Substituting one would be a lie: `Data.Array#push` at
-- layout `ptr` is called with `Array String` and with `Array (Option Int)`
-- alike, and there is no type to write that both of those check against. So the
-- copy keeps the original's scheme -- its call sites type-check unchanged, and
-- `Turkey.Coretc` checks it exactly as it checked the original -- and carries
-- the one extra fact it is compiled under: `CBind.layouts`, the layout each
-- abstracted variable stands for.
--
-- **What gets a copy.** Only the bindings whose layouts have to be known: the
-- ones `checkLayouts` would refuse, which are those holding a parameter whose
-- type *mentions* an abstracted variable without *being* one. `Array a` is
-- transparent and `a` is not, and the distinction is parametricity: a body may
-- hold an `a` and pass it on knowing nothing, and that is why a bare `a`
-- parameter needs no copy and gets none. The set is closed under one more rule:
-- a generic binding that calls a transparent one at a type mentioning its *own*
-- binders cannot say which copy it means, so it needs its layouts known too.

import Data.Set as Set
import Turkey.Core
import Turkey.Core (Core(..), CoreKind(..), CBind(..), CProgram(..), CParam(..),
                    Mapper(..), mapKind, namesOf, abstractionBinders,
                    transparentParameters)
import Turkey.Decls as Decls
import Turkey.Decls (DeclTable(..))
import Turkey.Diag as Diag
import Turkey.Types as Ty
import Turkey.Types (TVar, TCon, TApp, TFam, TFun, TTuple, TSet, TBottom)

-- The eight representations, as the strings `CBind.layouts` is keyed by. A
-- name rather than a constructor because that is what a copy's name is built
-- from and what the map holds; there is nothing to compute from one.
let unit_ = "unit"
let i1 = "i1"
let i8 = "i8"
let i32 = "i32"
let i64 = "i64"
let f64 = "f64"
let ptr = "ptr"

-- The layout a value of `ty` is held at, or `None` if that is not known.
--
-- `abstracted` is the binding's `layouts`: the layout each of its abstracted
-- variables stands for, when it is one of `share`'s copies. A variable that is
-- not in it has no layout, and this says so.
--
-- Partial on purpose. Answering `boxed` there would put two different facts
-- under one value, because `boxed` is also a real representation that the
-- runtime's box produces and consumes -- and a reader cannot tell "I do not
-- know" from "there is a box here". `key` below needs exactly that distinction.
fun layoutOf(decls : DeclTable, ty : Ty.Type,
             abstracted : Map Int String) -> Option String {
    let t = Ty.prune(ty)
    match t {
        TVar(cell) -> return Map.get(abstracted, cell.id)
        TBottom -> return Some(unit_)
        _ -> {}
    }
    -- A newtype is its payload, so its representation is its payload's. See
    -- `Decls.newtypes`, which is also where the recursion that would make this
    -- loop is excluded.
    match Decls.erasedPayload(decls, t) {
        Some(payload) -> return layoutOf(decls, payload, abstracted)
        None -> {}
    }
    match t {
        TCon(c) -> {
            if c.name == "Int" { return Some(i64) }
            if c.name == "Byte" { return Some(i8) }
            if c.name == "Char" { return Some(i32) }
            if c.name == "Float" { return Some(f64) }
            if c.name == "String" { return Some(ptr) }
            if c.name == "Unit" { return Some(unit_) }
            if c.name == "Data.Bool.Type#Bool" { return Some(i1) }
            -- Every other declared type is a heap object, and a heap object is
            -- reached by pointer. Said here rather than left to a catch-all at
            -- the end, so that the end can be a refusal.
            Some(ptr)
        }
        TFun(_, _) -> Some(ptr)
        TApp(_, _, _) -> Some(ptr)
        TTuple(_) -> Some(ptr)
        TFam(_, _, _) -> None
        -- A `TSet` is the one that can arrive: a numeric literal whose type was
        -- still a *decision*. Defaulting settles it at generalization and the
        -- type table's own check is that it did, so one here is a hole in that
        -- rather than a type wanting a representation.
        _ -> oops("no layout for the type '" + Ty.show(t) + "'")
    }
}

-- Whether this binding could take polymorphic data apart.
--
-- Every binder counts as abstracted here, which is the difference from
-- `checkLayouts`: that one asks after this pass has run and so may discount a
-- variable whose layout a copy already carries, while this one is deciding
-- which bindings need such a copy in the first place. The predicate itself is
-- `Core.transparentParameters`, shared with it, because when the two were
-- written separately they were blind to constrained bindings together.
fun transparent(b : CBind) -> Bool {
    let ids = Set.new()
    for v in abstractionBinders(b) { Set.add(ids, v.id) }
    len(transparentParameters(b, ids)) > 0
}

-- Every `CTyApp` of a top-level name anywhere inside a term.
fun applications(e : Core, out : Array (String, Array Ty.Type)) -> Unit {
    match e.kind {
        CTyApp(fn, args) -> match fn.kind {
            CVar(n) -> Array.push(out, (n, args))
            _ -> {}
        }
        _ -> {}
    }
    discard(mapKind(e.kind, Mapper {
        expr = fun(x) = {
            applications(x, out)
            x
        },
        type_ = fun(t) = t,
        bind = fun(b) = {
            applications(b.value, out)
            b
        },
    }))
}

-- The bindings whose abstracted layouts have to be known.
--
-- The transparent ones, and then whatever calls them at a type of its own.
--
-- Every binding, not even only the ones reachable from `main`: there is more
-- than one notion of reachable here, and a binding this pass declined to share
-- because one walk could not see it is a binding the other walk hands the
-- backend with no layouts. Sharing an unreachable binding costs a copy nothing
-- emits.
fun needsLayouts(binds : Map String CBind) -> Set.Set String {
    let found = Set.new()
    for (name, b) in Map.entries(binds) {
        if transparent(b) { Set.add(found, name) }
    }
    var grew = True
    while grew {
        grew = False
        for (name, b) in Map.entries(binds) {
            if Set.contains(found, name) { continue }
            let abstracted = Set.new()
            for v in abstractionBinders(b) { Set.add(abstracted, v.id) }
            if len(Set.elements(abstracted)) == 0 { continue }
            let uses = Array.new(4)
            applications(b.value, uses)
            for (called, args) in uses {
                if !Set.contains(found, called) { continue }
                for v in Ty.varsOf(args) {
                    if Set.contains(abstracted, v.id) {
                        Set.add(found, name)
                        grew = True
                        break
                    }
                }
            }
        }
    }
    found
}

-- The layout of each type argument, or `None` if one is not knowable.
fun key(decls : DeclTable, args : Array Ty.Type,
        abstracted : Map Int String) -> Option String {
    let out = Array.new(len(args))
    for a in args {
        match layoutOf(decls, a, abstracted) {
            Some(l) -> Array.push(out, l)
            None -> return None
        }
    }
    Some(String.join(out, ","))
}

type Sharer = Sharer {
    decls  : DeclTable,
    binds  : Map String CBind,
    shared : Set.Set String,
    -- "original|key" -> the name built for it.
    done   : Map String String,
    -- original -> the copies, in request order.
    made   : Map String (Array CBind),
    queue  : Array (String, String, String),
    used   : Set.Set String,
}

-- The program with one body per layout of every binding that needs one.
fun share(program : CProgram, decls : DeclTable) -> CProgram {
    let binds = Map.new()
    for b in Array.append(program.dicts, program.binds) {
        Map.put(binds, b.name, b)
    }
    let shared = needsLayouts(binds)
    if len(Set.elements(shared)) == 0 { return program }
    let used = Set.new()
    for (name, _) in Map.entries(binds) { Set.add(used, name) }
    let s = Sharer {
        decls = decls, binds = binds, shared = shared, done = Map.new(),
        made = Map.new(), queue = Array.new(8), used = used,
    }

    let rewritten = Map.new()
    for (name, b) in Map.entries(binds) {
        if !Set.contains(shared, name) {
            Map.put(rewritten, name, withValue(b, rewrite(s, b.value, b.layouts)))
        }
    }
    -- A cursor rather than a removal, because the queue is first-in-first-out
    -- and grows while it is drained: building a copy requests the copies that
    -- copy calls for.
    var at = 0
    while at < len(s.queue) {
        let (name, original, k) = s.queue[at]
        at = at + 1
        let copy = build(s, name, s.binds[original], k)
        match Map.get(s.made, original) {
            Some(found) -> Array.push(found, copy)
            None -> Map.put(s.made, original, [copy])
        }
    }

    let kept = reachable(s, rewritten)
    let dicts = Array.new(len(program.dicts))
    let rest = Array.new(len(program.binds))
    for b in program.dicts { emit(s, kept, rewritten, b, dicts) }
    for b in program.binds { emit(s, kept, rewritten, b, rest) }
    CProgram { dicts = dicts, binds = rest }
}

fun emit(s : Sharer, kept : Set.Set String, rewritten : Map String CBind,
         b : CBind, out : Array CBind) -> Unit {
    -- Beside the original and ahead of it, as `Turkey.Mono` places its
    -- specializations, and for the same reason: a copy depends on no more than
    -- the original does, and top-level bindings are evaluated in this order.
    for copy in Map.getOr(s.made, b.name, Array.new(0)) { Array.push(out, copy) }
    if Set.contains(kept, b.name) {
        Array.push(out, Map.getOr(rewritten, b.name, b))
    }
}

-- The names still worth emitting, which is every one but a shared original that
-- nothing calls any more.
--
-- A shared binding's original is the one body here that is *not* rewritten,
-- because rewriting it would need the layouts it does not have. Once every call
-- site has gone to a copy it is dead, and leaving it is not harmless: it names
-- the other originals, so one that survives keeps the rest alive, and
-- `checkLayouts` then refuses a program for a body nothing would have compiled.
--
-- A fixed point rather than one pass, since dropping one can orphan the next.
fun reachable(s : Sharer, rewritten : Map String CBind) -> Set.Set String {
    let made = Map.new()
    for (_, copies) in Map.entries(s.made) {
        for c in copies { Map.put(made, c.name, c) }
    }
    let live = Set.new()
    for (name, _) in Map.entries(s.binds) {
        if !Set.contains(s.shared, name) { Set.add(live, name) }
    }
    for (name, _) in Map.entries(made) { Set.add(live, name) }
    var grew = True
    while grew {
        grew = False
        for name in Array.sort(Set.elements(live)) {
            let b = match Map.get(made, name) {
                Some(found) -> found
                None -> match Map.get(rewritten, name) {
                    Some(found) -> found
                    None -> match Map.get(s.binds, name) {
                        Some(found) -> found
                        None -> continue
                    }
                }
            }
            for used in Set.elements(namesOf(b.value)) {
                if Map.contains(s.binds, used) && !Set.contains(live, used) {
                    Set.add(live, used)
                    grew = True
                }
            }
        }
    }
    live
}

fun request(s : Sharer, name : String, k : String) -> String {
    let slot = name + "|" + k
    match Map.get(s.done, slot) {
        Some(found) -> return found
        None -> {}
    }
    var made = name + "@[" + k + "]"
    while Set.contains(s.used, made) { made = made + "~" }
    Set.add(s.used, made)
    -- Recorded before the body is built, which is what makes a recursive
    -- binding terminate: the copy's own call to itself, at its own layouts,
    -- finds this entry rather than asking for another copy.
    Map.put(s.done, slot, made)
    Array.push(s.queue, (made, name, k))
    made
}

fun build(s : Sharer, made : String, b : CBind, k : String) -> CBind {
    let abstracted = Map.new()
    let parts = String.split(k, ",")
    let binders = abstractionBinders(b)
    for var i = 0; i < len(binders) && i < len(parts); i = i + 1 {
        Map.put(abstracted, binders[i].id, parts[i])
    }
    let copy = withValue(b, rewrite(s, b.value, abstracted))
    copy.name = made
    copy.layouts = abstracted
    copy
}

fun withValue(b : CBind, value : Core) -> CBind = CBind {
    name = b.name, ty = b.ty, binders = b.binders, value = value,
    span = b.span, mutable = b.mutable, module_ = b.module_,
    equations = b.equations, layouts = b.layouts,
}

-- Every call to a shared binding, pointed at the copy it means.
fun rewrite(s : Sharer, e : Core, abstracted : Map Int String) -> Core {
    let here = match e.kind {
        CTyApp(fn, args) -> match fn.kind {
            CVar(n) -> if Set.contains(s.shared, n) {
                -- Positional, so a binding that states its `forall` in both
                -- places at once cannot be keyed from one type application and
                -- is left alone -- `checkLayouts` then says so, which is a
                -- limit that is visible rather than one that is guessed at.
                let want = len(abstractionBinders(s.binds[n]))
                match if len(args) == want { key(s.decls, args, abstracted) }
                      else { None } {
                    Some(k) -> Some(mkCore(e.ty, e.span,
                        CTyApp(mkCore(fn.ty, fn.span, CVar(request(s, n, k))),
                               args)))
                    None -> None
                }
            } else { None }
            _ -> None
        }
        _ -> None
    }
    match here {
        Some(made) -> return made
        None -> {}
    }
    mkCore(e.ty, e.span, mapKind(e.kind, Mapper {
        expr = fun(x) = rewrite(s, x, abstracted),
        type_ = fun(t) = t,
        bind = fun(b) = withValue(b, rewrite(s, b.value, abstracted)),
    }))
}

fun oops(message : String) -> a =
    Diag.fail(Diag.whole("internal error", message), "")

fun discard(x : a) -> Unit = {}
