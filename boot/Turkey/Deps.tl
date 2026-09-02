module Turkey.Deps (freeNames, freeNamesOfFun, sccs)

-- Free-variable and dependency analysis over trees (`turkey/deps.py`).
--
-- Two entry points: the names a subtree references but does not bind, and
-- Tarjan's strongly-connected components emitted dependencies-first. Between
-- them they decide what a binding group is, which is what generalization is
-- quantified over.
--
-- `patternVars` lives in `Turkey.Ast`, being a structural question about the
-- tree that three stages ask.

import Data.Set as Set
import Turkey.Ast
import Turkey.Diag as Diag

-- -- free names ---------------------------------------------------------------------

-- The unbound names an expression references. A constructor counts: a binding
-- that mentions one depends on nothing at the value level, but the walk cannot
-- tell the two apart and the caller filters by what it actually bound.
fun freeNames(e : Expr, bound : Set.Set String) -> Set.Set String {
    let out = Set.new()
    exprFree(e, bound, out)
    out
}

-- A function's body, with its parameters bound. The function's own name is
-- deliberately *not* bound here; the caller decides whether it is recursive.
fun freeNamesOfFun(d : FunDecl, bound : Set.Set String) -> Set.Set String {
    let inner = extend(bound, paramVars(d.params))
    let out = Set.new()
    match d.body {
        Some(b) -> exprFree(b, inner, out)
        None -> {}
    }
    out
}

fun exprFree(e : Expr, bound : Set.Set String, out : Set.Set String) -> Unit =
    match e.kind {
        ELit(_) -> {}
        EUnit -> {}
        EContinue -> {}
        EVar(name, _) -> if !Set.contains(bound, name) { Set.add(out, name) }
        ECon(name) -> if !Set.contains(bound, name) { Set.add(out, name) }
        ETuple(elems) -> for x in elems { exprFree(x, bound, out) }
        EArray(elems) -> for x in elems { exprFree(x, bound, out) }
        EField(obj, _) -> exprFree(obj, bound, out)
        EProject(obj, _) -> exprFree(obj, bound, out)
        ERecord(con, fields) -> {
            if !Set.contains(bound, con) { Set.add(out, con) }
            for (_, value) in fields { exprFree(value, bound, out) }
        }
        ECall(fn, args) -> {
            exprFree(fn, bound, out)
            for a in args { exprFree(a, bound, out) }
        }
        EIndex(arr, index, _, _) -> {
            exprFree(arr, bound, out)
            exprFree(index, bound, out)
        }
        EUnary(_, operand, _) -> exprFree(operand, bound, out)
        EBinary(_, left, right, _) -> {
            exprFree(left, bound, out)
            exprFree(right, bound, out)
        }
        EAnnot(inner, _) -> exprFree(inner, bound, out)
        EIf(cond, then, otherwise) -> {
            exprFree(cond, bound, out)
            exprFree(then, bound, out)
            match otherwise {
                Some(o) -> exprFree(o, bound, out)
                None -> {}
            }
        }
        EWhile(cond, body) -> {
            exprFree(cond, bound, out)
            exprFree(body, bound, out)
        }
        ELoop(body) -> exprFree(body, bound, out)
        EReturn(value) -> optFree(value, bound, out)
        EBreak(value) -> optFree(value, bound, out)
        ELambda(params, _, body) ->
            exprFree(body, extend(bound, paramVars(params)), out)
        EMatch(scrutinee, arms) -> {
            exprFree(scrutinee, bound, out)
            for arm in arms {
                let inner = Set.new()
                for n in Set.elements(bound) { Set.add(inner, n) }
                for p in arm.patterns {
                    for v in patternVars(p) { Set.add(inner, v) }
                }
                exprFree(arm.body, inner, out)
            }
        }
        EForIn(pat, iterable, body, _, _) -> {
            exprFree(iterable, bound, out)
            exprFree(body, extend(bound, patternVars(pat)), out)
        }
        -- init, cond, step and body share one scope; only init introduces
        -- bindings the rest can see.
        EForC(init, cond, step, body) -> {
            let current = Set.new()
            for n in Set.elements(bound) { Set.add(current, n) }
            match init {
                Some(s) -> for n in stmtFree(s, current, out) { Set.add(current, n) }
                None -> {}
            }
            exprFree(cond, current, out)
            match step {
                Some(s) -> discard(stmtFree(s, current, out))
                None -> {}
            }
            exprFree(body, current, out)
        }
        EBlock(stmts) -> blockFree(stmts, bound, out)
        -- Neither survives desugaring, and nothing calls this before it runs.
        EQuestion(inner, _) -> exprFree(inner, bound, out)
        EDo(body) -> exprFree(body, bound, out)
    }

fun optFree(e : Option Expr, bound : Set.Set String, out : Set.Set String) -> Unit =
    match e {
        Some(x) -> exprFree(x, bound, out)
        None -> {}
    }

-- Walk a statement list left to right; each binding is in scope for every
-- statement after it.
fun blockFree(stmts : Array Stmt, bound : Set.Set String,
              out : Set.Set String) -> Unit {
    let current = Set.new()
    for n in Set.elements(bound) { Set.add(current, n) }
    for s in stmts {
        for n in stmtFree(s, current, out) { Set.add(current, n) }
    }
}

