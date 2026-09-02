module Turkey.Desugar (program)

-- `?` and `do`, rewritten into `bind` (SPEC-DELTAS.md 46, `turkey/desugar.py`).
--
-- Every other sugar in this language is a node the later stages interpret:
-- `a + b` carries the `add` it means, `for x in xs` carries its `iter` and
-- `next`. `?` cannot be one of those, because what it binds is *the rest of the
-- enclosing statement sequence*, and no node annotated in place can name that.
-- So this is a pass, and it runs early enough that nothing downstream ever sees
-- a `?`.
--
-- It runs *after* `Turkey.Resolve`, which buys two things. The code it moves
-- into lambdas has already been resolved, so moving it cannot change what a
-- name means; and the code it writes may name internal constructors directly
-- rather than going looking for surface names a module might not export.
--
-- ## What a `?` unwinds to
--
-- A **do-context** is the block a `?` unwinds to, and there are two: an
-- explicit `do { ... }`, and the body of a `fun` or lambda that contains a `?`.
-- Everything else -- `if`, `match`, a bare block -- is transparent, and a `?`
-- inside one lifts outward through it. A lambda is opaque, so a `?` in a
-- callback belongs to the callback.
--
-- A `do` with no `?` in it emits nothing whatsoever and means exactly the block
-- it wraps. That is the answer to "what monad is an empty `do`": it does not
-- have one, because it never asked for a `bind`.
--
-- ## The translation
--
-- `seq` translates a statement sequence, `expr` an expression; both take a
-- continuation from the value's tree to the rest of the program's. Straight-
-- line code comes out as exactly the chain someone would have written by hand:
--
--     do { let x = f(a?) + 1; g(x) }
--       =>  bind(a, fun(%k1) { let x = f(%k1) + 1; g(x) })
--
-- A control construct containing a `?` is *lifted* into the monad instead: each
-- branch is translated with `pure` as its continuation, and the construct as a
-- whole becomes the left argument of a `bind`. That `pure` is the one place the
-- lowering adds something the author did not write, and it is not the auto-
-- `pure` that was ruled out: the tail of a *do block* is still taken to be
-- already monadic, and a lifted branch is not a do block anyone wrote.
--
-- ## What crosses a bind (SPEC-DELTAS.md 47)
--
-- A `return`, `break` or `continue` after a `?` would land inside a generated
-- lambda, where `return` means "return from the lambda". It cannot escape
-- through the `bind` either -- escaping is not something a `bind` does, and for
-- `Array`, whose bind runs its continuation once per element, there is nothing
-- coherent for an escape to mean. So it travels as a value, in the Prelude's
-- `Flow`. A context that needs this runs in **flow mode**: every statement
-- answers with `m (Flow ...)`, the next runs only under `Fall`, and the
-- context's boundary is where `Ret` becomes a result again.
--
-- Loops are the same problem twice over: a loop's continuation is not known
-- until its body has run, so it cannot be a lambda written once. A lifted loop
-- becomes a recursive local `fun` answering with a `Flow`, where `Brk` becomes
-- the loop's own `Fall` -- which is why `let v = loop { ... break x }` still
-- works -- and `Ret` keeps travelling.
--
-- ## Two things the Python did with reflection
--
-- `turkey/desugar.py` asks four questions of a subtree (`_sugared`, `_owns`,
-- `_escapes`, `_has_lifted_loop`) that differ only in where they stop, and
-- writes each as a generic walk over the dataclass fields. Turkey has no
-- reflection, so `children` below is the one hand-written enumerator all four
-- share. It is the file's one maintenance hazard: a node kind added to
-- `Turkey.Ast` and forgotten here is missed by all four scanners at once, where
-- the Python's version could not miss one.
--
-- And a continuation is compared *by identity* there -- `k is not self.fall`
-- decides whether a loop is in statement position. Turkey has no function
-- identity, so a continuation is a variant here and the three the pass builds
-- for itself are named constructors rather than closures. That is arguably the
-- better spelling: `KFall` says what it is where a bound method did not.

import Turkey.Ast
import Turkey.Diag as Diag
import Turkey.Prelude as P
import Turkey.Source (Span)

-- -- state --------------------------------------------------------------------------

type D = D {
    -- The generated-name counter. `%` is not a character an identifier may
    -- contain, so a name made from it cannot collide with one the author wrote.
    n       : Int,
    -- The module's protocol-method scope, for `bind` and `pure`.
    methods : Map String String,
    -- How many lifted loops enclose the code being translated. A `break` only
    -- becomes a `Brk` inside one; outside, it is left alone so that the checker
    -- still reports it as a `break` with no loop, which is what it is.
    depth   : Int,
    -- Where the parser's id counter got to. Nodes written here continue it.
    nextId  : Int,
}

fun freshId(d : D) -> Int {
    d.nextId = d.nextId + 1
    d.nextId
}

fun mkExpr(d : D, s : Span, k : ExprKind) -> Expr =
    Expr { id = freshId(d), span = s, kind = k }

fun mkStmt(d : D, s : Span, k : StmtKind) -> Stmt =
    Stmt { id = freshId(d), span = s, kind = k }

fun mkPat(d : D, s : Span, k : PatKind) -> Pat =
    Pat { id = freshId(d), span = s, kind = k }

-- A fresh `%`-name. The counter is bumped before anything else the caller does,
-- because a continuation may make names of its own and the numbering is part of
-- what the two implementations agree about.
fun fresh(d : D, prefix : String) -> String {
    d.n = d.n + 1
    prefix + Int.toString(d.n)
}

-- -- continuations ------------------------------------------------------------------

-- Given the tree of a value, the tree of everything after it.
--
-- Three of the four are the pass's own and are named rather than built: `KId`
-- is a do-context's boundary in the ordinary case, `KFall` is flow mode's tail,
-- and `KPure` is what a lifted branch ends at. Only `KFn` is a closure.
type Cont
    = KId
    | KFall
    | KPure
    | KFn(fun(Expr) -> Expr)

fun apply(d : D, k : Cont, value : Expr) -> Expr = match k {
    KId -> value
    KFall -> fallOf(d, value)
    KPure -> pureOf(d, value)
    KFn(f) -> f(value)
}

fun isFall(k : Cont) -> Bool = match k {
    KFall -> True
    _ -> False
}

-- -- the child enumerator -----------------------------------------------------------

