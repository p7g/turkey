module Turkey.Opt (
    inlineLimit, joinSpecializeLimit, speculativeInlineLimit,
    loopBreakers, reduceProgram,
)

-- The local reductions, from inlining through loop-result fusion
-- (`plan.txt` item 7, `turkey/opt.py`).
--
-- None of them knows what a monad is, which is the whole reason item 4's
-- `?`-aware fast lowering was deleted in favour of this: a `bind` chain is
-- reached the way any other saturated call to a known small function is
-- reached.
--
-- Within either side of join discovery they are one traversal rather than a
-- pass per rule, because they feed each other and it would be dishonest to
-- pretend otherwise. Inlining `bind` puts a lambda in the function position of
-- an application, which is beta; beta binds a parameter to a constructor
-- application, which is case-of-known-constructor; and that selects a branch,
-- which exposes the next call. Splitting those rules into separate passes over
-- the whole program would mean running the sequence to a fixed point from
-- outside, which is the same computation with more traversals and a less
-- obvious bound.
--
-- ## Termination is structural, not budgeted
--
-- Item 6's cap is a budget: monomorphization stops because it has spent enough,
-- not because it has finished. This pass does not work that way -- inlining
-- terminates when the call graph it walks is acyclic, and the standard way to
-- make it acyclic is to choose a **loop breaker** in each cycle and never
-- inline that one. The choice within a component is arbitrary but stable: the
-- lexicographically first, which is the first element because `Turkey.Deps`
-- sorts each component. Arbitrary because no criterion here is better than
-- another; stable because a `.opt` golden would otherwise move whenever a name
-- changed.
--
-- ## Arguments are bound, not substituted -- unless they are values
--
-- The evaluator is strict and left to right, so substituting an argument
-- expression into a body that mentions its parameter twice would evaluate it
-- twice, and into one that mentions it never would evaluate it never. So an
-- argument becomes a `CLet`, which is exactly a call-by-value binding. The
-- exception is an argument that is already a **value**: duplicating one costs
-- nothing and evaluates nothing, and substituting it is what lets the next
-- reduction see through it. Substitution is capture-avoiding by refusing rather
-- than by renaming, which needs no fresh-name supply and keeps the output
-- readable against its input.
--
-- ## What replaces Python's reflection
--
-- Python writes six generic walks over the dataclass fields. There are none
-- here, so the shape of a node is read in one place -- `kids` -- and every walk
-- that only needs "the subterms" is written in terms of it. The walks that need
-- more than that (binders, free names, tail positions) say what more they need
-- beside their own recursion, which is where they differ from each other
-- anyway.

import Data.Set as Set
import Turkey.Ast
import Turkey.Ast as Ast
import Turkey.Core
import Turkey.Decls (substitute)
import Turkey.Deps as Deps
import Turkey.Diag as Diag
import Turkey.Source (Span)
import Turkey.Types as Ty
import Turkey.Types (VarCell(..), TBottom)

-- How large a body may be and still be inlined, counted as `sizeOf_` counts.
-- Small enough that a call site does not become unreadable, large enough for
-- the things this exists to reach: a method's body, an instance's `bind`.
let inlineLimit = 32

-- A shared join is copied only when specializing a known constructor makes the
-- copy small. Deliberately the same scale as ordinary inlining: both trade a
-- bounded amount of code for exposing a local reduction, and neither the
-- traffic through a join nor the number of callers gets to raise that bound.
let joinSpecializeLimit = inlineLimit

-- A larger body may be *examined* at a call site, but is admitted only if
-- applying the known arguments and reducing it brings the residual back under
-- `inlineLimit`. This bounds the work spent asking the question as well as the
-- code ultimately copied. It is deliberately not another emission limit.
let speculativeInlineLimit = inlineLimit * 4

-- What a call may cost when its result is scrutinised right there. The
-- ordinary limit asks only how much code a call site grows by, which is the
-- wrong question when the caller is about to take the constructor apart:
-- inlining `Iterator.next` puts a `Some` next to the `match` that reads it,
-- `caseOfCase` pushes the match through the join the callee's early `return`
-- left behind, and `knownConstructor` then deletes both the allocation and the
-- branch. GHC gives the same discount by name -- a case on a call's result
-- raises the inliner's budget, so `Option` never reaches the heap -- and LLVM's
-- inliner does it too, crediting a call whose result feeds a branch. The whole
-- cost of a `for` loop's `Some` per element rides on this.
let scrutineeInlineLimit = inlineLimit * 2

-- What a callee that always panics may carry into its caller. A bare
-- forwarding call -- `fun error(message) = Prim.error(message)` -- is worth
-- copying, because it costs the caller nothing and keeps the standard library
-- out of the panic trace. Anything larger is the failure path charging the
-- success path, which is what this exists to stop. On `sizeOf_`'s scale that
-- forwarder is the single call and nothing else, since its argument is a
-- variable and variables are free.
let bottomInlineLimit = 1

-- -- the shape of a node, once -------------------------------------------------------

-- Every immediate Core subterm, in field order.
fun kids(k : CoreKind) -> Array Core = match k {
    CLit(_) -> Array.new(0)
    CUnit -> Array.new(0)
    CVar(_) -> Array.new(0)
    CCon(_) -> Array.new(0)
    CPrim(_) -> Array.new(0)
    CTuple(elems) -> elems
    CArray(elems) -> elems
    CRecord(_, fields) -> Array.map(fields, fun(f) = {
        let (_, value) = f
        value
    })
    CField(target, _) -> [target]
    CProject(target, _) -> [target]
    CIndex(target, index) -> [target, index]
    CLam(_, body, _) -> [body]
    CApp(fn, args) -> {
        let out = [fn]
        for a in args { Array.push(out, a) }
        out
    }
    CTyLam(_, body) -> [body]
    CTyApp(fn, _) -> [fn]
    CLet(_, _, value, body, _) -> [value, body]
    CLetRec(binds, body) -> {
        let out = Array.map(binds, fun(b) = b.value)
        Array.push(out, body)
        out
    }
    CJoin(_, _, body, rest, _) -> [body, rest]
    CJump(_, args) -> args
    CRef(value) -> [value]
    CDeref(target) -> [target]
    CAssign(target, value) -> [target, value]
    CIf(cond, then, otherwise) -> {
        let out = [cond, then]
        match otherwise {
            Some(o) -> Array.push(out, o)
            None -> {}
        }
        out
    }
    CMatch(scrutinee, alts) -> {
        let out = [scrutinee]
        for a in alts { Array.push(out, a.body) }
        out
    }
}

fun withValue(b : CBind, value : Core) -> CBind = CBind {
    name = b.name, ty = b.ty, binders = b.binders, value = value,
    span = b.span, mutable = b.mutable, module_ = b.module_,
    equations = b.equations, layouts = b.layouts,
}

-- -- the call graph, and the cycles in it ---------------------------------------------

-- One binding per cycle, never to be inlined.
--
-- Deterministic on purpose: `Turkey.Deps.sccs` sorts each component, so taking
-- the first is the lexicographically first name, and a golden that depends on
-- which binding was broken does not move when an unrelated one is added.
-- Which top-level bindings each one mentions, and nothing else.
fun callGraph(binds : Array CBind) -> Map String (Set.Set String) {
    let known = Set.new()
    for b in binds { Set.add(known, b.name) }
    let graph = Map.new()
    for b in binds {
        let deps = Set.new()
        for n in Set.elements(namesOf(b.value)) {
            if Set.contains(known, n) { Set.add(deps, n) }
        }
        Map.put(graph, b.name, deps)
    }
    graph
}

fun loopBreakers(program : CProgram) -> Set.Set String {
    let graph = callGraph(Array.append(program.dicts, program.binds))
    let out = Set.new()
    for component in Deps.sccs(graph) {
        let first = component[0]
        let successors = match Map.get(graph, first) {
            Some(s) -> s
            None -> Set.new()
        }
        if len(component) > 1 || Set.contains(successors, first) {
            Set.add(out, first)
        }
    }
    out
}

-- -- the traversal --------------------------------------------------------------------

