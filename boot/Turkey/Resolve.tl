module Turkey.Resolve (program)

-- Name resolution across modules (design.md 9, `turkey/resolve.py`).
--
-- Every stage after this one keys on a flat string. So a module system needs
-- either one set of those tables per module, threaded through every stage, or
-- one set and names that are unique across the program. This takes the second
-- road: a module's top-level bindings are renamed to `Module#name`, and every
-- reference to one is rewritten to match. Nothing downstream learns that
-- modules exist.
--
-- Types and constructors go the same way, in their own namespaces, which is
-- what lets two libraries that each declare a `Node` be used in one program:
-- the two are different constructors, and unification compares names.
--
-- Two kinds of name are deliberately left alone.
--
-- * **Locals.** A parameter, a `let`, a pattern variable -- these never leave
--   the scope that binds them, so they need no qualification, and the frames
--   below track scopes precisely enough to tell one from a top-level
--   reference.
-- * **Operators.** `a + b` became a plain `add` reference at parse time, and
--   once shadowing an import is legal that node would resolve to whatever `add`
--   the module happens to define. The parser marks the node, and the marked
--   ones are looked up in the separate protocol-method scope -- so `+` still
--   means the imported `Add.add` in a module whose own `add` is unrelated.
--
-- A qualified name whose module part is not in scope is reported here, because
-- this is the only stage that knows what "in scope" means. An *unqualified*
-- name that is not found is left alone for inference to report, so that the
-- existing wording and span survive.

import Data.Set as Set
import Turkey.Ast
import Turkey.Diag as Diag
import Turkey.Modules (Scope(..))
import Turkey.Source (Span)

-- One module's scope, plus the stack of local frames the walk maintains.
type R = R {
    scope  : Scope,
    -- Innermost last. A name in any frame is local and is left alone.
    locals : Array (Set.Set String),
}

fun program(scope : Scope, p : Program) -> Unit {
    let r = R { scope = scope, locals = Array.new(4) }
    for decl in p.decls {
        match decl {
            DStmt(s) -> topLevel(r, s)
            DType(t) -> typeDecl(r, t)
            DClass(c) -> classDecl(r, c)
            DInstance(i) -> instanceDecl(r, i)
        }
    }
}

-- -- declarations -------------------------------------------------------------------

fun typeDecl(r : R, d : TypeDecl) -> Unit {
    d.name = tycon(r, d.name)
    match d.alias {
        Some(a) -> typeExpr(r, a)
        None -> {}
    }
    match d.variants {
        None -> {}
        Some(vs) -> for v in vs {
            v.name = con(r, v.name)
            for a in v.args { typeExpr(r, a) }
            match v.fields {
                Some(fs) -> for (_, te) in fs { typeExpr(r, te) }
                None -> {}
            }
        }
    }
}

fun classDecl(r : R, d : ClassDecl) -> Unit {
    d.name = cls(r, d.name)
    context(r, d.supers)
    for meth in d.methods {
        meth.name = d.name + "." + meth.name
        funDecl(r, meth)
    }
    for fam in d.families { fam.name = d.name + "." + fam.name }
}

fun instanceDecl(r : R, d : InstanceDecl) -> Unit {
    d.cls = cls(r, d.cls)
    typeExpr(r, d.head)
    context(r, d.context)
    for bind in d.families {
        bind.name = tycon(r, bind.name)
        typeExpr(r, bind.body)
    }
    for meth in d.methods { funDecl(r, meth) }
}

-- A top-level item: its binders are renamed, its body resolved.
fun topLevel(r : R, s : Stmt) -> Unit = match s.kind {
    SFun(d) -> {
        d.name = lookupOr(r.scope.values, d.name)
        funDecl(r, d)
    }
    SLet(pat, value) -> topLevelBinding(r, pat, value)
    SVar(pat, value) -> topLevelBinding(r, pat, value)
    _ -> {
        -- An assignment or a bare expression at the top level binds nothing, so
        -- there is nothing to rename -- only to resolve.
        push(r)
        stmt(r, s)
        pop(r)
    }
}

fun topLevelBinding(r : R, pat : Pat, value : Expr) -> Unit {
    expr(r, value)
    pattern(r, pat)
    renamePattern(pat, r.scope.values)
}

fun funDecl(r : R, d : FunDecl) -> Unit {
    context(r, d.context)
    for param in d.params { pattern(r, param) }
    match d.ret {
        Some(t) -> typeExpr(r, t)
        None -> {}
    }
    pushWith(r, paramVars(d.params))
    match d.body {
        Some(b) -> expr(r, b)
        None -> {}
    }
    pop(r)
}

-- -- types, predicates and patterns ---------------------------------------------------