-- Every `Expr`, `Stmt`, `Arm` or `FunDecl` directly under a node.
--
-- The method references the parser writes -- `EBinary`'s `add`, `EForIn`'s
-- `iter`, `EQuestion`'s `bind` -- are deliberately not children. Each is a bare
-- `EVar`, so no scanner's answer can depend on whether it is walked, and
-- leaving them out keeps this list to the syntax.
type Child
    = ChExpr(Expr)
    | ChStmt(Stmt)
    | ChArm(Arm)
    | ChFun(FunDecl)

fun children(c : Child) -> Array Child = match c {
    ChExpr(e) -> exprChildren(e)
    ChStmt(s) -> stmtChildren(s)
    ChArm(a) -> [ChExpr(a.body)]
    ChFun(f) -> match f.body {
        Some(b) -> [ChExpr(b)]
        None -> Array.new(0)
    }
}

fun exprChildren(e : Expr) -> Array Child = match e.kind {
    ELit(_) -> Array.new(0)
    EUnit -> Array.new(0)
    EVar(_, _) -> Array.new(0)
    ECon(_) -> Array.new(0)
    EContinue -> Array.new(0)
    ETuple(xs) -> Array.map(xs, ChExpr)
    EArray(xs) -> Array.map(xs, ChExpr)
    ERecord(_, fields) -> fieldChildren(fields)
    ELambda(_, _, body) -> [ChExpr(body)]
    ECall(fn, args) -> Array.append([ChExpr(fn)], Array.map(args, ChExpr))
    EIndex(arr, index, _, _) -> [ChExpr(arr), ChExpr(index)]
    EField(obj, _) -> [ChExpr(obj)]
    EProject(obj, _) -> [ChExpr(obj)]
    EUnary(_, operand, _) -> [ChExpr(operand)]
    EBinary(_, left, right, _) -> [ChExpr(left), ChExpr(right)]
    EAnnot(inner, _) -> [ChExpr(inner)]
    EIf(cond, then, otherwise) ->
        Array.append([ChExpr(cond), ChExpr(then)], optExpr(otherwise))
    EWhile(cond, body) -> [ChExpr(cond), ChExpr(body)]
    EForIn(_, iterable, body, _, _) -> [ChExpr(iterable), ChExpr(body)]
    EForC(init, cond, step, body) ->
        Array.append(Array.append(optStmt(init), [ChExpr(cond)]),
                     Array.append(optStmt(step), [ChExpr(body)]))
    ELoop(body) -> [ChExpr(body)]
    EMatch(scrutinee, arms) ->
        Array.append([ChExpr(scrutinee)], Array.map(arms, ChArm))
    EReturn(value) -> optExpr(value)
    EBreak(value) -> optExpr(value)
    EBlock(stmts) -> Array.map(stmts, ChStmt)
    EQuestion(inner, _) -> [ChExpr(inner)]
    EDo(body) -> [ChExpr(body)]
}

fun stmtChildren(s : Stmt) -> Array Child = match s.kind {
    SLet(_, value) -> [ChExpr(value)]
    SVar(_, value) -> [ChExpr(value)]
    SFun(d) -> [ChFun(d)]
    SAssign(target, value) -> [ChExpr(target), ChExpr(value)]
    SExpr(e) -> [ChExpr(e)]
}

fun fieldChildren(fields : Array (String, Expr)) -> Array Child {
    let out = Array.new(len(fields))
    for (_, value) in fields { Array.push(out, ChExpr(value)) }
    out
}

fun optExpr(e : Option Expr) -> Array Child = match e {
    Some(x) -> [ChExpr(x)]
    None -> Array.new(0)
}

fun optStmt(s : Option Stmt) -> Array Child = match s {
    Some(x) -> [ChStmt(x)]
    None -> Array.new(0)
}

-- -- the scanners -------------------------------------------------------------------

-- Is there a `?` or a `do` anywhere under here at all? Stops nowhere. Used only
-- to prune: a subtree this says nothing about is left exactly as it was found.
fun sugared(c : Child) -> Bool {
    match c {
        ChExpr(e) -> match e.kind {
            EQuestion(_, _) -> return True
            EDo(_) -> return True
            _ -> {}
        }
        _ -> {}
    }
    for ch in children(c) {
        if sugared(ch) { return True }
    }
    False
}

-- Is there a `?` under here that unwinds to *this* do-context?
--
-- Stops at a lambda, a local `fun` and a `do`, each of which is a context of
-- its own. It does not stop at a loop -- a `?` in a loop body has to be *seen*
-- in order to be lifted or refused.
fun owns(c : Child) -> Bool {
    match c {
        ChExpr(e) -> match e.kind {
            EQuestion(_, _) -> return True
            EDo(_) -> return False
            ELambda(_, _, _) -> return False
            _ -> {}
        }
        ChStmt(s) -> match s.kind {
            SFun(_) -> return False
            _ -> {}
        }
        _ -> {}
    }
    for ch in children(c) {
        if owns(ch) { return True }
    }
    False
}

-- A `return`/`break`/`continue` here that targets something outside `c`.
--
-- Stops at a lambda, whose `return` is its own. A `break` inside a loop that is
-- itself part of `c` belongs to that loop and does not escape, which is what
-- `inLoop` tracks.
fun escapes(c : Child, inLoop : Bool) -> Option Expr {
    var inner = inLoop
    match c {
        ChExpr(e) -> match e.kind {
            EReturn(_) -> return Some(e)
            EBreak(_) -> if !inLoop { return Some(e) }
            EContinue -> if !inLoop { return Some(e) }
            ELambda(_, _, _) -> return None
            EWhile(_, _) -> { inner = True }
            EForIn(_, _, _, _, _) -> { inner = True }
            EForC(_, _, _, _) -> { inner = True }
            ELoop(_) -> { inner = True }
            _ -> {}
        }
        ChStmt(s) -> match s.kind {
            SFun(_) -> return None
            _ -> {}
        }
        _ -> {}
    }
    for ch in children(c) {
        match escapes(ch, inner) {
            Some(found) -> return Some(found)
            None -> {}
        }
    }
    None
}

-- A loop holding a `?`, which therefore answers with a `Flow`.
fun hasLiftedLoop(c : Child) -> Bool {
    match c {
        ChExpr(e) -> match e.kind {
            EWhile(_, _) -> if owns(c) { return True }
            EForIn(_, _, _, _, _) -> if owns(c) { return True }
            EForC(_, _, _, _) -> if owns(c) { return True }
            ELoop(_) -> if owns(c) { return True }
            EDo(_) -> return False
            ELambda(_, _, _) -> return False
            _ -> {}
        }
        ChStmt(s) -> match s.kind {
            SFun(_) -> return False
            _ -> {}
        }
        _ -> {}
    }
    for ch in children(c) {
        if hasLiftedLoop(ch) { return True }
    }
    False
}