type Reducer = Reducer {
    breakers : Set.Set String,
    -- Only an immutable, lambda-valued binding can be inlined. A mutable one is
    -- a cell, and reading it twice is not reading it once. Type binders are
    -- fine: a call through `CTyApp` instantiates the lambda in place, which is
    -- type-level beta reduction rather than another top-level specialization.
    bindings : Map String CBind,
    -- What is currently being inlined. Belt and braces beside the loop
    -- breakers: this makes non-termination impossible in the code rather than
    -- merely absent from the graph.
    active : Array String,
    -- The size and the transfer question of each reduced body, beside the body
    -- itself: both are questions about the callee, so both are asked once per
    -- binding rather than once per call site.
    sizes : Map String Int,
    moves : Map String Bool,
    ends  : Map String Bool,
    -- `size` of each body *as written*, which is what the speculative ceiling
    -- is checked against before the body is reduced at all.
    wrote : Map String Int,
    -- A speculative large inline may reduce ordinary small calls inside its
    -- residual, but does not recursively speculate about another large call.
    speculating : Bool,
    -- A binding's body, reduced, computed once. Without it a chain `f` calls
    -- `g` calls `h` re-reduces `h` once per path that reaches it, which is
    -- exponential in the depth of the call graph.
    reduced : Map String Core,
    -- For the join points case-of-case invents. Per program and in traversal
    -- order, so it is deterministic and a golden can hold it.
    counter : Int,
    usedNames : Set.Set String,
}

fun newReducer(program : CProgram) -> Reducer {
    let all = Array.append(program.dicts, program.binds)
    let bindings = Map.new()
    for b in all {
        if !b.mutable {
            match b.value.kind {
                CLam(_, _, _) -> Map.put(bindings, b.name, b)
                _ -> {}
            }
        }
    }
    let used = Set.new()
    for b in all {
        Set.add(used, b.name)
        for n in Set.elements(namesOf(b.value)) { Set.add(used, n) }
        for n in Set.elements(bindersOf(b.value)) { Set.add(used, n) }
    }
    Reducer {
        breakers = loopBreakers(program), bindings = bindings,
        active = Array.new(8), sizes = Map.new(), moves = Map.new(),
        ends = Map.new(), wrote = Map.new(),
        speculating = False, reduced = Map.new(), counter = 0,
        usedNames = used,
    }
}

-- Every binding's body, reduced -- callees before their callers.
--
-- The order is not cosmetic. `bodyOf` reduces a callee the first time some call
-- site asks for it, and if that call site is itself inside a body being
-- reduced, the callee's traversal runs *nested inside* the caller's. On a
-- program with a deep call graph that makes the traversal's stack depth the
-- product of call-graph depth and term depth, which is what made this pass
-- unable to compile the bootstrap compiler at all.
--
-- Reducing in dependency order -- `Deps.sccs` already emits it -- means the
-- memo is warm before anyone asks, so `bodyOf` is a lookup and every traversal
-- is one term deep. Within a cycle there is no such order and the nesting can
-- still happen, but it is then bounded by the size of the component rather than
-- by the height of the whole graph.
fun reduceProgram(program : CProgram) -> CProgram {
    let r = newReducer(program)
    let binds = Array.append(program.dicts, program.binds)
    let first = Map.new()
    for var i = 0; i < len(binds); i = i + 1 {
        if !Map.contains(first, binds[i].name) {
            Map.put(first, binds[i].name, i)
        }
    }
    let done = Map.new()
    for component in Deps.sccs(callGraph(binds)) {
        for name in component {
            match Map.get(first, name) {
                Some(index) -> {
                    let value = expr(r, binds[index].value)
                    Map.put(done, index, value)
                    memoize(r, name, value)
                }
                None -> {}
            }
        }
    }
    -- A name bound twice has one entry in the graph, so its later bindings are
    -- in no component. Nothing else can be missing.
    for var i = 0; i < len(binds); i = i + 1 {
        if !Map.contains(done, i) { Map.put(done, i, expr(r, binds[i].value)) }
    }
    let split = len(program.dicts)
    let dicts = Array.new(split)
    for var i = 0; i < split; i = i + 1 {
        Array.push(dicts, withValue(program.dicts[i], Map.getOr(done, i,
            program.dicts[i].value)))
    }
    let rest = Array.new(len(program.binds))
    for var i = 0; i < len(program.binds); i = i + 1 {
        Array.push(rest, withValue(program.binds[i], Map.getOr(done, split + i,
            program.binds[i].value)))
    }
    CProgram { dicts = dicts, binds = rest }
}

-- Record a top-level binding's reduced body for `bodyOf`.
--
-- Only the monomorphic ones: a binding with type binders is reduced again per
-- instantiation, under a key that names the type arguments, and the
-- uninstantiated body is not an answer to any of those.
fun memoize(r : Reducer, name : String, value : Core) -> Unit {
    let bind = match Map.get(r.bindings, name) {
        Some(b) -> b
        None -> return
    }
    if len(bind.binders) > 0 { return }
    match value.kind {
        CLam(_, body, _) ->
            if !Map.contains(r.reduced, name + "|") {
                Map.put(r.reduced, name + "|", body)
            }
        _ -> {}
    }
}

-- `e` with its subterms reduced, then reduced itself to a fixed point.
--
-- Bottom-up, so each rewrite sees children that are already as small as they
-- are going to get -- which is what lets one traversal do the work of running
-- three passes to a fixed point.
fun expr(r : Reducer, e : Core) -> Core {
    var out = children(r, e)
    var settled = False
    while !settled {
        match step(r, out) {
            None -> { settled = True }
            -- And walk it again. A reduction does not only rewrite the node it
            -- fires on: substituting a continuation for a parameter puts a
            -- lambda where `bind`'s body had `f(x)`, and that beta redex is in
            -- a subterm this traversal has already been past.
            Some(made) -> { out = children(r, made) }
        }
    }
    out
}

fun children(r : Reducer, e : Core) -> Core =
    mkCore(e.ty, e.span, mapKind(e.kind, Mapper {
        expr = fun(x) = expr(r, x),
        type_ = fun(t) = t,
        bind = fun(b) = withValue(b, expr(r, b.value)),
    }))

-- One reduction at the root of `e`, or `None` if none applies.
--
-- Whatever it returns has reduced subterms: inlining and join specialization
-- reduce the bodies they bring in, and the remaining rules only rearrange terms
-- that came from here.
fun step(r : Reducer, e : Core) -> Option Core {
    match e.kind {
        CApp(fn, _) -> return match fn.kind {
            CLam(_, _, _) -> beta(r, e)
            _ -> inline(r, e, inlineLimit)
        }
        CMatch(_, _) -> {
            match knownConstructor(r, e) {
                Some(m) -> return Some(m)
                None -> {}
            }
            match inlineScrutinee(r, e) {
                Some(m) -> return Some(m)
                None -> {}
            }
            match floatLet(e) {
                Some(m) -> return Some(m)
                None -> {}
            }
            match fuseRecursiveJoinResult(r, e) {
                Some(m) -> return Some(m)
                None -> {}
            }
            match caseOfCase(r, e) {
                Some(m) -> return Some(m)
                None -> {}
            }
            return caseOfJoin(e)
        }
        CLet(_, _, _, _, _) -> {
            match deadLet(e) {
                Some(m) -> return Some(m)
                None -> {}
            }
            match trivialLet(e) {
                Some(m) -> return Some(m)
                None -> {}
            }
            match letToMatch(e) {
                Some(m) -> return Some(m)
                None -> {}
            }
            return floatLetThroughJoin(e)
        }
        CJoin(_, _, _, _, _) -> return specializeJoin(r, e)
        _ -> {}
    }
    None
}

fun fresh(r : Reducer, hint : String) -> String {
    var name = ""
    var settled = False
    while !settled {
        r.counter = r.counter + 1
        name = "%" + hint + Int.toString(r.counter)
        if !Set.contains(r.usedNames, name) { settled = True }
    }
    Set.add(r.usedNames, name)
    name
}

-- -- the reductions --------------------------------------------------------------------

-- A call under a `match`, inlined on the larger budget.
--
-- `expr` works bottom-up, so by the time this `match` is reduced its scrutinee
-- has already been offered to `inline` and turned down on size. Being
-- scrutinised is the information that offer did not have.
fun inlineScrutinee(r : Reducer, e : Core) -> Option Core = match e.kind {
    CMatch(scrutinee, alts) -> match scrutinee.kind {
        CApp(_, _) -> match inline(r, scrutinee, scrutineeInlineLimit) {
            Some(made) -> Some(mkCore(e.ty, e.span, CMatch(made, alts)))
            None -> None
        }
        _ -> None
    }
    _ -> None
}

-- A saturated call to a small, known, non-loop-breaking binding.
fun inline(r : Reducer, e : Core, limit : Int) -> Option Core = match e.kind {
    CApp(fn, args) -> inlineApp(r, e, fn, args, limit)
    _ -> None
}

