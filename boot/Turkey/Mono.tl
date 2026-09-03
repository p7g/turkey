module Turkey.Mono (
    maxSpecializations, maxTypeNodes, rounds,
    State(..), newState,
    monomorphize,
)

-- Monomorphization: one copy of a polymorphic binding per type it is used at
-- (`plan.txt` item 6, `turkey/mono.py`).
--
-- Item 5 left a typed Core in which every dictionary is an explicit argument at
-- a known type -- which is precisely the input this needs. A generalized binding
-- carries its binders, and every use of one is a type application at recorded
-- arguments, so "which types is this used at" is a question the IR already
-- answers. This pass reads the answer and writes a copy per answer.
--
-- What that buys: a dictionary is built once rather than rebuilt per request; a
-- dictionary at a call site is a *name*; a method selected from a ground
-- dictionary is then a projection out of a record whose definition is right
-- here, so it becomes a binding of its own; and what nothing reaches is dropped.
--
-- ## It is a *partial* pass, and that is a decision
--
-- Turkey admits polymorphic recursion, so the reachable set of specializations
-- can be infinite -- the standard reason monomorphization is undecidable. There
-- is a cap, and when it trips the call site keeps its type application and goes
-- on calling the generic binding. Every generic binding therefore survives the
-- pass unchanged, which is why the output is a mixture. The cap says nothing
-- when it trips: Turkey does not *promise* monomorphization, and a binding that
-- stays polymorphic is compiled by layout-keyed sharing instead.
--
-- ## It goes round twice, and the budget does not restart
--
-- Specializing and devirtualizing each *make* bindings the other would have
-- worked on. So the pipeline runs twice and reachability runs once at the end.
-- The thing that has to be got right is not the loop but the cap: a budget spent
-- per round is no bound at all, since N rounds would grant one binding 32N
-- copies. `State` is the answer -- one budget for the whole pass, charged to the
-- original a copy descends from, plus the memo of what has already been built.

import Data.Set as Set
import Turkey.Ast
import Turkey.Classes as Classes
import Turkey.Classes (ClassTable(..), ClassInfo(..), MethodInfo(..))
import Turkey.Core
import Turkey.Core as Cr
import Turkey.Coretc as Coretc
import Turkey.Decls as Decls
import Turkey.Decls (DeclTable(..))
import Turkey.Diag as Diag
import Turkey.Source (Span)
import Turkey.Typed as Typed
import Turkey.Types as Ty
import Turkey.Types (Kind(..), VarCell(..), TVar, TCon, TApp, TFam, TFun,
                     TTuple)

-- How many distinct specializations one binding may have, and how large a type
-- argument may be. Either limit stops the unrolling a polymorphically recursive
-- binding would otherwise ask for. The numbers are deliberately generous: no
-- program in the suite comes near them, so tripping one is a real signal.
let maxSpecializations = 32
let maxTypeNodes = 64

-- How many times the whole pipeline runs. A fixed number rather than a fixed
-- point, and the reason is the cap: a budget spent per round is not a bound at
-- all. So the budget is shared across rounds *and* the rounds are counted,
-- which makes the bound easy to state -- a binding descended from `f` has at
-- most `maxSpecializations` copies in the output, whatever this is set to.
let rounds = 2

let dictPrefix = "%Dict."

-- -- small questions about types ----------------------------------------------------

-- No unbound variable anywhere in it, so it names one type and not a family of
-- them. The test a specialization request has to pass.
fun ground(t : Ty.Type) -> Bool = len(Ty.varsOf([t])) == 0

fun typeSize(t : Ty.Type) -> Int = match Ty.prune(t) {
    TApp(fn, arg, _) -> 1 + typeSize(fn) + typeSize(arg)
    TFam(_, arg, _) -> 1 + typeSize(arg)
    TFun(params, ret) -> {
        var n = 1 + typeSize(ret)
        for p in params { n = n + typeSize(p) }
        n
    }
    TTuple(elems) -> {
        var n = 1
        for e in elems { n = n + typeSize(e) }
        n
    }
    _ -> 1
}

-- A type, as a fragment of a name.
--
-- Readability is the whole requirement -- `Main#squash@Int` should be findable
-- in a dump by someone who knows what they are looking for -- and *not*
-- uniqueness, which `fresh` provides separately by keying on the type and
-- disambiguating a collision with a counter.
fun mangle(t : Ty.Type) -> String = match Ty.prune(t) {
    TCon(c) -> Ty.shortName(c.name)
    TApp(_, _, _) -> {
        let (h, args) = Ty.spine(Ty.prune(t))
        mangle(h) + "(" + String.join(Array.map(args, mangle), ",") + ")"
    }
    TFam(name, arg, _) -> name + "(" + mangle(arg) + ")"
    TFun(_, _) -> "fun"
    TTuple(_) -> "tuple"
    _ -> "_"
}

-- The class a `%Dict.C h` type names, or `None` if it is not one.
fun dictClass(ty : Ty.Type) -> Option String {
    let (h, args) = Ty.spine(Ty.prune(ty))
    match h {
        TCon(c) -> if String.startsWith(c.name, dictPrefix) && len(args) == 1 {
            String.stripPrefix(c.name, dictPrefix)
        } else { None }
        _ -> None
    }
}

