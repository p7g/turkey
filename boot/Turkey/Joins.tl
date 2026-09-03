module Turkey.Joins (
    discover,
)

-- Join-point discovery (`plan.txt` item 7, `turkey/joins.py`).
--
-- A `let`-bound lambda is a **join point** when every mention of it is a
-- saturated call in tail position and none of them escapes. Then it is not a
-- function at all: it is a label, and the calls are jumps. That is the analysis
-- Maurer, Downen, Ariola and Peyton Jones describe in *Compiling without
-- continuations* (PLDI 2017).
--
-- The IR has had `CJoin` and `CJump` since it existed. This is the pass that
-- produces them, and it is an analysis rather than a transformation of meaning:
-- the term it emits computes exactly what the term it consumed did, and what
-- changed is only that a fact already true of the program is now written down
-- where a backend can act on it.
--
-- It knows nothing about monads, deliberately. A `bind` chain reaches this pass
-- the way any other saturated call to a known small function reaches it; what
-- makes `?` fast is inlining and case-of-case meeting a general join-point pass
-- here, not a second desugaring that knows what `Flow` is.
--
-- Which positions are tail positions is stated once, in `Turkey.Core`, and read
-- by both this pass and the rule in `Turkey.Coretc`. Where the two could drift
-- the disagreement is caught automatically: calling a position a tail where the
-- checker does not means a jump with nowhere to go, which is a rejected term
-- rather than a wrong answer. Being conservative the other way costs an
-- optimization and nothing else.
--
-- Python walks all three of these passes reflectively over the dataclass
-- fields, consulting `TAIL_FIELDS` by field *name*. There is no reflection
-- here, so the structure is written twice and only twice: `parts` reads a
-- node's children with their tail flags, and `mapTails` rebuilds one the same
-- way. Everything else in the module is expressed in those two.

import Turkey.Ast (patternVars)
import Turkey.Core
import Turkey.Coretc (compatible)
import Turkey.Diag as Diag
import Turkey.Types as Ty
import Turkey.Types (TBottom)

-- -- the structure, twice ------------------------------------------------------------

-- Every subterm, each paired with whether it is a tail position -- given that
-- the term it belongs to is itself in one when `tail`.
fun parts(k : CoreKind, tail : Bool) -> Array (Core, Bool) = match k {
    CLit(_) -> Array.new(0)
    CUnit -> Array.new(0)
    CVar(_) -> Array.new(0)
    CCon(_) -> Array.new(0)
    CPrim(_) -> Array.new(0)
    CTuple(elems) -> Array.map(elems, fun(x) = (x, False))
    CArray(elems) -> Array.map(elems, fun(x) = (x, False))
    CRecord(_, fields) -> Array.map(fields, fun(f) = {
        let (_, value) = f
        (value, False)
    })
    CField(target, _) -> [(target, False)]
    CProject(target, _) -> [(target, False)]
    CIndex(target, index) -> [(target, False), (index, False)]
    -- A closure outlives the frame that binds the join, so a lambda's body is
    -- not a tail position of anything outside it.
    CLam(_, body, _) -> [(body, False)]
    CApp(fn, args) -> {
        let out = [(fn, False)]
        for a in args { Array.push(out, (a, False)) }
        out
    }
    CTyLam(_, body) -> [(body, False)]
    CTyApp(fn, _) -> [(fn, False)]
    CLet(_, _, value, body, _) -> [(value, False), (body, tail)]
    CLetRec(binds, body) -> {
        let out = Array.map(binds, fun(b) = (b.value, False))
        Array.push(out, (body, tail))
        out
    }
    CJoin(_, _, body, rest, _) -> [(body, tail), (rest, tail)]
    CJump(_, args) -> Array.map(args, fun(x) = (x, False))
    CRef(value) -> [(value, False)]
    CDeref(target) -> [(target, False)]
    CAssign(target, value) -> [(target, False), (value, False)]
    CIf(cond, then, otherwise) -> {
        let out = [(cond, False), (then, tail)]
        match otherwise {
            Some(o) -> Array.push(out, (o, tail))
            None -> {}
        }
        out
    }
    CMatch(scrutinee, alts) -> {
        let out = [(scrutinee, False)]
        for a in alts { Array.push(out, (a.body, tail)) }
        out
    }
}