fun inlineApp(r : Reducer, e : Core, fn : Core, args : Array Core,
              limit : Int) -> Option Core {
    var name = ""
    var targs = None
    match fn.kind {
        CVar(n) -> { name = n }
        CTyApp(inner, ts) -> match inner.kind {
            CVar(n) -> {
                name = n
                targs = Some(ts)
            }
            _ -> return None
        }
        _ -> return None
    }
    if Set.contains(r.breakers, name) || Array.contains(r.active, name) {
        return None
    }
    let bind = match Map.get(r.bindings, name) {
        Some(b) -> b
        None -> return None
    }
    var lam = bind.value
    var key = name + "|"
    match targs {
        None -> if len(bind.binders) > 0 { return None }
        Some(ts) -> {
            if len(bind.binders) == 0 || len(bind.binders) != len(ts) {
                return None
            }
            let mapping = Map.new()
            for var i = 0; i < len(ts); i = i + 1 {
                Map.put(mapping, bind.binders[i].id, ts[i])
            }
            lam = instantiateTypes(bind.value, mapping)
            key = name + "|" + String.join(Array.map(ts, Ty.typeKey), "|")
        }
    }
    let params = match lam.kind {
        CLam(ps, _, _) -> ps
        _ -> oops("an inlinable binding whose value is not a lambda")
    }
    if len(params) != len(args) { return None }
    -- The ceiling, asked of the body *as written*, before anything reduces it.
    -- Asking it only of the reduced body -- which is what this did -- means the
    -- reduction happens first and the answer is discarded, so a callee too
    -- large to inline is fully optimized at every call site that names it,
    -- nested inside that call site's own traversal.
    --
    -- `Turkey.Desugar#thread` is fifty nodes and sits in a twenty-one binding
    -- cycle with `#expr`, the desugarer's whole expression walker. Reducing
    -- `thread` reduced `expr` inside it, and then the stack. Nothing was gained
    -- -- `expr` is refused on size the moment the question is finally asked.
    --
    -- It is also the size question GHC asks: its unfolding guidance is computed
    -- from the term as written.
    if written(r, name) > speculativeInlineLimit { return None }
    -- A monomorphic callee is reduced in its own turn, and `reduceProgram`
    -- takes those turns in dependency order, so by the time a caller is reached
    -- every callee it can name is already in the memo. A cold one is therefore
    -- a binding in the caller's own cycle whose turn has not come -- and
    -- reducing it *here*, nested inside this traversal, is what the dependency
    -- order exists to avoid.
    --
    -- Declining costs the inlining of a cycle's members into each other before
    -- their turn, and keeps it after: the components come out of `Deps.sccs`
    -- sorted, so a member is inlined into every later member and into none of
    -- the earlier ones. That is the same arbitrary-but-stable choice the loop
    -- breaker already makes, and for the same reason.
    --
    -- Type instantiation is exempt: `reduceProgram` warms no key that names
    -- type arguments, so a cold one there means "not asked for yet" rather than
    -- "not its turn", and that nesting is bounded anyway.
    match targs {
        None -> if !Map.contains(r.reduced, key) { return None }
        Some(_) -> {}
    }
    -- The *reduced* body drives the ordinary limit, not the written one. A
    -- small source body that expands to five hundred nodes is five hundred
    -- nodes at every call site.
    let body = bodyOf(r, name, key, lam)
    -- Both questions are about the *callee's* body, which `bodyOf` has already
    -- computed once and shares with every call site. Asking them here, before
    -- anything is copied, is what keeps a body that can never be inlined from
    -- being copied and substituted at every site that mentions it -- which was,
    -- measurably, most of the work this pass did on a program the size of the
    -- bootstrap compiler.
    if movesOf(r, key, body) { return None }
    let size = sizeOf(r, key, body)
    if size > speculativeInlineLimit || (r.speculating && size > limit) {
        return None
    }
    -- A callee that always panics has no useful residual -- there is nothing
    -- for the caller to reduce against -- so copying in anything it carries
    -- lets the *failure* path decide what the success path costs.
    -- `Data.Array#outOfBounds` is the case that matters: every string in its
    -- message is allocated, so inlining the message put thirteen live pointers
    -- across allocations into a function that is otherwise two comparisons, and
    -- every in-range access paid for a root frame it could not use. Rust marks
    -- these paths `#[cold] #[inline(never)]` and GHC keeps bottoming
    -- expressions off the hot path with `exprIsBottom`.
    --
    -- Only when it carries something, though. `Std.Classes#error` is
    -- `Prim.error(message)` and nothing else, so inlining it costs the caller
    -- nothing -- and *not* inlining it would put a frame in every panic trace
    -- pointing at the standard library rather than at the code that failed.
    if endsOf(r, key, body) && size > bottomInlineLimit { return None }
    -- A copied callee body becomes part of this call site. Keep spans on
    -- caller-supplied arguments, but make every node copied from the callee
    -- blame the invocation if a later Core check rejects the residual.
    let made = match applyParams(params, args, rebaseSpans(body, e.span), e.ty) {
        Some(m) -> m
        None -> return None
    }
    if size <= limit { return Some(made) }
    -- Restored to what it was, not to `False`. Speculation is meant not to
    -- nest -- the flag is the whole mechanism for that -- and clearing it on
    -- the way out of an inner speculation re-armed the outer one, so a chain of
    -- large calls could speculate at every level after all. That is unbounded
    -- rather than merely expensive: the traversal's depth grew with whatever
    -- stack it was given.
    let was = r.speculating
    r.speculating = True
    let residual = expr(r, made)
    r.speculating = was
    if sizeOf_(residual) <= limit { Some(residual) } else { None }
}

-- `size` of a reduced body, once per binding rather than per site.
-- `size` of a binding's body as written, once per binding.
--
-- Type instantiation copies a body without changing its shape, so this is asked
-- of the name rather than of the key: every specialization of `name` has the
-- same written size.
fun written(r : Reducer, name : String) -> Int = match Map.get(r.wrote, name) {
    Some(found) -> found
    None -> {
        let bind = match Map.get(r.bindings, name) {
            Some(b) -> b
            None -> oops("no binding named '" + name + "'")
        }
        let body = match bind.value.kind {
            CLam(_, inner, _) -> inner
            _ -> bind.value
        }
        let found = sizeOf_(body)
        Map.put(r.wrote, name, found)
        found
    }
}

fun sizeOf(r : Reducer, key : String, body : Core) -> Int = match Map.get(r.sizes, key) {
    Some(found) -> found
    None -> {
        let made = sizeOf_(body)
        Map.put(r.sizes, key, made)
        made
    }
}

-- `transfers` of a reduced body, once per binding rather than per site. Both
-- are answers about the callee, and the callee is shared.
fun movesOf(r : Reducer, key : String, body : Core) -> Bool = match Map.get(r.moves, key) {
    Some(found) -> found
    None -> {
        let made = transfers(body, Set.new())
        Map.put(r.moves, key, made)
        made
    }
}

-- `bottoms` of a reduced body, on the same terms as the other two.
fun endsOf(r : Reducer, key : String, body : Core) -> Bool = match Map.get(r.ends, key) {
    Some(found) -> found
    None -> {
        let made = bottoms(body)
        Map.put(r.ends, key, made)
        made
    }
}

-- A binding's reduced body, computed once.
--
-- The `active` guard cannot actually fire -- every cycle in the call graph
-- contains a loop breaker and a breaker is never inlined, so a chain of
-- expansions cannot come back to a name it is already inside. It is here so
-- that termination is a property of this function rather than of that argument.
fun bodyOf(r : Reducer, name : String, key : String, lam : Core) -> Core =
    match Map.get(r.reduced, key) {
        Some(found) -> found
        None -> {
            let inner = match lam.kind {
                CLam(_, body, _) -> body
                _ -> oops("a callee whose value is not a lambda")
            }
            Array.push(r.active, name)
            let made = expr(r, inner)
            discard(Array.pop(r.active))
            Map.put(r.reduced, key, made)
            made
        }
    }

-- A lambda applied directly. What inlining a call site leaves behind when the
-- callee's own body was itself a call.
fun beta(r : Reducer, e : Core) -> Option Core = match e.kind {
    CApp(fn, args) -> match fn.kind {
        CLam(params, body, _) ->
            if len(params) != len(args) || transfers(body, Set.new()) {
                None
            } else {
                applyParams(params, args, body, e.ty)
            }
        _ -> None
    }
    _ -> None
}