-- Must this context carry control transfers as values?
--
-- Two reasons it must. Either a transfer sits at or after the *first* statement
-- holding a `?`, so it would end up inside a lambda -- everything before that
-- stays in the prefix, at the nesting the author wrote it at, where a `return`
-- still means what it says. Or the context holds a lifted loop, which answers
-- with a `Flow` whether or not its body transfers.
fun needsFlow(stmts : Array Stmt) -> Bool {
    for s in stmts {
        if hasLiftedLoop(ChStmt(s)) { return True }
    }
    for var i = 0; i < len(stmts); i = i + 1 {
        if owns(ChStmt(stmts[i])) {
            for var j = i; j < len(stmts); j = j + 1 {
                if Option.isSome(escapes(ChStmt(stmts[j]), False)) { return True }
            }
            return False
        }
    }
    False
}

-- Does this statement answer with a `Flow` when translated in flow mode?
fun flowing(s : Stmt) -> Bool =
    Option.isSome(escapes(ChStmt(s), False)) || hasLiftedLoop(ChStmt(s))

-- Does this branch force its `if`/`match` to be lifted into the monad?
--
-- A `?` always does. In flow mode a control transfer does too, even with no `?`
-- anywhere near it: the transfer has to leave as a value, and only a lifted
-- branch can answer with one.
fun branching(e : Expr, flow : Bool) -> Bool =
    owns(ChExpr(e)) || (flow && Option.isSome(escapes(ChExpr(e), False)))

-- `return e`, `break e` or `continue` written as a whole statement.
fun transferOf(s : Stmt) -> Option Expr = match s.kind {
    SExpr(e) -> match e.kind {
        EReturn(_) -> Some(e)
        EBreak(_) -> Some(e)
        EContinue -> Some(e)
        _ -> None
    }
    _ -> None
}

-- -- the pass -----------------------------------------------------------------------

fun program(p : Program, methods : Map String String) -> Unit {
    let d = D { n = 0, methods = methods, depth = 0, nextId = p.nextId }
    for decl in p.decls {
        match decl {
            DClass(c) -> for m in c.methods { funBody(d, m) }
            DInstance(i) -> for m in i.methods { funBody(d, m) }
            DStmt(s) -> discard(walkStmt(d, s))
            DType(_) -> {}
        }
    }
    desugarIndexes(d, p)
    p.nextId = d.nextId
}

-- A `fun` body is a do-context exactly when it contains a `?`.
fun funBody(d : D, decl : FunDecl) -> Unit = match decl.body {
    None -> {}
    Some(b) -> if sugared(ChExpr(b)) { decl.body = Some(context(d, b)) }
}

-- A do-context: a `do` block, or the body of a `fun` holding a `?`.
fun context(d : D, body : Expr) -> Expr {
    -- The walk's *answer*, not `body`: a `do` is replaced rather than rewritten,
    -- and `fun f() = do { a? }` is a body that is one.
    if !owns(ChExpr(body)) { return walkExpr(d, body) }
    if !needsFlow(stmtsOf(d, body)) { return block(d, body, KId, False) }
    -- Something at or after the first `?` transfers control, so it would end up
    -- inside a lambda where `return` means the wrong thing. The whole context
    -- runs in flow mode, and its boundary is where `Ret` is cashed in.
    unflow(d, block(d, body, KFall, True), body.span)
}

fun stmtsOf(d : D, body : Expr) -> Array Stmt = match body.kind {
    EBlock(stmts) -> stmts
    _ -> [mkStmt(d, body.span, SExpr(body))]
}

fun block(d : D, body : Expr, k : Cont, flow : Bool) -> Expr =
    seq(d, stmtsOf(d, body), 0, k, body.span, flow)

-- -- statement sequences ------------------------------------------------------------

-- `S[stmts] k`, from index `from`. Statements with no `?` accumulate into a
-- prefix, so a block splits only where it actually has to.
--
-- In `flow` mode every statement answers with `m (Flow ...)`, `k` is
-- `pure . Fall`, and a control transfer becomes one of the other three
-- constructors instead of a jump. A statement that neither holds a `?` nor
-- transfers control still just goes in the prefix.
fun seq(d : D, stmts : Array Stmt, from : Int, k : Cont, span : Span,
        flow : Bool) -> Expr {
    let prefix = Array.new(4)
    for var i = from; i < len(stmts); i = i + 1 {
        let s = stmts[i]
        let last = i == len(stmts) - 1

        if flow {
            match transferOf(s) {
                Some(t) -> if d.depth > 0 || isReturn(t) {
                    -- Whatever follows is unreachable, so the sequence ends.
                    return wrap(d, prefix, transfer(d, t), span)
                }
                None -> {}
            }
        }

        if !owns(ChStmt(s))
           && !(flow && Option.isSome(escapes(ChStmt(s), False))) {
            discard(walkStmt(d, s))
            if last {
                match s.kind {
                    SExpr(e) -> return wrap(d, prefix, apply(d, k, e), span)
                    _ -> {}
                }
            }
            Array.push(prefix, s)
        } else {
            if !flow {
                -- Everything after this is about to become a lambda body, so a
                -- transfer in it would change meaning. `context` decides whether
                -- to run in flow mode; reaching here with one is a bug.
                refuseEscapes(d, stmts, i)
            }
            return wrap(d, prefix, split(d, stmts, i, k, span, flow, last), span)
        }
    }
    wrap(d, prefix, apply(d, k, mkExpr(d, span, EUnit)), span)
}