-- Whether this binding is a lambda over nothing but dictionaries.
--
-- Two things wear that shape and the collapse treats them alike: an instance
-- with a context (a function from a dictionary to a dictionary) and any binding
-- whose scheme retained a predicate. Both are applied to evidence at every use
-- and neither needs to be, once the evidence is ground.
--
-- Asked structurally rather than by the `%inst.` prefix, because the shape is
-- what the collapse depends on and a name is a convention a later pass could
-- change. `%Dict.` is not a convention in the same sense: no source type can be
-- spelled that way, so a parameter with one is evidence and nothing else.
fun takesDictionaries(b : CBind) -> Bool = match b.value.kind {
    CLam(params, _, _) -> {
        if len(params) == 0 { return False }
        for p in params {
            if Option.isNone(dictClass(p.ty)) { return False }
        }
        True
    }
    _ -> False
}

-- -- what one round tells the next --------------------------------------------------

-- The budget, the memo, and the hoists, none of which may restart with a round.
--
-- **The budget.** A per-round count is not a cap: refuse a binding its
-- thirty-third copy and the next round, counting from zero, grants it
-- thirty-two more. So the count is charged to the binding a copy *descends
-- from*, which is what `origin` records.
--
-- **The memo.** A second round that re-asked for a binding at the same types
-- would not find round one's answer, and would build a byte-identical second
-- copy under a disambiguated name -- specialization as a source of duplication,
-- which is the opposite of the point.
--
-- **The hoists.** A field a previous round lifted into a binding is one this
-- round's rewrites already name; hoisting it again would leave two bindings
-- where the program needs one.
type State = State {
    counts : Map String Int,
    done   : Map String String,
    origin : Map String String,
    hoists : Map String String,
}

fun newState() -> State = State {
    counts = Map.new(), done = Map.new(), origin = Map.new(),
    hoists = Map.new(),
}

fun root(st : State, name : String) -> String = Map.getOr(st.origin, name, name)

-- Charge one copy to whatever this name descends from, or refuse.
fun spend(st : State, name : String) -> Bool {
    let r = root(st, name)
    let n = Map.getOr(st.counts, r, 0)
    if n >= maxSpecializations { return False }
    Map.put(st.counts, r, n + 1)
    True
}

-- Record that a name is a descendant of another's original. Transitive by
-- construction: the root is stored, never the parent. Used for a specialization
-- and for a hoisted method alike -- the method is charged to the dictionary it
-- came out of because that is what it is, a piece of it.
fun derive(st : State, made : String, parent : String) -> Unit =
    Map.put(st.origin, made, root(st, parent))

-- A generalized binding *inside* a body -- a `let` that generalized, or a local
-- `fun` -- and the instantiations found for it.
--
-- It cannot be lifted to the top level and specialized there, because its body
-- may close over the enclosing binding's parameters. So it is specialized in
-- place: one extra binding beside the original, per type it was used at.
type Local = Local {
    binders : Array VarCell,
    insts   : Map String (String, Array Ty.Type),
}

fun newLocal(binders : Array VarCell) -> Local =
    Local { binders = binders, insts = Map.new() }

-- -- the whole program, and the specializations asked of it -------------------------

type Mono = Mono {
    program    : CProgram,
    decls      : DeclTable,
    classes    : ClassTable,
    state      : State,
    byname     : Map String CBind,
    generic    : Map String CBind,
    takesDicts : Set.Set String,
    -- Every top-level dictionary binding. A collapse may only be handed one of
    -- these, since the binding it makes lives out here with them. A dictionary
    -- this pass *builds* joins the set: the second collapse of a chain is handed
    -- the first one's answer, and refusing it there would leave the rebuild in
    -- place one level up.
    topDicts   : Set.Set String,
    dictGroup  : Set.Set String,
    made       : Map String (Array CBind),
    queue      : Array (String, CBind, Array Ty.Type, Option (Array String)),
    used       : Set.Set String,
}

fun newMono(program : CProgram, decls : DeclTable, classes : ClassTable,
            state : State) -> Mono = Mono {
    program = program, decls = decls, classes = classes, state = state,
    byname = Map.new(), generic = Map.new(), takesDicts = Set.new(),
    topDicts = Set.new(), dictGroup = Set.new(), made = Map.new(),
    queue = Array.new(8), used = Set.new(),
}

-- One binding's body, rewritten under one substitution.
--
-- Created per specialization (with the binders mapped to that specialization's
-- type arguments) and per ground binding (with an empty substitution, where it
-- only rewrites call sites and touches no types).
type Rewriter = Rewriter {
    owner     : Mono,
    subst     : Map Int Ty.Type,
    equations : Array (Ty.Type, Ty.Type),
    fams      : Ty.Families,
    rename    : Map String String,
    -- A local name that is nothing but another name for a top-level dictionary.
    -- An instance's own method opens with `let %d2.C = %inst.C.T` -- the
    -- self-dictionary the lowering binds -- and passing *that* to a recursive
    -- instance is what the collapse has to see through, since the binding it
    -- hoists the application into cannot see a local name.
    aliases   : Map String String,
    scopes    : Array (Map String Local),
}