-- The same shape, rebuilding. The five kinds with tail positions are spelled
-- out; everything else has none, so the ordinary mapper covers it.
fun mapTails(k : CoreKind, tail : Bool,
             f : fun(Core, Bool) -> Core) -> CoreKind = match k {
    CLet(name, bound, value, body, binders) ->
        CLet(name, bound, f(value, False), f(body, tail), binders)
    CLetRec(binds, body) -> CLetRec(Array.map(binds, fun(b) = CBind {
        name = b.name, ty = b.ty, binders = b.binders,
        value = f(b.value, False), span = b.span, mutable = b.mutable,
        module_ = b.module_, equations = b.equations,
    }), f(body, tail))
    CJoin(name, params, body, rest, recursive) ->
        CJoin(name, params, f(body, tail), f(rest, tail), recursive)
    CIf(cond, then, otherwise) -> CIf(f(cond, False), f(then, tail),
        match otherwise {
            Some(o) -> Some(f(o, tail))
            None -> None
        })
    CMatch(scrutinee, alts) -> CMatch(f(scrutinee, False),
        Array.map(alts, fun(a) = CAlt { pat = a.pat, body = f(a.body, tail) }))
    _ -> mapKind(k, Mapper {
        expr = fun(x) = f(x, False),
        type_ = fun(t) = t,
        bind = fun(b) = CBind {
            name = b.name, ty = b.ty, binders = b.binders,
            value = f(b.value, False), span = b.span, mutable = b.mutable,
            module_ = b.module_, equations = b.equations,
        },
    })
}

-- -- the walk ------------------------------------------------------------------------

-- Every binding's body, with its join points found.
fun discover(program : CProgram) -> CProgram = CProgram {
    dicts = Array.map(program.dicts, joinBind),
    binds = Array.map(program.binds, joinBind),
}

fun joinBind(b : CBind) -> CBind = CBind {
    name = b.name, ty = b.ty, binders = b.binders, value = expr(b.value),
    span = b.span, mutable = b.mutable, module_ = b.module_,
    equations = b.equations,
}

-- Rewrite `e`, converting any binding under it that is a join point.
--
-- Note what this walk does *not* carry: whether `e` is itself in tail position.
-- It does not need to. Whether `f` is a join point is a question about tail
-- position within `f`'s own scope -- the body of the `let` that binds it -- and
-- that is the same question wherever the `let` sits. A candidate that fails
-- inside an argument fails for its own reasons.
fun expr(e : Core) -> Core {
    match e.kind {
        CLet(name, _, value, body, binders) -> if len(binders) == 0 {
            match asJoin(name, value, body, e.ty, False) {
                Some(params) ->
                    return finish(e, name, params, value, body, False)
                None -> {}
            }
        }
        CLetRec(binds, body) -> if len(binds) == 1 && len(binds[0].binders) == 0 {
            let b = binds[0]
            match asJoin(b.name, b.value, body, e.ty, True) {
                Some(params) ->
                    return finish(e, b.name, params, b.value, body, True)
                None -> {}
            }
        }
        _ -> {}
    }
    mkCore(e.ty, e.span, mapKind(e.kind, Mapper {
        expr = expr,
        type_ = fun(t) = t,
        bind = joinBind,
    }))
}

-- Build a join whose eligibility is already settled.
--
-- Both halves are rewritten from tail position, because both halves *are* tail
-- positions of this join -- that is what `Turkey.Core.tailFieldNames` says about
-- `CJoin`, and the checker reads the same entry. A non-recursive join has no
-- self-calls for the `rest` pass to find, so passing `True` there is a statement
-- rather than a filter.
fun finish(e : Core, name : String, params : Array CParam, lam : Core,
           rest : Core, recursive : Bool) -> Core {
    let body = match lam.kind {
        CLam(_, inner, _) -> inner
        _ -> oops("a join whose value is not a lambda")
    }
    let arity = len(params)
    mkCore(e.ty, lam.span, CJoin(
        name, params,
        jumps(expr(body), name, arity, recursive),
        jumps(expr(rest), name, arity, True),
        recursive))
}

-- -- eligibility ---------------------------------------------------------------------