-- Adds one statement's free names to `out`, and answers what it newly binds.
fun stmtFree(s : Stmt, bound : Set.Set String,
             out : Set.Set String) -> Array String = match s.kind {
    SLet(pat, value) -> {
        exprFree(value, bound, out)
        patternVars(pat)
    }
    SVar(pat, value) -> {
        exprFree(value, bound, out)
        patternVars(pat)
    }
    SFun(d) -> {
        -- A local function's own name is in scope for its body, so it may
        -- recurse, and for later statements.
        let inner = extend(bound, [d.name])
        for n in Set.elements(freeNamesOfFun(d, inner)) { Set.add(out, n) }
        [d.name]
    }
    -- `x = e` still references `x`, so the target is walked too.
    SAssign(target, value) -> {
        exprFree(target, bound, out)
        exprFree(value, bound, out)
        Array.new(0)
    }
    SExpr(e) -> {
        exprFree(e, bound, out)
        Array.new(0)
    }
}

fun paramVars(params : Array Pat) -> Array String {
    let out = Array.new(4)
    for p in params {
        for v in patternVars(p) { Array.push(out, v) }
    }
    out
}

fun extend(bound : Set.Set String, names : Array String) -> Set.Set String {
    let out = Set.new()
    for n in Set.elements(bound) { Set.add(out, n) }
    for n in names { Set.add(out, n) }
    out
}

-- -- strongly-connected components --------------------------------------------------

type Frame = Frame {
    node  : String,
    succs : Array String,
    at    : Int,
}

type Tarjan = Tarjan {
    indexOf : Map String Int,
    lowlink : Map String Int,
    onStack : Map String Bool,
    stack   : Array String,
    result  : Array (Array String),
    counter : Int,
}

-- Tarjan's algorithm, iterative.
--
-- `graph` maps a name to the names it depends on. Edges to names that are not
-- keys of `graph` are ignored. Components come out dependencies-first: if A
-- depends on B, B is emitted first. Names within a component are sorted, and so
-- are the successors of each node, because the order components come out in is
-- the order bindings are checked and then evaluated -- it has to be a property
-- of the program rather than of the host's iteration order.
--
-- Python holds a live iterator on its work stack; there are none here, so a
-- frame carries its successor list and how far it has got.
fun sccs(graph : Map String (Set.Set String)) -> Array (Array String) {
    let t = Tarjan {
        indexOf = Map.new(), lowlink = Map.new(), onStack = Map.new(),
        stack = Array.new(8), result = Array.new(8), counter = 0,
    }
    for (start, _) in Map.entries(graph) {
        if !Map.contains(t.indexOf, start) { visit(t, graph, start) }
    }
    t.result
}

fun visit(t : Tarjan, graph : Map String (Set.Set String), start : String) -> Unit {
    enter(t, start)
    let work = Array.new(8)
    Array.push(work, Frame {
        node = start, succs = successors(graph, start), at = 0,
    })
    while len(work) > 0 {
        let frame = work[len(work) - 1]
        var descended = False
        while frame.at < len(frame.succs) && !descended {
            let succ = frame.succs[frame.at]
            frame.at = frame.at + 1
            if Map.contains(graph, succ) {
                if !Map.contains(t.indexOf, succ) {
                    enter(t, succ)
                    Array.push(work, Frame {
                        node = succ, succs = successors(graph, succ), at = 0,
                    })
                    descended = True
                } else if Map.getOr(t.onStack, succ, False) {
                    let seen = Map.getOr(t.indexOf, succ, 0)
                    if seen < Map.getOr(t.lowlink, frame.node, 0) {
                        Map.put(t.lowlink, frame.node, seen)
                    }
                }
            }
        }
        if !descended {
            discard(Array.pop(work))
            close(t, frame.node)
            if len(work) > 0 {
                let parent = work[len(work) - 1].node
                let mine = Map.getOr(t.lowlink, frame.node, 0)
                if mine < Map.getOr(t.lowlink, parent, 0) {
                    Map.put(t.lowlink, parent, mine)
                }
            }
        }
    }
}

fun enter(t : Tarjan, name : String) -> Unit {
    Map.put(t.indexOf, name, t.counter)
    Map.put(t.lowlink, name, t.counter)
    t.counter = t.counter + 1
    Array.push(t.stack, name)
    Map.put(t.onStack, name, True)
}

fun close(t : Tarjan, node : String) -> Unit {
    if Map.getOr(t.lowlink, node, 0) != Map.getOr(t.indexOf, node, 0) { return {} }
    let component = Array.new(2)
    var going = True
    while going {
        match Array.pop(t.stack) {
            None -> { going = False }
            Some(w) -> {
                Map.put(t.onStack, w, False)
                Array.push(component, w)
                if w == node { going = False }
            }
        }
    }
    Array.push(t.result, Array.sort(component))
}

fun successors(graph : Map String (Set.Set String), name : String) -> Array String =
    match Map.get(graph, name) {
        Some(s) -> Array.sort(Set.elements(s))
        None -> Array.new(0)
    }

fun discard(x : a) -> Unit = {}