fun newRewriter(owner : Mono, subst : Map Int Ty.Type,
                equations : Array (Ty.Type, Ty.Type),
                scopes : Option (Array (Map String Local))) -> Rewriter {
    -- Substituted before the family reducer is built from them: a given
    -- `Item s ~ Op` becomes `Item (Array Int) ~ Op` under the specialization,
    -- and it is the substituted form that is a rewrite rule for the types in the
    -- body. Plain substitution and not the reducing one, since the reducing one
    -- is what this makes.
    let eqs = Array.map(equations, fun(pair) = {
        let (a, b) = pair
        (Decls.substitute(a, subst), Decls.substitute(b, subst))
    })
    Rewriter {
        owner = owner, subst = subst, equations = eqs,
        fams = Coretc.famsOf(owner.classes, eqs),
        rename = Map.new(), aliases = Map.new(),
        scopes = match scopes {
            Some(s) -> s
            None -> Array.new(4)
        },
    }
}

-- A rewriter for a nested binding, sharing the local scopes so a specialized
-- local body can still reach the locals around it.
fun childRewriter(r : Rewriter, subst : Map Int Ty.Type,
                  equations : Array (Ty.Type, Ty.Type)) -> Rewriter {
    let made = newRewriter(r.owner, subst, equations, Some(r.scopes))
    for (k, v) in Map.entries(r.aliases) { Map.put(made.aliases, k, v) }
    made
}

-- A type under the substitution, with families reduced.
--
-- Reducing matters: `Item s` over a rigid `s` is stuck in the generic body and
-- becomes `Item (Array Int)` here, which the instance table decides -- and a
-- node still claiming the unreduced form would be rejected by a checker that had
-- reduced it.
--
-- With no substitution there is nothing to do. That is not just an optimization:
-- a ground binding's types were reduced by the lowering already, and re-deriving
-- them would be a second opinion where the milestone wants none.
fun rTy(r : Rewriter, t : Ty.Type) -> Ty.Type {
    if len(Map.keys(r.subst)) == 0 { return t }
    Typed.reduceDeep(Decls.substitute(t, r.subst), Some(r.fams))
}

fun rewrite(r : Rewriter, e : Core) -> Core = match e.kind {
    CVar(name) -> mkCore(rTy(r, e.ty), e.span,
                         CVar(Map.getOr(r.rename, name, name)))
    CTyApp(fn, args) -> rewriteTyApp(r, e, fn, args)
    CApp(_, _) -> match collapse(r, e) {
        Some(made) -> made
        None -> generic(r, e)
    }
    CLet(name, bound, value, body, binders) ->
        rewriteLet(r, e, name, bound, value, body, binders)
    CLetRec(binds, body) -> rewriteLetRec(r, e, binds, body)
    _ -> generic(r, e)
}

-- Every node not named above: rebuild it, rewriting the children and
-- substituting the types. `Turkey.Core.mapKind` is that rule, written once.
fun generic(r : Rewriter, e : Core) -> Core = mkCore(rTy(r, e.ty), e.span,
    mapKind(e.kind, Mapper {
        expr = fun(x) = rewrite(r, x),
        type_ = fun(t) = rTy(r, t),
        bind = fun(b) = rewriteBind(r, b),
    }))

-- A type application: the one place a specialization is asked for.
--
-- The operand is a name or a method projection. A name is what this pass can
-- specialize; a projection is a method's own quantification, which lives in a
-- record field and stays.
fun rewriteTyApp(r : Rewriter, e : Core, fn : Core,
                 args : Array Ty.Type) -> Core {
    let resolved = Array.map(args, fun(a) = rTy(r, a))
    match fn.kind {
        CVar(name) -> match instantiate(r, name, resolved, e) {
            Some(made) -> return made
            None -> {}
        }
        _ -> {}
    }
    mkCore(rTy(r, e.ty), e.span, CTyApp(rewrite(r, fn), resolved))
}

-- The specialized name for a use, or `None` to leave it generic.
fun instantiate(r : Rewriter, given : String, args : Array Ty.Type,
                e : Core) -> Option Core {
    let name = Map.getOr(r.rename, given, given)
    for a in args {
        if !ground(a) { return None }
    }
    match localNamed(r, name) {
        Some(local) -> return Some(mkCore(rTy(r, e.ty), e.span,
            CVar(localInst(r, name, local, args))))
        None -> {}
    }
    let b = match Map.get(r.owner.generic, name) {
        Some(x) -> x
        None -> return None
    }
    if len(b.binders) != len(args) { return None }
    match request(r.owner, b, args, None) {
        Some(made) -> Some(mkCore(rTy(r, e.ty), e.span, CVar(made)))
        None -> None
    }
}