-- A binding nothing reads, of a value, is not a binding.
--
-- Not swept in for tidiness: it is what the other three leave behind.
-- Devirtualization turns `d.mul(x, 10)` into `%inst.Mul.Int#mul(x, 10)` and
-- leaves the `let %d1.Mul` that fetched the dictionary standing. The value must
-- be a value -- a `let %seq = print(x)` reads nothing either, and dropping it
-- would drop the printing.
fun deadLet(e : Core) -> Option Core = match e.kind {
    CLet(name, _, value, body, binders) ->
        if !isValue(value) || len(binders) > 0 {
            None
        } else if Set.contains(namesOf(body), name) {
            None
        } else {
            Some(body)
        }
    _ -> None
}

-- `match (let x = v in b) { alts }` is `let x = v in match b { alts }`.
--
-- Not a reduction in itself: it is what lets the other rules see. Every call
-- this pass inlines becomes a `let` chain around the body, so a scrutinee that
-- is morally `Some(5)` arrives as several bindings wrapped around it, and
-- case-of-known-constructor -- which looks only at the node in front of it --
-- would stop there.
--
-- Declined when the alternatives mention the name, since moving them inside the
-- binding would rebind it out from under them.
fun floatLet(e : Core) -> Option Core = match e.kind {
    CMatch(scrutinee, alts) -> match scrutinee.kind {
        CLet(name, bound, value, body, binders) -> {
            if len(binders) > 0 { return None }
            if Set.contains(namesOfAlts(alts), name) { return None }
            let inner = mkCore(e.ty, e.span, CMatch(body, alts))
            Some(mkCore(e.ty, scrutinee.span,
                        CLet(name, bound, value, inner, Array.new(0))))
        }
        _ -> None
    }
    _ -> None
}

-- `match (join j(p) = B in R) { alts }`, with the match moved into B.
--
-- What an inlined `Iterator.next` leaves behind. The callee's early `return`
-- became a join, so the constructor the caller wants to take apart is not the
-- scrutinee itself but the argument of a `jump` inside it, and `caseOfCase` --
-- which knows how to push a match into the arms of a `match` or an `if` -- does
-- not reach it.
--
-- Moving the match into the join's *body* puts it exactly one step from those
-- jumps, and `specializeJoin` closes the gap: it copies the body once per
-- constructor signature seen at a jump, and the copy is a match on a known
-- constructor, which `knownConstructor` deletes along with the allocation. This
-- is GHC's case-of-case over join points, and the reason it is one rule there
-- and two here is that this compiler's join specialization is a separate rule
-- already.
--
-- `R` must not fall through, or its own value would escape unmatched. That is
-- not a restriction in practice: a join stands where a `return` or a `?` was,
-- and control reaches its continuation by jumping.
fun caseOfJoin(e : Core) -> Option Core = match e.kind {
    CMatch(scrutinee, alts) -> match scrutinee.kind {
        CJoin(name, params, body, rest, recursive) -> {
            if fallsThrough(rest) { return None }
            let mentioned = namesOfAlts(alts)
            for p in params {
                if Set.contains(mentioned, p.name) { return None }
            }
            Some(mkCore(e.ty, scrutinee.span, CJoin(
                name, params,
                mkCore(e.ty, e.span, CMatch(body, alts)),
                retype(rest, e.ty), recursive)))
        }
        _ -> None
    }
    _ -> None
}

-- `match (match S {...}) { alts }`, with the outer match pushed in.
--
-- The pass item 7 names as the one that actually erases `Flow`. A `?` builds a
-- `Flow` in one branch and scrutinises it downstream, and the two never meet
-- until the downstream match is moved next to the constructor -- at which point
-- case-of-known-constructor collapses both.
--
-- It is also the pass that duplicates continuations, which is why item 7
-- insists on join points *first*. `alts` goes into a `CJoin`, each branch that
-- does not reduce becomes a `jump` to it, and only the branches that genuinely
-- collapse carry a copy. Without the join, a chain of `?` over a
-- four-constructor `Flow` multiplies its continuation by four at every step.
--
-- Declined outright when no branch reduces: the join would then be pure
-- overhead, and this is not a rewrite worth doing for its own sake.
fun caseOfCase(r : Reducer, e : Core) -> Option Core = match e.kind {
    CMatch(scrutinee, alts) -> caseOfCaseOn(r, e, scrutinee, alts)
    _ -> None
}

fun caseOfCaseOn(r : Reducer, e : Core, scrutinee : Core,
                 alts : Array CAlt) -> Option Core {
    let branches = match scrutinee.kind {
        CMatch(_, inner) -> Array.map(inner, fun(a) = a.body)
        CIf(_, then, otherwise) -> match otherwise {
            Some(o) -> [then, o]
            None -> return None
        }
        _ -> return None
    }
    -- A branch holding a `return` is one whose value is not what the outer
    -- match will scrutinise. Item 7's last step is what makes this case
    -- ordinary; until then it is one to leave alone.
    if transfers(scrutinee, Set.new()) { return None }

    let name = fresh(r, "cc")
    let pushed = Array.new(len(branches))
    var landed = False
    for branch in branches {
        let made = expr(r, mkCore(e.ty, e.span, CMatch(branch, alts)))
        if mentionsAlts(made, alts) {
            Array.push(pushed, mkCore(TBottom, branch.span,
                                      CJump(name, [branch])))
        } else {
            Array.push(pushed, made)
            landed = True
        }
    }
    if !landed { return None }

    let param = CParam { name = fresh(r, "cv"), ty = scrutinee.ty }
    let body = mkCore(e.ty, e.span, CMatch(
        mkCore(scrutinee.ty, e.span, CVar(param.name)), alts))
    let rest = match scrutinee.kind {
        CMatch(inner, innerAlts) -> {
            let made = Array.new(len(innerAlts))
            for var i = 0; i < len(innerAlts); i = i + 1 {
                Array.push(made, CAlt { pat = innerAlts[i].pat, body = pushed[i] })
            }
            mkCore(e.ty, scrutinee.span, CMatch(inner, made))
        }
        CIf(cond, _, _) ->
            mkCore(e.ty, scrutinee.span, CIf(cond, pushed[0], Some(pushed[1])))
        _ -> return None
    }
    -- Every branch reduced, so nothing jumps and the join is dead.
    if !jumpsTo(rest, name) { return Some(rest) }
    Some(mkCore(e.ty, e.span, CJoin(name, [param], body, rest, False)))
}

-- Push a recursive join's result consumer through its boundary.
--
-- A lifted loop commonly has this shape:
--
--     match (join rec loop() : Option (Flow ...) = body in jump loop()) { exits }
--
-- The loop breaker correctly prevents ordinary inlining from unfolding `loop`,
-- so the produced `Flow` cannot meet `exits` unless the consumer moves into the
-- loop. Change the join's answer type to the match's answer type and consume
-- both its body and its entry there. Self-jumps stay self-jumps, so recursion
-- is neither copied nor turned into host recursion; only the loop's actual
-- exits are rewritten.
--
-- A general worker/result-wrapper rule: it knows neither `Flow` nor `Option`
-- and is useful whenever a recursive join returns a value that is immediately
-- scrutinised. Moving the alternatives under the join parameters could capture
-- a same-named free variable; that rare shape is declined rather than renamed,
-- consistently with the other capture-avoiding reductions here.
fun fuseRecursiveJoinResult(r : Reducer, e : Core) -> Option Core = match e.kind {
    CMatch(scrutinee, alts) -> match scrutinee.kind {
        CJoin(name, params, body, rest, recursive) -> if !recursive { None } else {
            let free = freeNamesOfAlts(alts)
            let bound = bindersOf(body)
            for n in Set.elements(bindersOf(rest)) { Set.add(bound, n) }
            for p in params { Set.add(bound, p.name) }
            if overlaps(free, bound) { return None }
            Some(mkCore(e.ty, scrutinee.span, CJoin(
                name, params,
                expr(r, consumeTail(body, alts, e.ty, e.span)),
                expr(r, consumeTail(rest, alts, e.ty, e.span)),
                recursive)))
        }
        _ -> None
    }
    _ -> None
}