-- The statement that actually holds the `?`, and everything after it.
fun split(d : D, stmts : Array Stmt, i : Int, k : Cont, span : Span,
          flow : Bool, last : Bool) -> Expr {
    let s = stmts[i]
    -- What runs once this statement has been bound. It ignores the value: a
    -- `let` has already put it where it belongs, and a bare statement's value is
    -- discarded by the sequence.
    let after = KFn(fun(value) =
        if last { apply(d, k, mkExpr(d, s.span, EUnit)) }
        else { seq(d, stmts, i + 1, k, span, flow) })
    match s.kind {
        SLet(pat, value) -> binding(d, s, pat, value, False, after, flow)
        SVar(pat, value) -> binding(d, s, pat, value, True, after, flow)
        SAssign(target, value) -> {
            let assigned = KFn(fun(v) = mkExpr(d, s.span, EBlock([
                mkStmt(d, s.span, SAssign(target, v)),
                mkStmt(d, s.span, SExpr(apply(d, after, mkExpr(d, s.span, EUnit)))),
            ])))
            expr(d, value, assigned, False, flow)
        }
        SExpr(e) -> if last {
            expr(d, e, k, True, flow)
        } else if flow && flowing(s) {
            -- A lifted construct holding a transfer, or a lifted loop: it
            -- answers with a `Flow`, so the rest of the block runs only under
            -- `Fall` and the other three travel outward.
            sequence(d, expr(d, e, KFall, True, True), after, s.span)
        } else {
            expr(d, e, after, False, flow)
        }
        SFun(_) -> oops("'?' is not supported in this kind of statement yet",
                        s.span)
    }
}

fun binding(d : D, s : Stmt, pat : Pat, value : Expr, isVar : Bool,
            after : Cont, flow : Bool) -> Expr {
    let bound = KFn(fun(v) = mkExpr(d, s.span, EBlock([
        mkStmt(d, s.span, if isVar { SVar(pat, v) } else { SLet(pat, v) }),
        mkStmt(d, s.span, SExpr(apply(d, after, mkExpr(d, s.span, EUnit)))),
    ])))
    if flow && flowing(s) {
        -- `let found = loop { ... break v }`: the value comes out of the loop's
        -- `Fall`, and the other three keep travelling.
        sequence(d, expr(d, value, KFall, True, True), bound, s.span)
    } else {
        expr(d, value, bound, False, flow)
    }
}

fun refuseEscapes(d : D, stmts : Array Stmt, from : Int) -> Unit {
    for var j = from; j < len(stmts); j = j + 1 {
        match escapes(ChStmt(stmts[j]), False) {
            Some(found) -> oops(
                "a 'return', 'break' or 'continue' cannot cross a '?' yet: it "
                + "would have to be carried through the monad's 'bind' as a "
                + "value, which is not implemented", found.span)
            None -> {}
        }
    }
}

fun refuseEscapesIn(d : D, e : Expr) -> Unit = match escapes(ChExpr(e), False) {
    Some(found) -> oops(
        "a 'return', 'break' or 'continue' cannot cross a '?' yet: it would "
        + "have to be carried through the monad's 'bind' as a value, which is "
        + "not implemented", found.span)
    None -> {}
}

-- -- expressions --------------------------------------------------------------------

-- `E[e] k`. Hoists each `?` in `e`, left to right.
--
-- `tail` says that `e` *is* the value of the enclosing do-context, so `k` is
-- that context's own continuation rather than "the rest of the block". A lifted
-- `if` or `match` there needs neither the `pure` nor the `bind` that lifting
-- normally costs: its branches already are the tail, so `k` goes straight into
-- them. That is not an optimization the monad laws are being trusted for -- it
-- is the no-auto-`pure` rule, which says the tail of a do block is already the
-- monadic value.
fun expr(d : D, e : Expr, k : Cont, tail : Bool, flow : Bool) -> Expr {
    -- In flow mode a construct with no `?` in it still needs lowering if it
    -- transfers control: the transfer has to become a value even though nothing
    -- here binds.
    if !owns(ChExpr(e)) && !(flow && Option.isSome(escapes(ChExpr(e), False))) {
        return apply(d, k, walkExpr(d, e))
    }
    match e.kind {
        EQuestion(inner, bindFn) ->
            expr(d, inner, KFn(fun(m) = bindWith(d, m, k, e.span, bindFn)),
                 False, False)
        -- A bare block is transparent: its statements belong to the context
        -- around it, and its scope is preserved by `seq`'s own blocks.
        EBlock(stmts) -> seq(d, stmts, 0, k, e.span, False)
        EAnnot(inner, t) -> expr(d, inner, KFn(fun(v) =
            apply(d, k, mkExpr(d, e.span, EAnnot(v, t)))), False, False)
        EField(obj, name) -> expr(d, obj, KFn(fun(o) =
            apply(d, k, mkExpr(d, e.span, EField(o, name)))), False, False)
        EProject(obj, index) -> expr(d, obj, KFn(fun(o) =
            apply(d, k, mkExpr(d, e.span, EProject(o, index)))), False, False)
        EIndex(arr, index, getFn, setFn) -> expr(d, arr, KFn(fun(a) =
            expr(d, index, KFn(fun(i) =
                apply(d, k, mkExpr(d, e.span, EIndex(a, i, getFn, setFn)))),
                False, False)), False, False)
        EUnary(op, operand, fn) -> expr(d, operand, KFn(fun(v) =
            apply(d, k, mkExpr(d, e.span, EUnary(op, v, fn)))), False, False)
        ECall(fn, args) -> expr(d, fn, KFn(fun(f) =
            thread(d, args, 0, Array.new(len(args)), fun(vs) =
                apply(d, k, mkExpr(d, e.span, ECall(f, vs))))), False, False)
        ETuple(elems) -> thread(d, elems, 0, Array.new(len(elems)), fun(vs) =
            apply(d, k, mkExpr(d, e.span, ETuple(vs))))
        EArray(elems) -> thread(d, elems, 0, Array.new(len(elems)), fun(vs) =
            apply(d, k, mkExpr(d, e.span, EArray(vs))))
        ERecord(con, fields) -> {
            let names = Array.new(len(fields))
            let values = Array.new(len(fields))
            for (name, value) in fields {
                Array.push(names, name)
                Array.push(values, value)
            }
            thread(d, values, 0, Array.new(len(values)), fun(vs) =
                apply(d, k, mkExpr(d, e.span, ERecord(con, zipped(names, vs)))))
        }
        EBinary(op, left, right, fn) ->
            binary(d, e.span, op, left, right, fn, k, tail, flow)
        EIf(cond, then, otherwise) ->
            ifExpr(d, e.span, cond, then, otherwise, k, tail, flow)
        EMatch(scrutinee, arms) ->
            matchExpr(d, e.span, scrutinee, arms, k, tail, flow)
        EWhile(_, _) -> loopExpr(d, e, k)
        EForIn(_, _, _, _, _) -> loopExpr(d, e, k)
        EForC(_, _, _, _) -> loopExpr(d, e, k)
        ELoop(_) -> loopExpr(d, e, k)
        EReturn(_) -> oops(
            "'?' cannot appear in the value of a 'return' or 'break' yet", e.span)
        EBreak(_) -> oops(
            "'?' cannot appear in the value of a 'return' or 'break' yet", e.span)
        _ -> oops("'?' is not supported in this expression yet", e.span)
    }
}