-- An application to ground evidence becomes a binding with the evidence already
-- in it.
--
-- For an instance that removes the per-request rebuild: the application is
-- collapsed rather than merely specialized because specializing alone would
-- leave it a *function*, still applied, still allocating a record every time.
-- For an ordinary binding it removes the parameter, which is what makes the
-- dictionary a top-level name inside the body -- which is what devirtualization
-- then needs to see.
--
-- Coherence is what makes both sound: the dictionary arguments are ground, so
-- they are the only ones this name could be applied to at these types, and doing
-- the application once does it with the same contents.
fun collapse(r : Rewriter, e : Core) -> Option Core = match e.kind {
    CApp(fn, args) -> collapseApp(r, e, fn, args)
    _ -> None
}

fun collapseApp(r : Rewriter, e : Core, fn : Core,
                args : Array Core) -> Option Core {
    var head = fn
    var targs = Array.new(0)
    match fn.kind {
        CTyApp(inner, ta) -> {
            targs = Array.map(ta, fun(a) = rTy(r, a))
            head = inner
        }
        _ -> {}
    }
    let given = match head.kind {
        CVar(n) -> n
        _ -> return None
    }
    let name = Map.getOr(r.rename, given, given)
    let b = match Map.get(r.owner.byname, name) {
        Some(x) -> x
        None -> return None
    }
    if !Set.contains(r.owner.takesDicts, name) { return None }
    let params = match b.value.kind {
        CLam(ps, _, _) -> ps
        _ -> return None
    }
    if len(b.binders) != len(targs) || len(params) != len(args) { return None }
    let lowered = Array.map(args, fun(a) = rewrite(r, a))
    let supplied = Array.new(len(lowered))
    for a in lowered {
        match globalDict(r, a) {
            -- A context this pass could not reduce to a *top-level* name -- a
            -- capped instance, or a dictionary parameter still in scope. The
            -- collapse hoists this application to the top level, so an argument
            -- that is not visible there cannot come along.
            None -> return None
            Some(n) -> Array.push(supplied, n)
        }
    }
    match request(r.owner, b, targs, Some(supplied)) {
        Some(made) -> Some(mkCore(rTy(r, e.ty), e.span, CVar(made)))
        None -> None
    }
}

-- The top-level dictionary this expression names, or `None`.
fun globalDict(r : Rewriter, e : Core) -> Option String = match e.kind {
    CVar(given) -> {
        let name = Map.getOr(r.aliases, given, given)
        if Set.contains(r.owner.topDicts, name) { Some(name) } else { None }
    }
    _ -> None
}

-- -- generalized bindings inside a body ---------------------------------------------

fun localNamed(r : Rewriter, name : String) -> Option Local {
    for var i = len(r.scopes) - 1; i >= 0; i = i - 1 {
        match Map.get(r.scopes[i], name) {
            Some(found) -> return Some(found)
            None -> {}
        }
    }
    None
}

fun localInst(r : Rewriter, name : String, local : Local,
              args : Array Ty.Type) -> String {
    let key = argsKey(args)
    match Map.get(local.insts, key) {
        Some((made, _)) -> made
        None -> {
            let made = fresh(r.owner, name, args)
            Map.put(local.insts, key, (made, args))
            made
        }
    }
}

fun argsKey(args : Array Ty.Type) -> String =
    String.join(Array.map(args, Ty.typeKey), "|")

fun rewriteLet(r : Rewriter, e : Core, name : String, bound : Ty.Type,
               value : Core, body : Core, binders : Array VarCell) -> Core {
    if len(binders) == 0 {
        -- Written out rather than left to `generic`, because the alias has to be
        -- recorded between the value and the body: the value is what decides
        -- whether there is one, and the body is where it is used.
        let v = rewrite(r, value)
        let had = Map.contains(r.aliases, name)
        let saved = Map.getOr(r.aliases, name, "")
        match globalDict(r, v) {
            Some(named) -> Map.put(r.aliases, name, named)
            None -> {}
        }
        let b = rewrite(r, body)
        if had { Map.put(r.aliases, name, saved) }
        else { discard(Map.delete(r.aliases, name)) }
        return mkCore(rTy(r, e.ty), e.span,
                      CLet(name, rTy(r, bound), v, b, Array.new(0)))
    }
    let local = newLocal(binders)
    let scope = Map.new()
    Map.put(scope, name, local)
    Array.push(r.scopes, scope)
    let b = rewrite(r, body)
    let made = drain(r, local, binders, value)
    discard(Array.pop(r.scopes))
    var out = b
    for var i = len(made) - 1; i >= 0; i = i - 1 {
        let (n, v) = made[i]
        out = mkCore(rTy(r, e.ty), e.span, CLet(n, v.ty, v, out, Array.new(0)))
    }
    -- The generic binding stays, outermost, so that a use this pass could not
    -- specialize -- a capped one, or one under a variable that is not this
    -- binding's -- still finds something to name. It is a value, so evaluating it
    -- costs an allocation and nothing else, and reachability removes it when
    -- nothing reads it.
    mkCore(rTy(r, e.ty), e.span,
           CLet(name, rTy(r, bound), rewrite(r, value), out, binders))
}