-- A binding of a name to a name is not a binding.
--
-- `let %k1 = x in let a = %k1 in ...` is what a `?` chain becomes once `bind`
-- is inlined and its continuation beta-reduced: three names for one value.
-- Substituting costs nothing -- reading a variable is free and may be done
-- twice or never -- and it is what puts the `if` that built the value next to
-- the `match` that scrutinises it.
--
-- Only names and literals, not every value: substituting a `CLam` would
-- duplicate its body at each mention, which is inlining and has a size limit
-- for a reason.
fun trivialLet(e : Core) -> Option Core = match e.kind {
    CLet(name, _, value, body, binders) -> {
        if len(binders) > 0 { return None }
        match value.kind {
            CVar(_) -> {}
            CLit(_) -> {}
            CUnit -> {}
            CCon(_) -> {}
            _ -> return None
        }
        let bound = bindersOf(body)
        if Set.contains(bound, name) || overlaps(freeNames(value), bound) {
            return None
        }
        let mapping = Map.new()
        Map.put(mapping, name, value)
        Some(substituteNames(body, mapping))
    }
    _ -> None
}

-- `let x = v in match x {...}` is `match v {...}`, when `x` is read only there.
--
-- The other half of putting a producer next to its consumer, and the one that
-- hands case-of-case something to work on.
--
-- Pattern variables scope over their alternative bodies, not over the
-- scrutinee. A free name in `value` therefore cannot be captured by an equally
-- named pattern when `value` becomes that scrutinee.
fun letToMatch(e : Core) -> Option Core = match e.kind {
    CLet(name, _, value, body, binders) -> {
        if len(binders) > 0 { return None }
        match body.kind {
            CMatch(scrutinee, alts) -> {
                match scrutinee.kind {
                    CVar(n) -> if n != name { return None }
                    _ -> return None
                }
                if Set.contains(freeNamesOfAlts(alts), name) { return None }
                Some(mkCore(body.ty, body.span, CMatch(value, alts)))
            }
            _ -> None
        }
    }
    _ -> None
}

-- Move a binding used only by a join's `rest` to that `rest`.
--
-- Inlined `bind` commonly leaves exactly this shape: the producer is an `if`,
-- its consumer is a `match`, and the continuation join introduced to keep
-- case-of-case from duplicating code sits between them. Moving the `let` across
-- a non-recursive join puts producer and consumer back together.
--
-- The value is not moved into the join body, and the rewrite is declined when
-- that body reads the binding. A recursive join is left alone: its rest is an
-- entry to a loop rather than merely the continuation beside this binding.
fun floatLetThroughJoin(e : Core) -> Option Core = match e.kind {
    CLet(name, bound, value, body, binders) -> match body.kind {
        CJoin(joinName, params, joinBody, joinRest, recursive) -> {
            if len(binders) > 0 || recursive { return None }
            if Set.contains(freeNames(joinBody), name) { return None }
            let rest = mkCore(body.ty, e.span,
                              CLet(name, bound, value, joinRest, binders))
            Some(mkCore(e.ty, body.span,
                        CJoin(joinName, params, joinBody, rest, recursive)))
        }
        _ -> None
    }
    _ -> None
}

-- One specialized copy of a join, per constructor signature its jumps carry.
type Variant = Variant {
    -- Which argument positions arrive as a known constructor.
    known : Array Bool,
    calls : Array Core,
    -- Filled in once the copy is built and found to be worth keeping.
    made  : Option String,
}

-- Carry known constructor tags across a non-recursive join.
--
-- A single-use join is beta-reduced when doing so is capture-safe. For a shared
-- join, calls with the same constructor signature are redirected to one
-- specialized copy. Constructor fields, and arguments whose tag is unknown,
-- become parameters of that copy; rebuilding the known constructor in its body
-- lets the ordinary match rule select an arm.
--
-- This preserves the reason the join exists: a continuation is copied once per
-- useful tag, never once per incoming edge. Copies which do not shrink after
-- reduction, or remain over `joinSpecializeLimit`, are declined.
--
-- Python keys its replacements by the *identity* of each jump. There is no
-- object identity here, and none is needed: which replacement a jump gets is
-- decided entirely by its signature, so the walk that rewrites recomputes the
-- signature and looks it up. A jump with no known constructor anywhere cannot
-- collide with a group, because every group was founded by a jump that has one.
fun specializeJoin(r : Reducer, e : Core) -> Option Core = match e.kind {
    CJoin(name, params, body, rest, recursive) ->
        if recursive { None } else { specializeOn(r, e, name, params, body, rest) }
    _ -> None
}

fun specializeOn(r : Reducer, e : Core, name : String, params : Array CParam,
                 body : Core, rest : Core) -> Option Core {
    let jumps = joinJumps(rest, name)
    if len(jumps) == 0 { return Some(rest) }

    var anyKnown = False
    for j in jumps { if hasKnownArg(j) { anyKnown = True } }
    if !anyKnown { return None }

    if len(jumps) == 1 {
        -- Moving the body to the jump site must not capture one of its free
        -- variables under a same-named binder in `rest`.
        if overlaps(freeNames(body), bindersOf(rest)) { return None }
        let args = jumpArgs(jumps[0])
        let made = match applyParams(params, args, body, e.ty) {
            Some(m) -> expr(r, m)
            None -> return None
        }
        return Some(replaceJumps(rest, name, fun(_) = Some(made)))
    }

    let groups = Map.new()
    for j in jumps {
        if hasKnownArg(j) {
            let key = signatureKey(jumpArgs(j))
            match Map.get(groups, key) {
                Some(g) -> Array.push(g.calls, j)
                None -> Map.put(groups, key, Variant {
                    known = Array.map(jumpArgs(j),
                                      fun(a) = isConstructor(a)),
                    calls = [j], made = None,
                })
            }
        }
    }

    let variants = Array.new(4)
    var replaced = 0
    for (_, group) in Map.entries(groups) {
        let first = jumpArgs(group.calls[0])
        let fresh_ = Array.new(4)
        let templates = Array.new(len(first))
        for var i = 0; i < len(first); i = i + 1 {
            let arg = first[i]
            if !group.known[i] {
                let made = fresh(r, "jv")
                Array.push(fresh_, CParam { name = made, ty = params[i].ty })
                Array.push(templates, mkCore(params[i].ty, arg.span, CVar(made)))
            } else {
                let vars = Array.new(4)
                for field in constructorArgs(arg) {
                    let made = fresh(r, "jf")
                    Array.push(fresh_, CParam { name = made, ty = field.ty })
                    Array.push(vars, mkCore(field.ty, field.span, CVar(made)))
                }
                Array.push(templates, rebuildConstructor(arg, vars))
            }
        }
        match applyParams(params, templates, body, e.ty) {
            None -> {}
            Some(built) -> {
                let made = expr(r, built)
                if sizeOf_(made) < sizeOf_(body)
                        && sizeOf_(made) <= joinSpecializeLimit {
                    let owner = fresh(r, "js")
                    group.made = Some(owner)
                    replaced = replaced + len(group.calls)
                    Array.push(variants, (owner, fresh_, made))
                }
            }
        }
    }
    if len(variants) == 0 { return None }

    var out = replaceJumps(rest, name, fun(j) = specialized(groups, j))
    if replaced != len(jumps) {
        out = mkCore(e.ty, e.span, CJoin(name, params, body, out, False))
    }
    for var i = len(variants) - 1; i >= 0; i = i - 1 {
        let (owner, ps, made) = variants[i]
        out = mkCore(e.ty, e.span, CJoin(owner, ps, made, out, False))
    }
    Some(out)
}

-- The jump a specialized copy wants in place of this one, if its signature
-- founded a copy that was kept.
fun specialized(groups : Map String Variant, j : Core) -> Option Core {
    let args = jumpArgs(j)
    if !hasKnownArg(j) { return None }
    let group = match Map.get(groups, signatureKey(args)) {
        Some(g) -> g
        None -> return None
    }
    let target = match group.made {
        Some(n) -> n
        None -> return None
    }
    let out = Array.new(len(args))
    for var i = 0; i < len(args); i = i + 1 {
        if group.known[i] {
            for field in constructorArgs(args[i]) { Array.push(out, field) }
        } else {
            Array.push(out, args[i])
        }
    }
    Some(mkCore(TBottom, j.span, CJump(target, out)))
}