fun typeExpr(r : R, t : Type) -> Unit = match t.kind {
    -- A type variable is scoped to its own declaration and names nothing here.
    TEVar(_) -> {}
    TECon(name, args) -> {
        t.kind = TECon(tycon(r, name), args)
        for a in args { typeExpr(r, a) }
    }
    TEApp(head, args) -> {
        typeExpr(r, head)
        for a in args { typeExpr(r, a) }
    }
    TETuple(elems) -> for e in elems { typeExpr(r, e) }
    TEFun(params, ret) -> {
        for p in params { typeExpr(r, p) }
        typeExpr(r, ret)
    }
}

-- A superclass list, a function's context or an instance's -- the same node.
fun context(r : R, preds : Array Pred) -> Unit {
    for pred in preds {
        match pred.kind {
            PClass(name, arg) -> {
                pred.kind = PClass(cls(r, name), arg)
                typeExpr(r, arg)
            }
            PEq(left, right) -> {
                typeExpr(r, left)
                typeExpr(r, right)
            }
        }
    }
}

-- Rename the constructors a pattern mentions. Its *variables* are binders, and
-- `patternVars` collects them separately.
fun pattern(r : R, p : Pat) -> Unit = match p.kind {
    PCon(name, args) -> {
        p.kind = PCon(con(r, name), args)
        for a in args { pattern(r, a) }
    }
    PRecord(name, fields) -> {
        p.kind = PRecord(con(r, name), fields)
        for (_, sub) in fields { pattern(r, sub) }
    }
    PTuple(elems) -> for e in elems { pattern(r, e) }
    PAnnot(inner, t) -> {
        pattern(r, inner)
        typeExpr(r, t)
    }
    _ -> {}
}

-- Rewrite a top-level binder's variables to their internal names.
fun renamePattern(p : Pat, scope : Map String String) -> Unit = match p.kind {
    PVar(name) -> { p.kind = PVar(Map.getOr(scope, name, name)) }
    PCon(_, args) -> for a in args { renamePattern(a, scope) }
    PTuple(elems) -> for e in elems { renamePattern(e, scope) }
    PRecord(_, fields) -> for (_, sub) in fields { renamePattern(sub, scope) }
    PAnnot(inner, _) -> renamePattern(inner, scope)
    _ -> {}
}

-- -- expressions --------------------------------------------------------------------

fun expr(r : R, e : Expr) -> Unit = match e.kind {
    EVar(name, isMethod) -> {
        -- A marked node is an operator or a `for` loop's `iter`/`next`, written
        -- by the parser: it means the class method by construction.
        if isMethod {
            match Map.get(r.scope.methods, name) {
                Some(target) -> { e.kind = EVar(target, True) }
                None -> {}
            }
        } else if !isLocal(r, name) {
            match Map.get(r.scope.values, name) {
                Some(target) -> { e.kind = EVar(target, False) }
                None -> if String.contains(name, ".") {
                    -- Qualified, and its module is not in scope. Inference
                    -- would find it anyway -- `Prim.intAdd` is in the shared
                    -- environment so the Prelude can be checked -- so this is
                    -- the stage that has to say no.
                    oops("'" + name + "' is not defined", e.span)
                }
            }
        }
    }
    ECon(name) -> { e.kind = ECon(con(r, name)) }
    ELit(_) -> {}
    EUnit -> {}
    EContinue -> {}
    ETuple(elems) -> for x in elems { expr(r, x) }
    EArray(elems) -> for x in elems { expr(r, x) }
    ERecord(name, fields) -> {
        e.kind = ERecord(con(r, name), fields)
        for (_, value) in fields { expr(r, value) }
    }
    ECall(fn, args) -> {
        expr(r, fn)
        for a in args { expr(r, a) }
    }
    EIndex(arr, index, _, _) -> {
        expr(r, arr)
        expr(r, index)
        -- `xs[i]` and `xs[i] = v` are `Index`'s `get` and `set`. Desugaring
        -- turns them into those calls; resolution is where the two references
        -- are settled, since only it knows the module's method scope.
        e.kind = EIndex(arr, index, methodRef(r, "get", e.span),
                        methodRef(r, "set", e.span))
    }
    EField(obj, _) -> expr(r, obj)
    EProject(obj, _) -> expr(r, obj)
    EUnary(_, operand, fn) -> {
        resolveMethod(r, fn)
        expr(r, operand)
    }
    EBinary(_, left, right, fn) -> {
        resolveMethod(r, fn)
        expr(r, left)
        expr(r, right)
    }
    EAnnot(inner, t) -> {
        expr(r, inner)
        typeExpr(r, t)
    }
    EIf(cond, then, otherwise) -> {
        expr(r, cond)
        expr(r, then)
        match otherwise {
            Some(o) -> expr(r, o)
            None -> {}
        }
    }
    EWhile(cond, body) -> {
        expr(r, cond)
        expr(r, body)
    }
    ELoop(body) -> expr(r, body)
    EReturn(value) -> match value {
        Some(v) -> expr(r, v)
        None -> {}
    }
    EBreak(value) -> match value {
        Some(v) -> expr(r, v)
        None -> {}
    }
    ELambda(params, ret, body) -> {
        for p in params { pattern(r, p) }
        match ret {
            Some(t) -> typeExpr(r, t)
            None -> {}
        }
        pushWith(r, paramVars(params))
        expr(r, body)
        pop(r)
    }
    EMatch(scrutinee, arms) -> {
        expr(r, scrutinee)
        for arm in arms {
            let bound = Array.new(4)
            for p in arm.patterns {
                pattern(r, p)
                for v in patternVars(p) { Array.push(bound, v) }
            }
            pushWith(r, bound)
            expr(r, arm.body)
            pop(r)
        }
    }
    EForIn(pat, iterable, body, iterFn, nextFn) -> {
        resolveMethod(r, iterFn)
        resolveMethod(r, nextFn)
        expr(r, iterable)
        pattern(r, pat)
        pushWith(r, patternVars(pat))
        expr(r, body)
        pop(r)
    }
    EForC(init, cond, step, body) -> {
        -- init, cond, step and body share one scope.
        push(r)
        match init {
            Some(s) -> stmt(r, s)
            None -> {}
        }
        expr(r, cond)
        match step {
            Some(s) -> stmt(r, s)
            None -> {}
        }
        expr(r, body)
        pop(r)
    }
    EBlock(stmts) -> {
        push(r)
        for s in stmts { stmt(r, s) }
        pop(r)
    }
    EQuestion(inner, bindFn) -> {
        resolveMethod(r, bindFn)
        expr(r, inner)
    }
    -- Only a marker; the block underneath scopes as any block does.
    EDo(body) -> expr(r, body)
}