-- Left to right, so `g(a?, b?)` binds `a` before `b`.
fun thread(d : D, todo : Array Expr, at : Int, done : Array Expr,
           k : fun(Array Expr) -> Expr) -> Expr {
    if at == len(todo) { return k(done) }
    expr(d, todo[at], KFn(fun(v) =
        thread(d, todo, at + 1, Array.append(done, [v]), k)), False, False)
}

fun zipped(names : Array String, values : Array Expr) -> Array (String, Expr) {
    let out = Array.new(len(names))
    for var i = 0; i < len(names); i = i + 1 {
        Array.push(out, (names[i], values[i]))
    }
    out
}

fun binary(d : D, span : Span, op : String, left : Expr, right : Expr,
           fn : Option Expr, k : Cont, tail : Bool, flow : Bool) -> Expr {
    if (op == "&&" || op == "||") && owns(ChExpr(right)) {
        -- These short-circuit, which no `bind` argument does. Reading them as
        -- the `if` they already mean is what keeps that true: the right operand
        -- stays under a branch instead of being hoisted out of one.
        let other = mkExpr(d, span,
            ECon(if op == "&&" { P.boolFalse } else { P.boolTrue }))
        let then = if op == "&&" { right } else { other }
        let otherwise = if op == "&&" { other } else { right }
        return ifExpr(d, span, left, then, Some(otherwise), k, tail, flow)
    }
    expr(d, left, KFn(fun(l) = expr(d, right, KFn(fun(r) =
        apply(d, k, mkExpr(d, span, EBinary(op, l, r, fn)))), False, False)),
        False, False)
}

fun ifExpr(d : D, span : Span, cond : Expr, then : Expr,
           otherwise : Option Expr, k : Cont, tail : Bool, flow : Bool) -> Expr {
    let lifted = branching(then, flow) || (match otherwise {
        Some(o) -> branching(o, flow)
        None -> False
    })

    if !lifted {
        return expr(d, cond, KFn(fun(c) = {
            -- Rebuilt from what the walk answers, for the reason `context` is:
            -- a branch that *is* a `do` is replaced, not rewritten.
            let t = walkExpr(d, then)
            let o = match otherwise {
                Some(x) -> Some(walkExpr(d, x))
                None -> None
            }
            apply(d, k, mkExpr(d, span, EIf(c, t, o)))
        }), False, False)
    }

    -- In tail position `k` goes into the branches and there is no `bind` at all;
    -- otherwise each branch ends at `pure` and the `if` is bound.
    let inner = if tail { k } else { KPure }
    expr(d, cond, KFn(fun(c) = {
        if !flow {
            -- Outside flow mode a branch becomes a lambda body, so a transfer
            -- in it would change meaning.
            refuseEscapesIn(d, then)
            match otherwise {
                Some(o) -> refuseEscapesIn(d, o)
                None -> {}
            }
        }
        let t = block(d, then, inner, flow)
        let o = match otherwise {
            Some(x) -> block(d, x, inner, flow)
            None -> apply(d, inner, mkExpr(d, span, EUnit))
        }
        let built = mkExpr(d, span, EIf(c, t, Some(o)))
        if tail { built } else { bindWith(d, built, k, span, None) }
    }), False, False)
}

fun matchExpr(d : D, span : Span, scrutinee : Expr, arms : Array Arm,
              k : Cont, tail : Bool, flow : Bool) -> Expr {
    var lifted = False
    for a in arms {
        if branching(a.body, flow) { lifted = True }
    }

    if !lifted {
        return expr(d, scrutinee, KFn(fun(s) = {
            for a in arms { a.body = walkExpr(d, a.body) }
            apply(d, k, mkExpr(d, span, EMatch(s, arms)))
        }), False, False)
    }

    let inner = if tail { k } else { KPure }
    expr(d, scrutinee, KFn(fun(s) = {
        let built = Array.new(len(arms))
        for a in arms {
            if !flow { refuseEscapesIn(d, a.body) }
            Array.push(built, Arm {
                span = a.span, patterns = a.patterns,
                body = block(d, a.body, inner, flow),
            })
        }
        let made = mkExpr(d, span, EMatch(s, built))
        if tail { made } else { bindWith(d, made, k, span, None) }
    }), False, False)
}

-- -- the two calls the lowering makes ------------------------------------------------

fun bindWith(d : D, m : Expr, k : Cont, span : Span, fn : Option Expr) -> Expr {
    let name = fresh(d, "%k")
    let param = mkPat(d, span, PVar(name))
    let read = mkExpr(d, span, EVar(name, False))
    let lam = mkExpr(d, span, ELambda([param], None, apply(d, k, read)))
    let f = match fn {
        Some(x) -> x
        None -> methodRef(d, span, P.monadBind)
    }
    mkExpr(d, span, ECall(f, [m, lam]))
}

fun pureOf(d : D, value : Expr) -> Expr =
    mkExpr(d, value.span,
           ECall(methodRef(d, value.span, P.monadPure), [value]))

-- The flow-mode tail: fell off the end of the block with this value.
fun fallOf(d : D, value : Expr) -> Expr =
    pureOf(d, conOf(d, P.flowFall, [value], value.span))

fun methodRef(d : D, span : Span, name : String) -> Expr =
    mkExpr(d, span, EVar(Map.getOr(d.methods, name, name), True))

-- A `Flow` or `Option` constructor. Nullary ones are values, not calls.
fun conOf(d : D, name : String, args : Array Expr, span : Span) -> Expr {
    let made = mkExpr(d, span, ECon(name))
    if len(args) == 0 { made } else { mkExpr(d, span, ECall(made, args)) }
}

fun varOf(d : D, name : String, span : Span) -> Expr =
    mkExpr(d, span, EVar(name, False))

-- -- flow mode: control transfers as values (SPEC-DELTAS.md 47) ---------------------

fun isReturn(e : Expr) -> Bool = match e.kind {
    EReturn(_) -> True
    _ -> False
}