-- A `match` on a constructor whose identity is right there.
--
-- Scanned in source order and stopped at the first alternative that is not a
-- *definite* mismatch, so an earlier arm that might have matched keeps this
-- from firing. A pattern that binds anything but plain variables is left alone:
-- the nested case is a real transformation and this is not the milestone for it.
--
-- The scrutinee need not be a value, but nothing it computes may be thrown
-- away. Selecting a branch discards the arms not taken -- which a `match` would
-- not have run anyway -- and it discards the constructor application itself,
-- which it *would* have. So every argument is bound, in order, including the
-- ones the pattern ignores, under a name nothing reads. `Some(launch())` still
-- launches.
fun knownConstructor(r : Reducer, e : Core) -> Option Core = match e.kind {
    CMatch(scrutinee, alts) -> match constructorName(scrutinee) {
        None -> None
        Some(con) -> selectArm(r, e, scrutinee, alts, con,
                               constructorArgs(scrutinee))
    }
    _ -> None
}

fun selectArm(r : Reducer, e : Core, scrutinee : Core, alts : Array CAlt,
              con : String, args : Array Core) -> Option Core {
    for alt in alts {
        match unannot(alt.pat).kind {
            PCon(name, subs) -> if name == con {
                let names = Array.new(len(subs))
                for sub in subs {
                    match unannot(sub).kind {
                        PVar(bound) -> Array.push(names, bound)
                        PWild -> Array.push(names, fresh(r, "seq"))
                        _ -> return None
                    }
                }
                return applyNames(names, args, alt.body, e.ty)
            }
            -- a different constructor: cannot match
            PVar(bound) -> return applyNames([bound], [scrutinee], alt.body, e.ty)
            PWild -> return applyNames([fresh(r, "seq")], [scrutinee], alt.body,
                                       e.ty)
            -- a literal, a tuple, a record: not decided here
            _ -> return None
        }
    }
    None
}

-- -- binding a call's arguments ---------------------------------------------------------

fun applyParams(params : Array CParam, args : Array Core, body : Core,
                ty : Ty.Type) -> Option Core =
    applyNames(Array.map(params, fun(p) = p.name), args, body, ty)

-- `body` with each name bound to its argument, or `None` if it cannot be.
--
-- A value argument is substituted when that cannot capture anything, because
-- seeing through it is what lets the next reduction fire. Everything else
-- becomes a `CLet`, which is call by value written down.
--
-- `None` when the bindings would capture each other. `let a = e1 in let b = e2`
-- puts `a` in scope for `e2`, and a call's arguments are all evaluated in the
-- caller's scope, so if `e2` mentions a name some earlier parameter also has,
-- the chain means something the call did not. The names in question are a
-- callee's parameters and a caller's locals, so it takes a coincidence -- but a
-- reduction that is wrong on a coincidence is wrong.
--
-- The `let`s this builds are themselves binders, and a substituted argument
-- lands *inside* them, so `bound` cannot be read off the callee's body alone.
-- `internal(name, b.name)` -- a caller's local spelled like a callee's second
-- parameter -- substitutes `name` for `owner` and then wraps `let name =
-- b.name` around it, and the body reads the field twice. So a parameter that
-- becomes a `let` disqualifies substituting any argument that mentions it, and
-- disqualifying one turns it into a `let` in turn, which is why this settles
-- rather than deciding in one pass.
fun applyNames(names : Array String, args : Array Core, body : Core,
               ty : Ty.Type) -> Option Core {
    let bound = bindersOf(body)
    let candidates = Map.new()
    let letters = Set.new()
    let earlier = Set.new()
    for var i = 0; i < len(names); i = i + 1 {
        let free = freeNames(args[i])
        if overlaps(free, earlier) { return None }
        if isValue(args[i]) && !Set.contains(bound, names[i])
                && !overlaps(free, bound) {
            Map.put(candidates, names[i], args[i])
        } else {
            Set.add(letters, names[i])
        }
        Set.add(earlier, names[i])
    }
    var settled = False
    while !settled {
        let captured = Array.new(4)
        for (name, arg) in Map.entries(candidates) {
            if overlaps(freeNames(arg), letters) { Array.push(captured, name) }
        }
        if len(captured) == 0 {
            settled = True
        } else {
            for name in captured {
                Map.delete(candidates, name)
                Set.add(letters, name)
            }
        }
    }

    var out = if len(Map.entries(candidates)) > 0 {
        substituteNames(body, candidates)
    } else {
        body
    }
    for var i = len(names) - 1; i >= 0; i = i - 1 {
        if !Map.contains(candidates, names[i]) {
            out = mkCore(ty, args[i].span,
                         CLet(names[i], args[i].ty, args[i], out, Array.new(0)))
        }
    }
    Some(out)
}

-- Apply a result match at every yielding tail of `e`.
--
-- A jump is already an exit and must remain in tail position. The other cases
-- mirror `Turkey.Core.tailFieldNames` but are written out because their result
-- type also changes from the consumed value's type to `ty`.
fun consumeTail(e : Core, alts : Array CAlt, ty : Ty.Type,
                span : Option Span) -> Core = match e.kind {
    CJump(_, _) -> e
    CIf(cond, then, otherwise) -> {
        let inner = match otherwise {
            Some(o) -> o
            None -> mkCore(e.ty, e.span, CUnit)
        }
        mkCore(ty, e.span, CIf(cond, consumeTail(then, alts, ty, span),
                               Some(consumeTail(inner, alts, ty, span))))
    }
    CLet(name, bound, value, body, binders) ->
        mkCore(ty, e.span, CLet(name, bound, value,
                                consumeTail(body, alts, ty, span), binders))
    CLetRec(binds, body) ->
        mkCore(ty, e.span, CLetRec(binds, consumeTail(body, alts, ty, span)))
    CMatch(scrutinee, inner) ->
        mkCore(ty, e.span, CMatch(scrutinee, Array.map(inner, fun(a) = CAlt {
            pat = a.pat, body = consumeTail(a.body, alts, ty, span),
        })))
    CJoin(name, params, body, rest, recursive) ->
        mkCore(ty, e.span, CJoin(name, params,
                                 consumeTail(body, alts, ty, span),
                                 consumeTail(rest, alts, ty, span), recursive))
    _ -> mkCore(ty, span, CMatch(e, alts))
}

-- -- copying a term ----------------------------------------------------------------------

-- `value` with free type variables replaced by a call's type arguments.
--
-- The erased-language equivalent of beta-reducing a `CTyApp`. Deliberately
-- happy with non-ground arguments: unlike monomorphization it makes no
-- top-level copy and cannot chase polymorphic recursion, and a phantom variable
-- such as a lifted loop's uninhabited `Brk` slot is exactly the case that
-- brought this rule here.
--
-- A binder introduces a type variable rather than using one, so `mapKind`
-- leaves those alone; the types under them may still mention an outer variable
-- and are therefore walked normally.
fun instantiateTypes(e : Core, mapping : Map Int Ty.Type) -> Core =
    mkCore(substitute(e.ty, mapping), e.span, mapKind(e.kind, Mapper {
        expr = fun(x) = instantiateTypes(x, mapping),
        type_ = fun(t) = substitute(t, mapping),
        bind = fun(b) = CBind {
            name = b.name, ty = substitute(b.ty, mapping),
            binders = b.binders, value = instantiateTypes(b.value, mapping),
            span = b.span, mutable = b.mutable, module_ = b.module_,
            equations = Array.map(b.equations, fun(pair) = {
                let (l, r) = pair
                (substitute(l, mapping), substitute(r, mapping))
            }),
            layouts = b.layouts,
        },
    }))

-- Give a copied Core subtree one diagnostic origin.
--
-- This runs before value arguments are substituted, so those argument nodes
-- retain their own caller spans when the substitution inserts them.
fun rebaseSpans(e : Core, span : Option Span) -> Core =
    mkCore(e.ty, span, mapKind(e.kind, Mapper {
        expr = fun(x) = rebaseSpans(x, span),
        type_ = fun(t) = t,
        bind = fun(b) = CBind {
            name = b.name, ty = b.ty, binders = b.binders,
            value = rebaseSpans(b.value, span), span = span,
            mutable = b.mutable, module_ = b.module_, equations = b.equations,
            layouts = b.layouts,
        },
    }))

fun substituteNames(e : Core, mapping : Map String Core) -> Core = match e.kind {
    CVar(name) -> match Map.get(mapping, name) {
        Some(found) -> found
        None -> e
    }
    _ -> mkCore(e.ty, e.span, mapKind(e.kind, Mapper {
        expr = fun(x) = substituteNames(x, mapping),
        type_ = fun(t) = t,
        bind = fun(b) = withValue(b, substituteNames(b.value, mapping)),
    }))
}

-- -- small questions about terms ------------------------------------------------------