-- Build every instantiation of one local binding, including the ones discovered
-- while building the others.
fun drain(r : Rewriter, local : Local, binders : Array VarCell,
          value : Core) -> Array (String, Core) {
    let out = Array.new(4)
    let seen = Set.new()
    var going = True
    while going {
        let todo = Array.new(4)
        for (k, v) in Map.entries(local.insts) {
            if !Set.contains(seen, k) { Array.push(todo, (k, v)) }
        }
        if len(todo) == 0 { return out }
        for (key, made) in todo {
            Set.add(seen, key)
            let (name, args) = made
            let sub = childRewriter(r, extended(r.subst, binders, args),
                                    r.equations)
            sub.rename = r.rename
            Array.push(out, (name, rewrite(sub, value)))
        }
    }
    out
}

fun extended(subst : Map Int Ty.Type, binders : Array VarCell,
             args : Array Ty.Type) -> Map Int Ty.Type {
    let out = Map.new()
    for (k, v) in Map.entries(subst) { Map.put(out, k, v) }
    for var i = 0; i < len(binders) && i < len(args); i = i + 1 {
        Map.put(out, binders[i].id, args[i])
    }
    out
}

fun rewriteLetRec(r : Rewriter, e : Core, binds : Array CBind,
                  body : Core) -> Core {
    let poly = Map.new()
    for b in binds {
        if len(b.binders) > 0 { Map.put(poly, b.name, newLocal(b.binders)) }
    }
    if len(Map.keys(poly)) == 0 { return generic(r, e) }
    Array.push(r.scopes, poly)
    let rest = rewrite(r, body)
    -- The generic copies first, and for the reason a `let` keeps its original: a
    -- sibling or a capped call may still name them. Their own recursive calls are
    -- at their own binders, which are not ground, so nothing inside them
    -- specializes.
    let out = Array.map(binds, fun(b) = rewriteBind(r, b))
    let pending = Map.new()
    for b in binds {
        if len(b.binders) > 0 { Map.put(pending, b.name, b) }
    }
    let seen = Set.new()
    var going = True
    while going {
        let todo = Array.new(4)
        for (ownerName, local) in Map.entries(poly) {
            for (k, v) in Map.entries(local.insts) {
                if !Set.contains(seen, ownerName + "|" + k) {
                    Array.push(todo, (ownerName, k, v))
                }
            }
        }
        if len(todo) == 0 { going = False }
        for (ownerName, key, made) in todo {
            Set.add(seen, ownerName + "|" + key)
            let (name, args) = made
            let orig = match Map.get(pending, ownerName) {
                Some(x) -> x
                None -> oops("a local instantiation lost its original", e.span)
            }
            let sub = childRewriter(r, extended(r.subst, orig.binders, args),
                                    orig.equations)
            sub.rename = r.rename
            let b = CBind {
                name = name, ty = rTy(sub, orig.ty), binders = Array.new(0),
                value = rewrite(sub, orig.value), span = orig.span,
                mutable = False, module_ = orig.module_,
                equations = sub.equations,
            }
            Array.push(out, b)
        }
    }
    discard(Array.pop(r.scopes))
    mkCore(rTy(r, e.ty), e.span, CLetRec(out, rest))
}

-- One member of a group, generic or not, under this rewriter -- with its own
-- equations added, since a member may carry some.
fun rewriteBind(r : Rewriter, b : CBind) -> CBind {
    if len(b.equations) == 0 {
        return CBind {
            name = b.name, ty = rTy(r, b.ty), binders = b.binders,
            value = rewrite(r, b.value), span = b.span, mutable = b.mutable,
            module_ = b.module_, equations = b.equations,
        }
    }
    let sub = childRewriter(r, r.subst, b.equations)
    sub.rename = r.rename
    CBind {
        name = b.name, ty = rTy(sub, b.ty), binders = b.binders,
        value = rewrite(sub, b.value), span = b.span, mutable = b.mutable,
        module_ = b.module_, equations = sub.equations,
    }
}

-- -- the specializer ----------------------------------------------------------------

fun runMono(m : Mono) -> CProgram {
    for (group, isDict) in [(m.program.dicts, True), (m.program.binds, False)] {
        for b in group {
            Map.put(m.byname, b.name, b)
            Set.add(m.used, b.name)
            if len(b.binders) > 0 { Map.put(m.generic, b.name, b) }
            if isDict { Set.add(m.dictGroup, b.name) }
            if isDict && len(b.binders) == 0 { Set.add(m.topDicts, b.name) }
            if takesDictionaries(b) { Set.add(m.takesDicts, b.name) }
        }
    }
    -- A ground binding is where every specialization starts: its body's type
    -- arguments are already types rather than variables. `main` is one of these,
    -- and so is every instance dictionary with no context.
    let rewritten = Map.new()
    for group in [m.program.dicts, m.program.binds] {
        for b in group {
            if len(b.binders) == 0 { Map.put(rewritten, b.name, rewriteTop(m, b)) }
        }
    }
    var at = 0
    while at < len(m.queue) {
        let (name, b, targs, dicts) = m.queue[at]
        at = at + 1
        let group = match Map.get(m.made, b.name) {
            Some(g) -> g
            None -> {
                let g = Array.new(2)
                Map.put(m.made, b.name, g)
                g
            }
        }
        Array.push(group, build(m, name, b, targs, dicts))
    }

    let out = CProgram { dicts = Array.new(16), binds = Array.new(32) }
    for (group, target) in [(m.program.dicts, out.dicts),
                            (m.program.binds, out.binds)] {
        for b in group {
            -- Beside its original, which is already in dependency order, and
            -- ahead of it, since a specialization depends on no more than the
            -- original does. Top-level bindings are evaluated in this order, so
            -- it has to be one that works.
            for made in Map.getOr(m.made, b.name, Array.new(0)) {
                Array.push(target, made)
            }
            Array.push(target, Map.getOr(rewritten, b.name, b))
        }
    }
    out
}