-- `return e` / `break e` / `continue`, as the value that carries it.
fun transfer(d : D, e : Expr) -> Expr = match e.kind {
    EContinue -> pureOf(d, conOf(d, P.flowCont, Array.new(0), e.span))
    EReturn(value) -> transferWith(d, P.flowRet, value, e.span)
    EBreak(value) -> transferWith(d, P.flowBrk, value, e.span)
    _ -> oops("not a control transfer", e.span)
}

fun transferWith(d : D, con : String, value : Option Expr, span : Span) -> Expr =
    match value {
        None -> pureOf(d, conOf(d, con, [mkExpr(d, span, EUnit)], span))
        Some(v) -> expr(d, v, KFn(fun(vv) =
            pureOf(d, conOf(d, con, [vv], span))), False, False)
    }

-- Run `after` only if `head` fell through; carry the rest outward.
--
-- `after` receives the value `head` fell through *with*, which is how
-- `let found = loop { ... break v }` gets its `v`: a lifted loop's `Fall`
-- carries the value it broke with.
--
-- The three propagating arms rebuild rather than passing on the value they
-- matched, because the `Flow` `head` answers with and the one this answers with
-- differ in their first parameter -- `head`'s own value type is not the rest of
-- the block's.
fun sequence(d : D, head : Expr, after : Cont, span : Span) -> Expr {
    let fell = fresh(d, "%fell")
    bindWith(d, head, KFn(fun(f) = mkExpr(d, span, EMatch(f, [
        armOf(d, P.flowFall, Some(fell), span,
              apply(d, after, varOf(d, fell, span))),
        armOf(d, P.flowBrk, Some("%b"), span,
              pureOf(d, conOf(d, P.flowBrk, [varOf(d, "%b", span)], span))),
        armOf(d, P.flowCont, None, span,
              pureOf(d, conOf(d, P.flowCont, Array.new(0), span))),
        armOf(d, P.flowRet, Some("%r"), span,
              pureOf(d, conOf(d, P.flowRet, [varOf(d, "%r", span)], span))),
    ]))), span, None)
}

fun armOf(d : D, con : String, bind : Option String, span : Span,
          body : Expr) -> Arm {
    let args = match bind {
        None -> Array.new(0)
        Some(b) -> if b == "%_" { [mkPat(d, span, PWild)] }
                   else { [mkPat(d, span, PVar(b))] }
    }
    Arm {
        span = span,
        patterns = [mkPat(d, span, PCon(con, args))],
        body = body,
    }
}

-- A do-context's boundary: where `Ret` stops being a value again.
--
-- `Brk` and `Cont` cannot arrive -- a `break` outside a loop is refused while
-- this pass runs -- but nothing in the type system knows that, so the arms
-- exist and diverge.
fun unflow(d : D, body : Expr, span : Span) -> Expr {
    let unreachable = mkExpr(d, span, ECall(
        mkExpr(d, span, EVar(P.error, False)),
        [mkExpr(d, span, ELit(LString(
            "internal error: a loop transfer escaped its loop")))]))
    bindWith(d, body, KFn(fun(f) = mkExpr(d, span, EMatch(f, [
        armOf(d, P.flowFall, Some("%v"), span, varOf(d, "%v", span)),
        armOf(d, P.flowRet, Some("%r"), span, varOf(d, "%r", span)),
        armOf(d, P.flowBrk, Some("%_"), span, unreachable),
        armOf(d, P.flowCont, None, span, unreachable),
    ]))), span, None)
}

-- -- lifted loops -------------------------------------------------------------------

-- A loop holding a `?` becomes a recursive local function.
--
-- There is no way around the recursion: a loop's continuation is not known
-- until the body has run, so it cannot be a lambda written once. The function
-- answers with a `Flow`, which is what carries a `break` out and a `return`
-- further out still; `Fall` and `Cont` both mean "go round again", and differ
-- only in where they came from.
--
-- `for x in xs` is expanded to its cursor form here (design.md 6.5) rather than
-- left for inference, because only the expansion has a loop to lift.
fun loopExpr(d : D, e : Expr, k : Cont) -> Expr {
    if !isFall(k) {
        oops("a loop containing a '?' can only be a statement, not a value: "
             + "its result has to carry whether the body broke, continued or "
             + "returned", e.span)
    }
    let span = e.span
    let name = fresh(d, "%loop")
    let call = mkExpr(d, span, ECall(varOf(d, name, span), Array.new(0)))

    d.depth = d.depth + 1
    let bodyFlow = block(d, loopBody(e), KFall, True)
    d.depth = d.depth - 1

    let step = match e.kind {
        EForC(_, _, s, _) -> optStmtArray(s)
        _ -> Array.new(0)
    }
    let again = if len(step) > 0 { wrap(d, step, call, span) } else { call }
    -- `Brk(v)` is the loop's own value, so it becomes this expression's `Fall`;
    -- `Ret` keeps travelling.
    let roundAgain = bindWith(d, bodyFlow, KFn(fun(f) =
        mkExpr(d, span, EMatch(f, [
            armOf(d, P.flowFall, Some("%_"), span, again),
            armOf(d, P.flowCont, None, span, again),
            armOf(d, P.flowBrk, Some("%b"), span, fallOf(d, varOf(d, "%b", span))),
            armOf(d, P.flowRet, Some("%r"), span,
                  pureOf(d, conOf(d, P.flowRet, [varOf(d, "%r", span)], span))),
        ]))), span, None)

    let done = fallOf(d, mkExpr(d, span, EUnit))

    match e.kind {
        -- Only a `break` ever ends it.
        ELoop(_) -> wrap(d, [loopFun(d, name, roundAgain, span)], call, span)
        EForIn(pat, iterable, _, iterFn, nextFn) ->
            forIn(d, pat, iterable, iterFn, nextFn, name, call, roundAgain, span)
        EWhile(cond, _) -> {
            let inner = expr(d, cond, KFn(fun(c) =
                mkExpr(d, span, EIf(c, roundAgain, Some(done)))), False, True)
            wrap(d, [loopFun(d, name, inner, span)], call, span)
        }
        EForC(init, cond, _, _) -> {
            let inner = expr(d, cond, KFn(fun(c) =
                mkExpr(d, span, EIf(c, roundAgain, Some(done)))), False, True)
            wrap(d, Array.append(optStmtArray(init),
                                 [loopFun(d, name, inner, span)]), call, span)
        }
        _ -> oops("not a loop", span)
    }
}