-- Whether evaluating `e` does no work and can be done twice or never.
--
-- A `CVar` counts: variables are immutable bindings, and a `var` is a `CRef`
-- read through a `CDeref`, which is not this.
fun isValue(e : Core) -> Bool = match e.kind {
    CLit(_) -> True
    CUnit -> True
    CVar(_) -> True
    CCon(_) -> True
    CPrim(_) -> True
    CLam(_, _, _) -> True
    CTuple(elems) -> {
        for x in elems { if !isValue(x) { return False } }
        True
    }
    CApp(fn, args) -> match fn.kind {
        CCon(_) -> {
            for a in args { if !isValue(a) { return False } }
            True
        }
        _ -> False
    }
    _ -> False
}

-- Roughly the code `e` becomes, for the inliner to spend a budget of.
--
-- Counting every node as one is what this did, and it charges for a great deal
-- that is not code. `Main#inc` in the brainfuck benchmark -- one array element
-- read, incremented and written back -- measured 98 against a budget of 40 and
-- so stayed a call, while the loop that called it three times a cycle paid a
-- frame, a spill set and a panic-flag test for each. Most of those 98 nodes
-- were names: `match xs { Data.Array#Array(value) -> value }` appeared four
-- times, which is a single-constructor unpack and lowers to one load, and the
-- `let`s naming each intermediate lower to nothing at all, the backend being in
-- SSA already.
--
-- So: a variable, a literal and a nullary constructor are operands and cost
-- nothing; a type application is erased; a `let` is a name for a value, and
-- costs what it names. A match with one alternative is a projection -- the
-- backend does not even test the tag -- while one with several costs a branch
-- each. This is GHC's `sizeExpr`, which is free for variables, literals, types
-- and coercions and charges per case alternative, and it is the same reasoning
-- as the discounts already here: the budget should track what a call site
-- actually grows by.
fun sizeOf_(e : Core) -> Int = match e.kind {
    CVar(_) -> 0
    CLit(_) -> 0
    CUnit -> 0
    CCon(_) -> 0
    CTyApp(fn, _) -> sizeOf_(fn)
    CLet(_, _, value, body, _) -> sizeOf_(value) + sizeOf_(body)
    CLam(_, body, _) -> sizeOf_(body)
    CJoin(_, _, body, rest, _) -> sizeOf_(body) + sizeOf_(rest)
    CMatch(scrutinee, alts) -> {
        var total = if len(alts) <= 1 { 0 } else { len(alts) }
        total = total + sizeOf_(scrutinee)
        for a in alts { total = total + sizeOf_(a.body) }
        total
    }
    -- A `letrec` member costs what its value costs, which is what the
    -- recursion over `kids` already does: the wrapper itself is not code.
    _ -> {
        var total = 1
        for child in kids(e.kind) { total = total + sizeOf_(child) }
        total
    }
}

-- Whether evaluating `e` always diverges.
--
-- GHC's `exprIsBottom`, cut down to the one producer this compiler has:
-- `Prim.error`. Conservative in the direction of answering false, which costs
-- an inline allowed rather than a term moved wrongly.
fun bottoms(e : Core) -> Bool = match e.kind {
    CApp(fn, _) -> match stripTyApp(fn).kind {
        CPrim(name) -> name == "Prim.error"
        -- Python asks for a `name` field and takes whichever node has one, so
        -- a variable spelled this way would answer too. Nothing builds one,
        -- and saying so costs a line.
        CVar(name) -> name == "Prim.error"
        _ -> False
    }
    CLet(_, _, _, body, _) -> bottoms(body)
    CJoin(_, _, _, rest, _) -> bottoms(rest)
    CMatch(_, alts) -> {
        if len(alts) == 0 { return False }
        for a in alts { if !bottoms(a.body) { return False } }
        True
    }
    CIf(_, then, otherwise) -> match otherwise {
        Some(o) -> bottoms(then) && bottoms(o)
        None -> False
    }
    _ -> False
}

fun stripTyApp(e : Core) -> Core = match e.kind {
    CTyApp(fn, _) -> stripTyApp(fn)
    _ -> e
}

-- Whether `e` can produce a value of its own rather than jumping away.
--
-- A join's type is the type of its result, which says nothing about where that
-- result comes from: the `if` an inlined `return` leaves behind is typed
-- `Option a` while both its branches are jumps, so nothing ever reaches the
-- expression after it. `caseOfJoin` needs that distinction, and only the syntax
-- has it.
--
-- Conservative in the direction of answering true: an unrecognised term is
-- assumed to fall through, which costs a rewrite declined rather than a value
-- dropped.
fun fallsThrough(e : Core) -> Bool = match e.kind {
    CJump(_, _) -> False
    CLet(_, _, _, body, _) -> fallsThrough(body)
    CJoin(_, _, body, rest, _) -> fallsThrough(body) || fallsThrough(rest)
    CMatch(_, alts) -> {
        for a in alts { if fallsThrough(a.body) { return True } }
        False
    }
    CIf(_, then, otherwise) -> match otherwise {
        Some(o) -> fallsThrough(then) || fallsThrough(o)
        None -> True
    }
    _ -> True
}

-- `e`, with the result type of every tail position replaced.
--
-- Only ever called where `fallsThrough(e)` is false, and that is what makes it
-- sound: nothing here can produce a value, so the type each of these nodes
-- carries is a label on a result that never happens. Because `fallsThrough`
-- decides a join by *both* its body and its rest, and a match by all of its
-- arms, the recursion below reaches exactly the terms that inherited the
-- phantom type and stops at the jumps that gave it away.
fun retype(e : Core, ty : Ty.Type) -> Core = match e.kind {
    CLet(name, bound, value, body, binders) ->
        mkCore(ty, e.span, CLet(name, bound, value, retype(body, ty), binders))
    CJoin(name, params, body, rest, recursive) ->
        mkCore(ty, e.span, CJoin(name, params, retype(body, ty),
                                 retype(rest, ty), recursive))
    CMatch(scrutinee, alts) ->
        mkCore(ty, e.span, CMatch(scrutinee, Array.map(alts, fun(a) = CAlt {
            pat = a.pat, body = retype(a.body, ty),
        })))
    CIf(cond, then, otherwise) -> match otherwise {
        Some(o) -> mkCore(ty, e.span, CIf(cond, retype(then, ty),
                                          Some(retype(o, ty))))
        None -> e
    }
    _ -> e
}

-- Whether the alternatives `alts` are still being matched on inside `e`:
-- whether *this* match survived the reduction, which is exactly the question
-- "did pushing it into the branch buy anything".
--
-- Python asks it with `n.alts is alts`, and the answer is always false --
-- FINDINGS.md 42, and the reason this is a constant rather than a walk.
-- `expr` rebuilds every node it touches, the alternative list included, before
-- any rule fires; the object the caller named is gone by the time the reduction
-- being asked about has happened, and no rule reuses it. Turkey has no object
-- identity to translate that with, and it needs none, because there is nothing
-- there to translate: the question is constant.
--
-- Carrying the *patterns*' identity instead would make it fire -- a pattern is
-- the one part of an alternative that survives a rebuild, here and in
-- `Turkey.Core.mapKind` alike -- and that was tried on both sides and reverted.
-- It costs optimization rather than buying protection: a branch whose pushed-in
-- match has not collapsed *yet* becomes a jump carrying the whole unreduced
-- branch, and the join specialization downstream can no longer find a
-- constructor tag to split on. What the guard should key on is open; that this
-- spelling of it has never fired is not.
fun mentionsAlts(e : Core, alts : Array CAlt) -> Bool = False

-- Whether `e` contains a jump that leaves it.
--
-- A term is movable when everything it transfers to travels with it. A jump
-- names its target, so the question is whether the name is bound inside `e`. A
-- function whose body holds a `return` carries its own `%ret` join *inside* its
-- lambda, so the whole body is self-contained and this answers false where a
-- position-named transfer would have answered true -- which is the reduction
-- the join points were for.
--
-- A jump under a nested `CLam` cannot exist: a lambda body is out of tail
-- position and the checker gives it an empty join scope. So a lambda is not
-- descended into, and a `CJoin` extends the bound set for its `rest` always and
-- for its own `body` only when it says it is recursive, exactly as the checker
-- does.
fun transfers(e : Core, bound : Set.Set String) -> Bool = match e.kind {
    CJump(name, _) -> !Set.contains(bound, name)
    CLam(_, _, _) -> False
    CJoin(name, _, body, rest, recursive) -> {
        let inner = Set.new()
        for n in Set.elements(bound) { Set.add(inner, n) }
        Set.add(inner, name)
        transfers(body, if recursive { inner } else { bound })
            || transfers(rest, inner)
    }
    _ -> {
        for child in kids(e.kind) { if transfers(child, bound) { return True } }
        False
    }
}