fun rewriteTop(m : Mono, b : CBind) -> CBind {
    let r = newRewriter(m, Map.new(), b.equations, None)
    CBind {
        name = b.name, ty = b.ty, binders = b.binders,
        value = rewrite(r, b.value), span = b.span, mutable = b.mutable,
        module_ = b.module_, equations = b.equations,
    }
}

-- Ask for a binding at these arguments. `None` means the cap said no.
fun request(m : Mono, b : CBind, targs : Array Ty.Type,
            dicts : Option (Array String)) -> Option String {
    for t in targs {
        if !ground(t) { return None }
    }
    let key = b.name + "|" + argsKey(targs) + "|" + match dicts {
        Some(ds) -> String.join(ds, ",")
        None -> "*"
    }
    match Map.get(m.state.done, key) {
        Some(found) -> return Some(found)
        None -> {}
    }
    -- Size first, then the budget, so a request refused for its type does not
    -- also spend a copy the program never received.
    for t in targs {
        if typeSize(t) > maxTypeNodes { return None }
    }
    if !spend(m.state, b.name) { return None }
    let name = fresh(m, b.name, targs)
    derive(m.state, name, b.name)
    match dicts {
        Some(_) -> if Set.contains(m.dictGroup, b.name) { Set.add(m.topDicts, name) }
        None -> {}
    }
    -- Recorded *before* the body is built, which is what makes a recursive
    -- binding terminate: the copy's own call to itself, at its own arguments,
    -- finds this entry rather than asking for another copy.
    Map.put(m.state.done, key, name)
    Array.push(m.queue, (name, b, targs, dicts))
    Some(name)
}

fun fresh(m : Mono, name : String, targs : Array Ty.Type) -> String {
    let base = if len(targs) == 0 { name + "@" } else {
        name + "@" + String.join(Array.map(targs, mangle), ",")
    }
    var candidate = base
    var n = 1
    while Set.contains(m.used, candidate) {
        n = n + 1
        candidate = base + "~" + Int.toString(n)
    }
    Set.add(m.used, candidate)
    candidate
}

fun build(m : Mono, name : String, b : CBind, targs : Array Ty.Type,
          dicts : Option (Array String)) -> CBind {
    let subst = Map.new()
    for var i = 0; i < len(b.binders) && i < len(targs); i = i + 1 {
        Map.put(subst, b.binders[i].id, targs[i])
    }
    let r = newRewriter(m, subst, b.equations, None)
    var ty = rTy(r, b.ty)
    var value = b.value
    match dicts {
        None -> {}
        Some(ds) -> {
            -- The evidence, supplied. The lambda goes away and its parameters
            -- are renamed to the dictionaries they were handed -- a rename and
            -- not a `let`, because the argument is a top-level name, so
            -- substituting it is capture-free and leaves what the body projects
            -- out of something devirtualization can see through.
            let (params, body, _) = match value.kind {
                CLam(ps, bd, n) -> (ps, bd, n)
                _ -> oops("a collapsed binding is not a lambda", b.span)
            }
            for var i = 0; i < len(params) && i < len(ds); i = i + 1 {
                Map.put(r.rename, params[i].name, ds[i])
            }
            value = body
            ty = rTy(r, body.ty)
        }
    }
    CBind {
        name = name, ty = ty, binders = Array.new(0), value = rewrite(r, value),
        span = b.span, mutable = b.mutable, module_ = b.module_,
        equations = r.equations,
    }
}

fun oops(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("internal error", message), "")
    Some(s) -> Diag.fail(Diag.at("internal error", message, s), s.file)
}

fun discard(x : a) -> Unit = {}

-- -- devirtualization ---------------------------------------------------------------

-- A method selected from a *known* dictionary becomes a name.
--
-- After specialization a method call is a projection out of a record this pass
-- can see the definition of, and then an indirect call. Both halves are
-- avoidable: each field of a ground dictionary is hoisted into its own top-level
-- binding and the projection becomes a variable naming it.
--
-- Coherence is again what makes it sound. A ground dictionary is one record,
-- built once, and no other value can ever be that class's dictionary at that
-- type, so the field's value is decided at compile time and a name for it is a
-- name for what the projection would have found.
--
-- Three things fall out rather than being arranged: a `for` loop's `iter` and
-- `next` are ordinary terms, so every loop stops projecting; a method's own
-- quantification stops being a special case, since a type application over a
-- projection becomes one over a name; and a superclass field is already a
-- top-level dictionary's name, so it is followed rather than hoisted.
--
-- The record itself stays. A dictionary *parameter* is still projected from, and
-- a capped call site still passes one; what removes the records nothing reads is
-- reachability, which is a separate question.
type Devirt = Devirt {
    program : CProgram,
    classes : ClassTable,
    state   : State,
    -- Ground dictionaries only: a record whose contents are known. An instance
    -- with a context is a lambda and has no fields to hoist.
    records : Map String Core,
    used    : Set.Set String,
    -- (dictionary, field) -> the name that projection is now. Shared across
    -- rounds, so a projection this round is the first to see still resolves to
    -- the binding a previous round hoisted.
    target  : Map String String,
    hoisted : Array CBind,
}