fun loopBody(e : Expr) -> Expr = match e.kind {
    EWhile(_, body) -> body
    EForIn(_, _, body, _, _) -> body
    EForC(_, _, _, body) -> body
    ELoop(body) -> body
    _ -> e
}

fun optStmtArray(s : Option Stmt) -> Array Stmt = match s {
    Some(x) -> [x]
    None -> Array.new(0)
}

-- `fun name() { body }`, as a statement, so the loop can call itself.
--
-- Not generalized. It has one use site -- the call built beside it -- plus its
-- own recursion, so there is nothing for a scheme to be used at more than one
-- type, and generalizing it is what made the lifted loops survive every pass.
-- The arms above only ever build `Fall` and `Ret` at the enclosing block's
-- `Flow`, so the `Brk` slot is uninhabited *by construction*; generalizing left
-- a variable there that nothing could solve, every call site read as ground in
-- the slot that matters and open in the one that cannot be, and `mono`
-- specializes only at ground instantiations. One dead parameter kept the whole
-- loop generic and out of reach of the inliner behind it.
fun loopFun(d : D, name : String, body : Expr, span : Span) -> Stmt =
    mkStmt(d, span, SFun(FunDecl {
        span = span, name = name, params = Array.new(0), ret = None,
        body = Some(body), context = Array.new(0), monomorphic = True,
    }))

-- `for x in xs` in its cursor form, then lifted like any other loop.
fun forIn(d : D, pat : Pat, iterable : Expr, iterFn : Option Expr,
          nextFn : Option Expr, name : String, call : Expr, roundAgain : Expr,
          span : Span) -> Expr {
    let n = fresh(d, "")
    let seqName = "%seq" + n
    let curName = "%cur" + n
    let item = "%item" + n
    let iter = iteration(d, iterFn, span)
    let next = iteration(d, nextFn, span)

    expr(d, iterable, KFn(fun(xs) = {
        let advance = mkExpr(d, span, ECall(next,
            [varOf(d, seqName, span), varOf(d, curName, span)]))
        let body = mkExpr(d, span, EMatch(advance, [
            armOf(d, P.optionNone, None, span,
                  fallOf(d, mkExpr(d, span, EUnit))),
            Arm {
                span = span,
                patterns = [mkPat(d, span, PCon(P.optionSome,
                    [mkPat(d, span, PVar(item))]))],
                body = mkExpr(d, span, EBlock([
                    mkStmt(d, span, SLet(pat, varOf(d, item, span))),
                    mkStmt(d, span, SExpr(roundAgain)),
                ])),
            },
        ]))
        wrap(d, [
            mkStmt(d, span, SLet(mkPat(d, span, PVar(seqName)), xs)),
            mkStmt(d, span, SLet(mkPat(d, span, PVar(curName)),
                mkExpr(d, span, ECall(iter, [varOf(d, seqName, span)])))),
            loopFun(d, name, body, span),
        ], call, span)
    }), False, False)
}

-- A `for` loop's `iter` or `next`, which resolution wrote. Absent only if the
-- module cannot see `Iterator` at all, which inference would refuse anyway.
fun iteration(d : D, ref : Option Expr, span : Span) -> Expr = match ref {
    Some(e) -> e
    None -> oops("this 'for' loop has no 'iter' or 'next' in scope", span)
}

fun wrap(d : D, prefix : Array Stmt, value : Expr, span : Span) -> Expr {
    if len(prefix) == 0 { return value }
    mkExpr(d, span, EBlock(Array.append(prefix,
        [mkStmt(d, value.span, SExpr(value))])))
}

-- -- the transparent walk -----------------------------------------------------------

-- Rewrite the do-contexts nested inside a node, and nothing else.
--
-- This is the path taken by every expression that has no `?` of its own. It
-- rebuilds nothing structural: only the contexts underneath it change, and they
-- are installed where they were found. A `do` is the exception -- it *becomes*
-- the block it wrapped, so the answer is a different node and the caller has to
-- take it.
fun walkExpr(d : D, e : Expr) -> Expr {
    if !sugared(ChExpr(e)) { return e }
    match e.kind {
        EDo(body) -> return context(d, body)
        ELambda(params, ret, body) -> {
            e.kind = ELambda(params, ret, context(d, body))
            return e
        }
        EQuestion(_, _) -> oops(
            "'?' is only meaningful inside a function body or a 'do' block",
            e.span)
        ELit(_) -> {}
        EUnit -> {}
        EVar(_, _) -> {}
        ECon(_) -> {}
        EContinue -> {}
        ETuple(xs) -> { e.kind = ETuple(walkAll(d, xs)) }
        EArray(xs) -> { e.kind = EArray(walkAll(d, xs)) }
        ERecord(con, fields) -> { e.kind = ERecord(con, walkFields(d, fields)) }
        ECall(fn, args) -> { e.kind = ECall(walkExpr(d, fn), walkAll(d, args)) }
        EIndex(arr, index, getFn, setFn) -> {
            e.kind = EIndex(walkExpr(d, arr), walkExpr(d, index), getFn, setFn)
        }
        EField(obj, name) -> { e.kind = EField(walkExpr(d, obj), name) }
        EProject(obj, index) -> { e.kind = EProject(walkExpr(d, obj), index) }
        EUnary(op, operand, fn) -> {
            e.kind = EUnary(op, walkExpr(d, operand), fn)
        }
        EBinary(op, left, right, fn) -> {
            e.kind = EBinary(op, walkExpr(d, left), walkExpr(d, right), fn)
        }
        EAnnot(inner, t) -> { e.kind = EAnnot(walkExpr(d, inner), t) }
        EIf(cond, then, otherwise) -> {
            e.kind = EIf(walkExpr(d, cond), walkExpr(d, then),
                         walkOpt(d, otherwise))
        }
        EWhile(cond, body) -> {
            e.kind = EWhile(walkExpr(d, cond), walkExpr(d, body))
        }
        EForIn(pat, iterable, body, iterFn, nextFn) -> {
            e.kind = EForIn(pat, walkExpr(d, iterable), walkExpr(d, body),
                            iterFn, nextFn)
        }
        EForC(init, cond, step, body) -> {
            e.kind = EForC(walkOptStmt(d, init), walkExpr(d, cond),
                           walkOptStmt(d, step), walkExpr(d, body))
        }
        ELoop(body) -> { e.kind = ELoop(walkExpr(d, body)) }
        EMatch(scrutinee, arms) -> {
            for a in arms { a.body = walkExpr(d, a.body) }
            e.kind = EMatch(walkExpr(d, scrutinee), arms)
        }
        EReturn(value) -> { e.kind = EReturn(walkOpt(d, value)) }
        EBreak(value) -> { e.kind = EBreak(walkOpt(d, value)) }
        EBlock(stmts) -> for var i = 0; i < len(stmts); i = i + 1 {
            stmts[i] = walkStmt(d, stmts[i])
        }
    }
    e
}