-- `e` read as a saturated constructor application.
fun constructorName(e : Core) -> Option String = match e.kind {
    CCon(name) -> Some(name)
    CApp(fn, _) -> match fn.kind {
        CCon(name) -> Some(name)
        _ -> None
    }
    _ -> None
}

fun constructorArgs(e : Core) -> Array Core = match e.kind {
    CApp(fn, args) -> match fn.kind {
        CCon(_) -> args
        _ -> Array.new(0)
    }
    _ -> Array.new(0)
}

fun isConstructor(e : Core) -> Bool = match constructorName(e) {
    Some(_) -> True
    None -> False
}

-- A constructor shaped like `e`, with `args` as its fields.
fun rebuildConstructor(e : Core, args : Array Core) -> Core = match e.kind {
    CCon(_) -> e
    CApp(fn, _) -> mkCore(e.ty, e.span, CApp(fn, args))
    _ -> oops("not a constructor application")
}

fun hasKnownArg(j : Core) -> Bool {
    for a in jumpArgs(j) { if isConstructor(a) { return True } }
    False
}

fun jumpArgs(j : Core) -> Array Core = match j.kind {
    CJump(_, args) -> args
    _ -> oops("not a jump")
}

-- What groups two jumps into one specialized copy: the constructor at each
-- position, with its arity, or nothing where the tag is unknown.
fun signatureKey(args : Array Core) -> String {
    let parts = Array.map(args, fun(a) = match constructorName(a) {
        Some(con) -> con + "/" + Int.toString(len(constructorArgs(a)))
        None -> "*"
    })
    String.join(parts, ",")
}

-- The jumps in `e` bound by the surrounding join named `name`.
fun joinJumps(e : Core, name : String) -> Array Core {
    let out = Array.new(4)
    collectJumps(e, name, False, out)
    out
}

fun collectJumps(e : Core, name : String, shadowed : Bool,
                 out : Array Core) -> Unit = match e.kind {
    CJump(target, args) -> {
        if !shadowed && target == name { Array.push(out, e) }
        for a in args { collectJumps(a, name, shadowed, out) }
    }
    CJoin(bound, _, body, rest, recursive) -> {
        let same = bound == name
        collectJumps(body, name, shadowed || (same && recursive), out)
        collectJumps(rest, name, shadowed || same, out)
    }
    _ -> for child in kids(e.kind) { collectJumps(child, name, shadowed, out) }
}

-- Rewrite the jumps `joinJumps` would have found, where `chosen` has something
-- to say about one. A replaced jump's arguments are not descended into, as
-- Python's identity-keyed rewrite does not descend into a node it replaced.
fun replaceJumps(e : Core, name : String,
                 chosen : fun(Core) -> Option Core) -> Core =
    replaceJumpsIn(e, name, False, chosen)

fun replaceJumpsIn(e : Core, name : String, shadowed : Bool,
                   chosen : fun(Core) -> Option Core) -> Core = match e.kind {
    CJump(target, args) -> {
        if !shadowed && target == name {
            match chosen(e) {
                Some(made) -> return made
                None -> {}
            }
        }
        mkCore(e.ty, e.span, CJump(target, Array.map(args, fun(a) =
            replaceJumpsIn(a, name, shadowed, chosen))))
    }
    CJoin(bound, params, body, rest, recursive) -> {
        let same = bound == name
        mkCore(e.ty, e.span, CJoin(bound, params,
            replaceJumpsIn(body, name, shadowed || (same && recursive), chosen),
            replaceJumpsIn(rest, name, shadowed || same, chosen),
            recursive))
    }
    _ -> mkCore(e.ty, e.span, mapKind(e.kind, Mapper {
        expr = fun(x) = replaceJumpsIn(x, name, shadowed, chosen),
        type_ = fun(t) = t,
        bind = fun(b) = withValue(b,
            replaceJumpsIn(b.value, name, shadowed, chosen)),
    }))
}

fun jumpsTo(e : Core, name : String) -> Bool {
    match e.kind {
        CJump(target, _) -> if target == name { return True }
        _ -> {}
    }
    for child in kids(e.kind) { if jumpsTo(child, name) { return True } }
    False
}

-- The term variables `e` mentions and does not itself bind.
--
-- `Turkey.Core.namesOf` is the over-approximation -- every name, bound or not
-- -- and it is the right one for keeping a binding alive. It is the wrong one
-- for asking whether moving a term could capture something, and using it there
-- cost most of this pass's reductions in Python: the continuation a `?` builds
-- contains `let x = ... in ...`, and `Option`'s `bind` calls its first
-- parameter `x`, so a guard reading every mentioned name refused to inline
-- `bind` at all.
fun freeNames(e : Core) -> Set.Set String = match e.kind {
    CVar(name) -> {
        let out = Set.new()
        Set.add(out, name)
        out
    }
    CLam(params, body, _) -> {
        let out = freeNames(body)
        for p in params { Set.remove(out, p.name) }
        out
    }
    CLet(name, _, value, body, _) -> {
        let out = freeNames(body)
        Set.remove(out, name)
        for n in Set.elements(freeNames(value)) { Set.add(out, n) }
        out
    }
    CLetRec(binds, body) -> {
        let out = freeNames(body)
        for b in binds {
            for n in Set.elements(freeNames(b.value)) { Set.add(out, n) }
        }
        for b in binds { Set.remove(out, b.name) }
        out
    }
    CJoin(name, params, body, rest, recursive) -> {
        let out = freeNames(body)
        for p in params { Set.remove(out, p.name) }
        if recursive { Set.remove(out, name) }
        let outer = freeNames(rest)
        Set.remove(outer, name)
        for n in Set.elements(outer) { Set.add(out, n) }
        out
    }
    CMatch(scrutinee, alts) -> {
        let out = freeNames(scrutinee)
        for n in Set.elements(freeNamesOfAlts(alts)) { Set.add(out, n) }
        out
    }
    _ -> {
        let out = Set.new()
        for child in kids(e.kind) {
            for n in Set.elements(freeNames(child)) { Set.add(out, n) }
        }
        out
    }
}

fun freeNamesOfAlts(alts : Array CAlt) -> Set.Set String {
    let out = Set.new()
    for a in alts {
        let inner = freeNames(a.body)
        for name in patternVars(a.pat) { Set.remove(inner, name) }
        for n in Set.elements(inner) { Set.add(out, n) }
    }
    out
}

fun namesOfAlts(alts : Array CAlt) -> Set.Set String {
    let out = Set.new()
    for a in alts { namesInto(a.body, out) }
    out
}

-- Every name bound anywhere inside `e`.
--
-- An over-approximation, and in the safe direction: a name that appears here
-- but binds nothing on the path to an occurrence only costs a substitution this
-- pass declines to make. Python drives it off *field names* -- a `pat` binds
-- what its pattern binds, a `params` binds its parameters' names -- so that a
-- node added later cannot fall outside the rule by accident. The cases are
-- spelled out here instead, and `kids` is what keeps the recursion from being
-- one of them.
fun bindersOf(e : Core) -> Set.Set String {
    let out = Set.new()
    bindersInto(e, out)
    out
}

fun bindersInto(e : Core, out : Set.Set String) -> Unit {
    match e.kind {
        CLam(params, _, _) -> for p in params { Set.add(out, p.name) }
        CLet(name, _, _, _, _) -> Set.add(out, name)
        CLetRec(binds, _) -> for b in binds { Set.add(out, b.name) }
        CJoin(name, params, _, _, _) -> {
            Set.add(out, name)
            for p in params { Set.add(out, p.name) }
        }
        CMatch(_, alts) -> for a in alts {
            for name in patternVars(a.pat) { Set.add(out, name) }
        }
        _ -> {}
    }
    for child in kids(e.kind) { bindersInto(child, out) }
}

fun overlaps(a : Set.Set String, b : Set.Set String) -> Bool {
    for n in Set.elements(a) { if Set.contains(b, n) { return True } }
    False
}

fun unannot(p : Pat) -> Pat = match p.kind {
    PAnnot(inner, _) -> unannot(inner)
    _ -> p
}

fun oops(message : String) -> a =
    Diag.fail(Diag.whole("internal error", message), "")

fun discard(x : a) -> Unit = {}