-- The join's parameters if this binding qualifies, or `None`.
--
-- Five conditions, and each is the same one seen from a different side: the
-- binding is a lambda; nothing rebinds its name underneath, so every mention
-- below really is a mention of it; every mention is a saturated tail call; the
-- self-calls are there only if the join says it is recursive; and the lambda
-- answers what the surrounding term answers, since a jump replaces the value of
-- the whole join expression.
--
-- Split in two because the `match` that takes the lambda apart cannot also
-- answer `None` for the binding that is not one: a control transfer may not
-- cross a destructuring binding (FINDINGS.md 41).
fun asJoin(name : String, value : Core, body : Core, ty : Ty.Type,
           recursive : Bool) -> Option (Array CParam) = match value.kind {
    CLam(params, inner, _) -> asJoinLam(name, params, inner, body, ty,
                                        recursive)
    _ -> None
}

fun asJoinLam(name : String, params : Array CParam, inner : Core, body : Core,
              ty : Ty.Type, recursive : Bool) -> Option (Array CParam) {
    let arity = len(params)
    if rebinds(body, name) || rebinds(inner, name) { return None }
    if !onlyTailCalls(body, name, arity, True) { return None }
    if !onlyTailCalls(inner, name, arity, recursive) { return None }
    -- A `letrec` whose one binding never calls itself. Legal, and not this
    -- pass's business to tidy: leaving it alone keeps the recursive case about
    -- recursion.
    if recursive && !mentions(inner, name) { return None }
    if !compatible(ty, inner.ty) { return None }
    Some(params)
}

-- Whether every mention of `name` in `e` is a saturated call in tail position
-- -- and, when `allowed` is false, whether there are none at all.
fun onlyTailCalls(e : Core, name : String, arity : Int,
                  allowed : Bool) -> Bool {
    if !mentions(e, name) { return True }
    if !allowed { return False }
    walkCalls(e, name, arity, True)
}

fun walkCalls(e : Core, name : String, arity : Int, tail : Bool) -> Bool {
    match e.kind {
        -- A bare mention: it escapes -- into an argument, a data structure, a
        -- returned value -- and a label cannot be passed anywhere.
        CVar(n) -> return n != name
        CApp(fn, args) -> match fn.kind {
            CVar(n) -> if n == name {
                if !tail || len(args) != arity { return False }
                for a in args {
                    if !walkCalls(a, name, arity, False) { return False }
                }
                return True
            }
            _ -> {}
        }
        _ -> {}
    }
    for (child, inTail) in parts(e.kind, tail) {
        if !walkCalls(child, name, arity, inTail) { return False }
    }
    True
}

-- Turn the calls the analysis just licensed into jumps.
fun jumps(e : Core, name : String, arity : Int, tail : Bool) -> Core {
    match e.kind {
        CApp(fn, args) -> match fn.kind {
            CVar(n) -> if n == name && tail {
                if len(args) != arity {
                    oops("an unlicensed call reached jumps")
                }
                return mkCore(TBottom, e.span, CJump(name,
                    Array.map(args, fun(a) = jumps(a, name, arity, False))))
            }
            _ -> {}
        }
        _ -> {}
    }
    mkCore(e.ty, e.span, mapTails(e.kind, tail,
                                  fun(x, t) = jumps(x, name, arity, t)))
}

-- -- two small questions about names --------------------------------------------------

fun mentions(e : Core, name : String) -> Bool {
    match e.kind {
        CVar(n) -> if n == name { return True }
        _ -> {}
    }
    for (child, _) in parts(e.kind, False) {
        if mentions(child, name) { return True }
    }
    False
}

-- Whether anything under `e` binds `name` again.
--
-- Names are unique after resolution and the generated ones carry a counter, so
-- this should never fire -- which is exactly why it is worth asking. If it ever
-- did, an inner mention would not be a mention of the join point, and rewriting
-- it to a jump would be a miscompilation the checker could well accept, since
-- the two would agree on types and differ only on which binding they meant.
fun rebinds(e : Core, name : String) -> Bool {
    match e.kind {
        CLam(params, _, _) -> for p in params {
            if p.name == name { return True }
        }
        CJoin(bound, params, _, _, _) -> {
            if bound == name { return True }
            for p in params { if p.name == name { return True } }
        }
        CLet(bound, _, _, _, _) -> if bound == name { return True }
        CLetRec(binds, _) -> for b in binds {
            if b.name == name { return True }
        }
        CMatch(_, alts) -> for a in alts {
            if Array.contains(patternVars(a.pat), name) { return True }
        }
        _ -> {}
    }
    for (child, _) in parts(e.kind, False) {
        if rebinds(child, name) { return True }
    }
    False
}

fun oops(message : String) -> a =
    Diag.fail(Diag.whole("internal error", message), "")