fun walkStmt(d : D, s : Stmt) -> Stmt {
    if !sugared(ChStmt(s)) { return s }
    match s.kind {
        SFun(decl) -> funBody(d, decl)
        SLet(pat, value) -> { s.kind = SLet(pat, walkExpr(d, value)) }
        SVar(pat, value) -> { s.kind = SVar(pat, walkExpr(d, value)) }
        SAssign(target, value) -> {
            s.kind = SAssign(walkExpr(d, target), walkExpr(d, value))
        }
        SExpr(e) -> { s.kind = SExpr(walkExpr(d, e)) }
    }
    s
}

fun walkAll(d : D, xs : Array Expr) -> Array Expr =
    Array.map(xs, fun(x) = walkExpr(d, x))

fun walkOpt(d : D, e : Option Expr) -> Option Expr = match e {
    Some(x) -> Some(walkExpr(d, x))
    None -> None
}

fun walkOptStmt(d : D, s : Option Stmt) -> Option Stmt = match s {
    Some(x) -> Some(walkStmt(d, x))
    None -> None
}

fun walkFields(d : D, fields : Array (String, Expr)) -> Array (String, Expr) {
    let out = Array.new(len(fields))
    for (name, value) in fields { Array.push(out, (name, walkExpr(d, value))) }
    out
}

-- -- brackets -----------------------------------------------------------------------

-- Turn bracket reads and writes into the resolved `Index` method calls they
-- mean. A separate walk over the whole program rather than part of the one
-- above, because it applies everywhere and not only where a `?` reaches.
--
-- Both rewrites keep the node's own span, so each is done in place: `xs[i]`
-- becomes the `get` call at the same place, and `xs[i] = v` becomes a `set`
-- call statement at the statement's place. A missing reference stops the walk
-- exactly where the Python's does -- the module cannot see `Index`, which
-- inference is about to say.
fun desugarIndexes(d : D, p : Program) -> Unit {
    for decl in p.decls {
        match decl {
            DStmt(s) -> indexStmt(d, s)
            DClass(c) -> for m in c.methods { indexFun(d, m) }
            DInstance(i) -> for m in i.methods { indexFun(d, m) }
            DType(_) -> {}
        }
    }
}

fun indexFun(d : D, f : FunDecl) -> Unit = match f.body {
    Some(b) -> indexExpr(d, b)
    None -> {}
}

fun indexStmt(d : D, s : Stmt) -> Unit = match s.kind {
    SAssign(target, value) -> match target.kind {
        EIndex(arr, index, _, setFn) -> match setFn {
            None -> {}
            Some(set) -> {
                indexExpr(d, arr)
                indexExpr(d, index)
                indexExpr(d, value)
                s.kind = SExpr(mkExpr(d, s.span,
                    ECall(set, [arr, index, value])))
            }
        }
        _ -> {
            indexExpr(d, target)
            indexExpr(d, value)
        }
    }
    SLet(_, value) -> indexExpr(d, value)
    SVar(_, value) -> indexExpr(d, value)
    SFun(f) -> indexFun(d, f)
    SExpr(e) -> indexExpr(d, e)
}

fun indexExpr(d : D, e : Expr) -> Unit = match e.kind {
    EIndex(arr, index, getFn, _) -> match getFn {
        None -> {}
        Some(get) -> {
            indexExpr(d, arr)
            indexExpr(d, index)
            e.kind = ECall(get, [arr, index])
        }
    }
    ELit(_) -> {}
    EUnit -> {}
    EVar(_, _) -> {}
    ECon(_) -> {}
    EContinue -> {}
    ETuple(xs) -> indexAll(d, xs)
    EArray(xs) -> indexAll(d, xs)
    ERecord(_, fields) -> for (_, value) in fields { indexExpr(d, value) }
    ELambda(_, _, body) -> indexExpr(d, body)
    ECall(fn, args) -> {
        indexExpr(d, fn)
        indexAll(d, args)
    }
    EField(obj, _) -> indexExpr(d, obj)
    EProject(obj, _) -> indexExpr(d, obj)
    EUnary(_, operand, _) -> indexExpr(d, operand)
    EBinary(_, left, right, _) -> {
        indexExpr(d, left)
        indexExpr(d, right)
    }
    EAnnot(inner, _) -> indexExpr(d, inner)
    EIf(cond, then, otherwise) -> {
        indexExpr(d, cond)
        indexExpr(d, then)
        indexOpt(d, otherwise)
    }
    EWhile(cond, body) -> {
        indexExpr(d, cond)
        indexExpr(d, body)
    }
    EForIn(_, iterable, body, _, _) -> {
        indexExpr(d, iterable)
        indexExpr(d, body)
    }
    EForC(init, cond, step, body) -> {
        indexOptStmt(d, init)
        indexExpr(d, cond)
        indexOptStmt(d, step)
        indexExpr(d, body)
    }
    ELoop(body) -> indexExpr(d, body)
    EMatch(scrutinee, arms) -> {
        indexExpr(d, scrutinee)
        for a in arms { indexExpr(d, a.body) }
    }
    EReturn(value) -> indexOpt(d, value)
    EBreak(value) -> indexOpt(d, value)
    EBlock(stmts) -> for s in stmts { indexStmt(d, s) }
    EQuestion(inner, _) -> indexExpr(d, inner)
    EDo(body) -> indexExpr(d, body)
}

fun indexAll(d : D, xs : Array Expr) -> Unit {
    for x in xs { indexExpr(d, x) }
}

fun indexOpt(d : D, e : Option Expr) -> Unit = match e {
    Some(x) -> indexExpr(d, x)
    None -> {}
}

fun indexOptStmt(d : D, s : Option Stmt) -> Unit = match s {
    Some(x) -> indexStmt(d, x)
    None -> {}
}

-- -- diagnostics --------------------------------------------------------------------

fun oops(message : String, where : Span) -> a =
    Diag.fail(Diag.at("unsupported", message, where), where.file)

fun discard(x : a) -> Unit = {}