fun stmt(r : R, s : Stmt) -> Unit = match s.kind {
    SLet(pat, value) -> localBinding(r, pat, value)
    SVar(pat, value) -> localBinding(r, pat, value)
    SFun(d) -> {
        -- In scope for its own body, so it may recurse.
        bind(r, [d.name])
        funDecl(r, d)
    }
    SAssign(target, value) -> {
        expr(r, target)
        expr(r, value)
    }
    SExpr(e) -> expr(r, e)
}

fun localBinding(r : R, pat : Pat, value : Expr) -> Unit {
    expr(r, value)
    pattern(r, pat)
    bind(r, patternVars(pat))
}

-- -- namespaces ---------------------------------------------------------------------

fun tycon(r : R, name : String) -> String = lookupOr(r.scope.types, name)
fun con(r : R, name : String) -> String = lookupOr(r.scope.cons, name)
fun cls(r : R, name : String) -> String = lookupOr(r.scope.classes, name)

fun lookupOr(space : Map String String, name : String) -> String =
    Map.getOr(space, name, name)

-- A method reference the parser wrote: `EBinary`'s `fn`, `EForIn`'s `iter`.
-- Absent when the operator has no class, which `!` and `&&` do not.
fun resolveMethod(r : R, ref : Option Expr) -> Unit = match ref {
    None -> {}
    Some(e) -> match e.kind {
        EVar(name, _) -> match Map.get(r.scope.methods, name) {
            Some(target) -> { e.kind = EVar(target, True) }
            None -> {}
        }
        _ -> {}
    }
}

-- A method reference *this* stage writes, for a node the parser left bare.
-- `None` when the module cannot see the method at all, which leaves the
-- bracket unlowered for inference to complain about.
fun methodRef(r : R, name : String, where : Span) -> Option Expr =
    match Map.get(r.scope.methods, name) {
        None -> None
        Some(target) -> Some(Expr {
            id = 0, span = where, kind = EVar(target, True),
        })
    }

-- -- local frames -------------------------------------------------------------------

fun isLocal(r : R, name : String) -> Bool {
    for frame in r.locals {
        if Set.contains(frame, name) { return True }
    }
    False
}

fun push(r : R) -> Unit = Array.push(r.locals, Set.new())

fun pushWith(r : R, names : Array String) -> Unit {
    push(r)
    bind(r, names)
}

fun pop(r : R) -> Unit = discard(Array.pop(r.locals))

fun bind(r : R, names : Array String) -> Unit = match Array.last(r.locals) {
    Some(frame) -> for n in names { Set.add(frame, n) }
    None -> {}
}

fun paramVars(params : Array Pat) -> Array String {
    let out = Array.new(4)
    for p in params {
        for v in patternVars(p) { Array.push(out, v) }
    }
    out
}

fun oops(message : String, where : Span) -> a =
    Diag.fail(Diag.at("type error", message, where), where.file)

fun discard(x : a) -> Unit = {}