fun newDevirt(program : CProgram, classes : ClassTable, state : State) -> Devirt =
    Devirt {
        program = program, classes = classes, state = state,
        records = Map.new(), used = Set.new(), target = state.hoists,
        hoisted = Array.new(8),
    }

fun hoistKey(owner : String, field : String) -> String = owner + "#" + field

fun runDevirt(d : Devirt) -> CProgram {
    for b in Array.append(d.program.dicts, d.program.binds) {
        Set.add(d.used, b.name)
    }
    for b in d.program.dicts {
        if len(b.binders) == 0 {
            match b.value.kind {
                CRecord(_, _) -> Map.put(d.records, b.name, b.value)
                _ -> {}
            }
        }
    }
    -- Every hoist decided before any rewrite, because a method's body may
    -- project out of a dictionary whose fields have not been looked at yet --
    -- including its own.
    for b in d.program.dicts {
        match Map.get(d.records, b.name) {
            None -> {}
            Some(record) -> match record.kind {
                CRecord(_, fields) -> for (name, value) in fields {
                    -- A previous round hoisted it, and the binding it made is in
                    -- the program already. Hoisting again would make a second
                    -- copy and split the call sites between them.
                    if !Map.contains(d.target, hoistKey(b.name, name)) {
                        match hoist(d, b, name, value) {
                            Some(made) -> Map.put(d.target,
                                                  hoistKey(b.name, name), made)
                            None -> {}
                        }
                    }
                }
                _ -> {}
            }
        }
    }

    let out = CProgram { dicts = Array.new(16), binds = Array.new(32) }
    for b in d.program.dicts { Array.push(out.dicts, devirtBind(d, b)) }
    -- The hoisted bindings live with the dictionaries, not after them: everything
    -- in `dicts` is defined before any record is filled, and a record's field may
    -- now name a hoisted binding. A hoisted binding is always a lambda, so
    -- defining it builds a closure and cannot read a record that is still empty.
    for b in d.hoisted { Array.push(out.dicts, devirtBind(d, b)) }
    for b in d.program.binds { Array.push(out.binds, devirtBind(d, b)) }
    out
}

fun devirtBind(d : Devirt, b : CBind) -> CBind = CBind {
    name = b.name, ty = b.ty, binders = b.binders, value = devirt(d, b.value),
    span = b.span, mutable = b.mutable, module_ = b.module_,
    equations = b.equations,
}

-- The name a field now has, or `None` to leave it a projection.
fun hoist(d : Devirt, owner : CBind, name : String,
          value : Core) -> Option String {
    match value.kind {
        CVar(n) -> if Map.contains(d.records, n) { return Some(n) }
        _ -> {}
    }
    let (binders, inner) = abstraction(d, owner, name, value)
    match inner.kind {
        CLam(_, _, _) -> {}
        -- A field whose value is neither a lambda nor another dictionary's name
        -- stays where it is. Hoisting it would move an evaluation to where the
        -- dictionaries are built, and what an evaluation there can observe is
        -- exactly what the two-pass build exists to be careful about.
        _ -> return None
    }
    var made = owner.name + "#" + name
    while Set.contains(d.used, made) { made = made + "~" }
    Set.add(d.used, made)
    derive(d.state, made, owner.name)
    Array.push(d.hoisted, CBind {
        name = made, ty = inner.ty, binders = binders, value = inner,
        span = owner.span, mutable = False, module_ = owner.module_,
        equations = Array.new(0),
    })
    Some(made)
}

-- A field's own type abstraction, as binders and a body.
--
-- A method that quantifies over more than its class variable is not always a
-- type abstraction in the term. When the method has a context of its own the
-- lowering emits the dictionary lambda and leaves the quantification implicit,
-- because the checker reads a projection's binders off the *class table* rather
-- than off the term. So this reads them the same way, and takes the term's only
-- when the term states them.
--
-- Getting this from the term alone is what a first attempt did, and the checker
-- caught it immediately: a method was hoisted with no binders and the use site
-- still had one type argument.
fun abstraction(d : Devirt, owner : CBind, name : String,
                value : Core) -> (Array VarCell, Core) {
    match value.kind {
        CTyLam(binders, body) -> return (binders, body)
        _ -> {}
    }
    let cls = match dictClass(owner.ty) {
        Some(c) -> c
        None -> return (Array.new(0), value)
    }
    let info = match Map.get(d.classes.classes, cls) {
        Some(i) -> i
        None -> return (Array.new(0), value)
    }
    let m = match Classes.memberNamed(Map.keys(info.methods), name) {
        Some(resolved) -> match Map.get(info.methods, resolved) {
            Some(x) -> x
            None -> return (Array.new(0), value)
        }
        None -> return (Array.new(0), value)
    }
    let binders = Array.new(4)
    for q in m.scheme.quantified {
        if q.id != m.classVar.id { Array.push(binders, q) }
    }
    (binders, value)
}

fun devirt(d : Devirt, e : Core) -> Core {
    match e.kind {
        CField(_, _) -> match projected(d, e) {
            Some(found) -> return mkCore(e.ty, e.span, CVar(found))
            None -> {}
        }
        _ -> {}
    }
    mkCore(e.ty, e.span, mapKind(e.kind, Mapper {
        expr = fun(x) = devirt(d, x),
        type_ = fun(t) = t,
        bind = fun(b) = devirtBind(d, b),
    }))
}

fun projected(d : Devirt, e : Core) -> Option String = match e.kind {
    CField(target, name) -> match dictOwner(d, target) {
        Some(owner) -> Map.get(d.target, hoistKey(owner, name))
        None -> None
    }
    _ -> None
}

-- The ground dictionary this expression names, if it names one.
--
-- Recursive, so a superclass chain resolves in one go: the target of a method
-- projection may itself be a superclass projection this already knows the
-- answer to.
fun dictOwner(d : Devirt, e : Core) -> Option String = match e.kind {
    CVar(name) -> if Map.contains(d.records, name) { Some(name) } else { None }
    CField(_, _) -> match projected(d, e) {
        Some(found) -> if Map.contains(d.records, found) { Some(found) } else { None }
        None -> None
    }
    _ -> None
}

-- -- reachability -------------------------------------------------------------------

-- Whether not evaluating this binding is unobservable.
--
-- The test is *effects*, not use, because a top-level binding is evaluated for
-- its own sake: the program walks its bindings in order before it calls `main`,
-- so a binding whose right-hand side prints has printed whether or not anything
-- reads it. Three kinds are safe, each for a stated reason: a dictionary, which
-- is a record or a lambda; a binding with binders, which generalized, and under
-- the value restriction a generalized right-hand side is a syntactic value; and
-- any other binding whose value is literally a lambda.
fun droppable(b : CBind, isDict : Bool) -> Bool {
    if isDict || len(b.binders) > 0 { return True }
    match b.value.kind {
        CLam(_, _, _) -> True
        CTyLam(_, _) -> True
        _ -> False
    }
}

-- Drop the bindings nothing reaches, where dropping one is unobservable.
--
-- This is what makes specialization a saving rather than an addition. A program
-- that used a class at two types carried the generic binding, the two copies, and
-- every dictionary the Prelude declares; after this it carries the two copies and
-- the dictionaries they name.
--
-- The guard the cap needs is not written here because it does not need to be: a
-- capped call site still holds a type application of the generic binding, so the
-- generic binding is named by whatever reaches that call site, and being named is
-- all this asks for.
--
-- Run once, at the end, and not between rounds: an intermediate round's output is
-- full of bindings nothing reaches *yet*, and dropping one would be answering a
-- question the next round was going to ask.
fun reachable(program : CProgram, main : String) -> CProgram {
    let byname = Map.new()
    for b in Array.append(program.dicts, program.binds) {
        Map.put(byname, b.name, b)
    }
    let drop = Set.new()
    for b in program.dicts { Set.add(drop, b.name) }
    for b in program.binds {
        if droppable(b, False) { Set.add(drop, b.name) }
    }
    let work = Array.new(16)
    for (name, _) in Map.entries(byname) {
        if !Set.contains(drop, name) { Array.push(work, name) }
    }
    Array.push(work, main)
    let reach = Set.new()
    for n in work { Set.add(reach, n) }
    while len(work) > 0 {
        let name = match Array.pop(work) {
            Some(n) -> n
            None -> ""
        }
        match Map.get(byname, name) {
            None -> {}
            Some(b) -> for n in Set.elements(namesOf(b.value)) {
                if !Set.contains(reach, n) {
                    Set.add(reach, n)
                    Array.push(work, n)
                }
            }
        }
    }
    let out = CProgram { dicts = Array.new(16), binds = Array.new(32) }
    for b in program.dicts {
        if Set.contains(reach, b.name) { Array.push(out.dicts, b) }
    }
    for b in program.binds {
        if Set.contains(reach, b.name) { Array.push(out.binds, b) }
    }
    out
}

-- Specialize, devirtualize, again, and then drop what nothing reaches.
--
-- The rounds share one state, which is what keeps the cap a cap. Reachability
-- runs once, at the end, over the finished program.
fun monomorphize(program : CProgram, decls : DeclTable, classes : ClassTable,
                 main : String) -> CProgram {
    let state = newState()
    var out = program
    for var i = 0; i < rounds; i = i + 1 {
        let specialized = runMono(newMono(out, decls, classes, state))
        out = runDevirt(newDevirt(specialized, classes, state))
    }
    reachable(out, main)
}
