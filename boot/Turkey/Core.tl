module Turkey.Core (
    refName, refOf, isRef, refElem,
    Core(..), CoreKind(..), CParam(..), CAlt(..), CBind(..), CProgram(..),
    mkCore, tailParts, tailFieldNames,
    Mapper(..), mapKind,
    namesOf, namesInto,
    Aliases(..), newAliases, alias,
    showExpr, showBind, showProgram,
)

-- The typed Core IR (`plan.txt` item 5, `turkey/core.py`).
--
-- This module holds the shape and a printer, and no construction logic at all:
-- `Turkey.Lower` builds it and `Turkey.Coretc` checks it, so what is written
-- here can be read as a specification rather than as the incidental output of a
-- pass.
--
-- ## What Core makes explicit, and what it leaves alone
--
-- Three things, and only three. **Types**: every node carries the one inference
-- gave it. **Dictionaries**: a class becomes a record type, an instance becomes a
-- top-level binding of that type, and evidence becomes ordinary term syntax -- a
-- variable, an application, a field projection. **Polymorphism**: a generalized
-- binding becomes a type abstraction and every use of it a type application.
--
-- Everything else keeps the shape it had, with one exception. `CIf` and `CMatch`
-- are the surface constructs they always were, but there is no node here for a
-- loop and none for a control transfer: `while`, `loop`, the C-style `for` and
-- `for ... in` are all `CJoin`, and `return`, `break` and `continue` are all
-- `CJump`. That is what "unrepresentable" means -- not that no pass emits one,
-- but that there is nothing to emit.
--
-- ## The one thing that is not a translation
--
-- Core has **no statements**. A block is nested `CLet`s, and the value of a
-- block is the body of the innermost one; sequencing is a `CLet` whose name
-- nothing reads. This is what lets the checker be a walk with one rule per node
-- instead of a walk plus a separate statement discipline, and a checker simple
-- enough to trust is the entire point of the milestone.
--
-- ## `var` is a reference cell
--
-- `let` is an immutable binding and is a plain `CLet`. `var` is mutable, and it
-- gets *capture by reference*: a lambda that writes a captured `var` writes
-- through to the original. So a `var` lowers to a cell -- `CRef` makes one,
-- `CDeref` reads it, `CAssign` writes it, and its type is `%Ref t`. Capture is
-- then by value of a reference, stated in the IR where a checker can see it
-- rather than left to the representation of a scope.
--
-- The node shape is `Turkey.Ast`'s: a record carrying the type and the span,
-- around an immutable variant carrying the form. Python puts the two on a base
-- class; there is no inheritance here, and repeating them in thirty variants
-- would be worse than either.

import Data.Set as Set
import Turkey.Ast
import Turkey.Ast as Ast
import Turkey.Diag as Diag
import Turkey.Source (Span)
import Turkey.Types as Ty
import Turkey.Types (Kind(..), VarCell(..), Namer(..), TVar, TCon, TApp)

-- The reference-cell constructor. `%` cannot start a source identifier, so this
-- can never collide with a declared type, and no program can write it.
let refName = "%Ref"

fun refCon() -> Ty.Type = Ty.tcon(refName, KFun(KStar, KStar), Ty.noScope)

fun refOf(t : Ty.Type) -> Ty.Type = TApp(refCon(), t, KStar)

fun isRef(t : Ty.Type) -> Bool = match Ty.prune(t) {
    TApp(fn, _, _) -> match Ty.prune(fn) {
        TCon(c) -> c.name == refName
        _ -> False
    }
    _ -> False
}

fun refElem(t : Ty.Type) -> Ty.Type = match Ty.prune(t) {
    TApp(_, arg, _) -> arg
    _ -> oops("not a reference cell: " + Ty.show(t))
}

-- -- terms --------------------------------------------------------------------------

-- Every Core node carries its type. That is what "typed" means here.
type Core = Core {
    ty   : Ty.Type,
    span : Option Span,
    kind : CoreKind,
}

fun mkCore(ty : Ty.Type, span : Option Span, kind : CoreKind) -> Core =
    Core { ty = ty, span = span, kind = kind }

type CParam = CParam {
    name : String,
    ty   : Ty.Type,
}

-- One arm. What its pattern *binds*, and at what types, is deliberately not
-- recorded: the checker derives it from the scrutinee's type and the
-- constructor's declaration, which makes it a check rather than a restatement.
type CAlt = CAlt {
    pat  : Pat,
    body : Core,
}

type CoreKind
    = CLit(Ast.Lit)
    | CUnit
    -- A term variable: a local, a top-level binding, or a dictionary. A
    -- dictionary is not a separate form -- that is the milestone's whole claim:
    -- evidence became a value, so it is fetched the way any other value is.
    | CVar(String)
    -- A data constructor, referred to but not yet applied.
    | CCon(String)
    -- A builtin, named as the primitive table names it.
    | CPrim(String)
    | CTuple(Array Core)
    | CArray(Array Core)
    -- A record built by naming its fields, which is how a dictionary is built.
    | CRecord(String, Array (String, Core))
    -- A field projection. A method selection and a superclass selection are both
    -- this, which is the point: they were two shapes of evidence and are one
    -- shape of term.
    | CField(Core, String)
    | CProject(Core, Int)
    | CIndex(Core, Core)
    | CLam(Array CParam, Core, String)
    | CApp(Core, Array Core)
    -- Type abstraction. The binders are the scheme's own quantified variables,
    -- used as binders directly -- a variable is already a distinct identity, and
    -- reusing the scheme's means the type arguments a use site recorded line up
    -- with no separate agreement to keep.
    | CTyLam(Array VarCell, Core)
    | CTyApp(Core, Array Ty.Type)
    -- `let name : ty = value in body`. The only sequencing form there is. The
    -- name may be one nothing reads, which is how a statement whose value is
    -- discarded is expressed. A `let` generalizes, so a local binding may be
    -- polymorphic and its uses may be type applications.
    | CLet(String, Ty.Type, Core, Core, Array VarCell)
    -- A binding group that may refer to itself and to its siblings. One form for
    -- the whole group rather than one per name, because that is what a
    -- strongly-connected component is and what the dictionary abstraction is
    -- shared over.
    | CLetRec(Array CBind, Core)
    -- `join j(params) = body in rest` -- a label, not a function.
    --
    -- A join point is a `let`-bound continuation that is only ever *jumped* to:
    -- every mention is a saturated call in tail position, and none of them
    -- escapes. That is what makes it compilable as a label rather than as a
    -- closure. It is a distinct node rather than a flag, and a jump is a
    -- distinct node rather than a call, because the restriction is the point: a
    -- walker that has not been taught about these fails loudly, where one that
    -- has not been taught about a flag is silently wrong.
    --
    -- The `Bool` says whether the body may jump to the join itself -- a loop
    -- compiled as a label. A non-recursive join is the case-of-case
    -- duplication: one continuation, several branches reaching it.
    | CJoin(String, Array CParam, Core, Core, Bool)
    -- `jump j(args)`. Typed `!`: it never yields to its context, which is why it
    -- needs no type of its own beyond bottom, and why a branch that jumps stays
    -- compatible with a branch that does not.
    | CJump(String, Array Core)
    -- Make a reference cell. What a `var` binding is.
    | CRef(Core)
    -- Read a reference cell. What a mention of a `var` is.
    | CDeref(Core)
    -- Write a reference cell, an array slot, or a mutable record field. All
    -- three are assignment in the surface language and all three are reference
    -- semantics, so they are one node with three shapes of target.
    | CAssign(Core, Core)
    | CIf(Core, Core, Option Core)
    -- Pattern matching, over surface patterns unchanged. They are already a
    -- small, total, type-directed language with no implicit anything, and
    -- exhaustiveness has decided about them before this point.
    | CMatch(Core, Array CAlt)

-- One top-level binding, with the type variables it abstracts over.
--
-- The binders and the dictionary parameters are separate because they are
-- abstracted at different times and by different rules: the type variables come
-- from generalization, the dictionary parameters from the predicates the scheme
-- retained.
type CBind = CBind {
    name      : String,
    ty        : Ty.Type,
    binders   : Array VarCell,
    value     : Core,
    span      : Option Span,
    mutable   : Bool,
    -- Which module declared it. Only the `core` dump reads this, for the reason
    -- `types` prints one module's signatures: a whole program's Core is mostly
    -- the Prelude's.
    module_   : String,
    -- The equalities the binding's context states: `Item s ~ Op`. They are not
    -- evidence -- nothing is passed for them, and they are erased at run time --
    -- but they are *facts the body was checked under*, and a family over a rigid
    -- variable does not reduce without them.
    equations : Array (Ty.Type, Ty.Type),
}

-- A whole program: the dictionaries, then the bindings, in run order.
type CProgram = CProgram {
    dicts : Array CBind,
    binds : Array CBind,
}

-- Which subterms are **tail positions**: the places a jump may stand, because
-- the value of what stands there is the value of the whole enclosing term.
-- Stated once, here in the IR, because it is a fact about the IR -- the checker
-- reads it to decide what a join scope reaches, and join discovery reads it to
-- decide what a tail call is, and two statements of one fact is exactly the kind
-- of agreement item 5 exists to be rid of.
--
-- Everything absent is not a tail position, and the one absence that carries
-- weight is a lambda's body: a closure outlives the frame that binds the join,
-- so a label is not something it can jump to. There is no loop body to say the
-- same of any more -- a loop *is* a join here.
-- The same fact, by field name, for the checker: it asks "does this subterm
-- inherit the join scope" rather than "what are the tail subterms". One
-- statement, read two ways, because two statements is what item 5 exists to be
-- rid of.
fun tailFieldNames(k : CoreKind) -> Array String = match k {
    CIf(_, _, _) -> ["then", "otherwise"]
    CLet(_, _, _, _, _) -> ["body"]
    CLetRec(_, _) -> ["body"]
    CJoin(_, _, _, _, _) -> ["body", "rest"]
    CMatch(_, _) -> ["alts"]
    _ -> Array.new(0)
}

fun tailParts(k : CoreKind) -> Array Core = match k {
    CIf(_, then, otherwise) -> match otherwise {
        Some(o) -> [then, o]
        None -> [then]
    }
    CLet(_, _, _, body, _) -> [body]
    CLetRec(_, body) -> [body]
    CJoin(_, _, body, rest, _) -> [body, rest]
    CMatch(_, alts) -> Array.map(alts, fun(a) = a.body)
    _ -> Array.new(0)
}

-- -- rebuilding ---------------------------------------------------------------------

-- How to rebuild a node from its parts.
--
-- Python writes this as one reflective walk over the dataclass fields, and says
-- why: there is exactly one rule -- rewrite the children, substitute the types
-- -- and "writing it thirty times is thirty chances to miss a field a later node
-- gains". With no reflection the thirty cases have to exist, so they exist
-- *once*, here, and every pass that rebuilds a term supplies the three
-- functions rather than its own walk. `Turkey.Ast.children` is the same
-- decision for the surface tree, made for the same reason (FINDINGS.md 25).
--
-- A `CTyLam`'s and a `CLet`'s `binders` are deliberately not mapped: a binder is
-- a variable being *bound*, not one being used, so a substitution must not reach
-- it.
type Mapper = Mapper {
    expr  : fun(Core) -> Core,
    type_ : fun(Ty.Type) -> Ty.Type,
    bind  : fun(CBind) -> CBind,
}

fun mapKind(k : CoreKind, m : Mapper) -> CoreKind = match k {
    CLit(_) -> k
    CUnit -> k
    CVar(_) -> k
    CCon(_) -> k
    CPrim(_) -> k
    CTuple(elems) -> CTuple(Array.map(elems, m.expr))
    CArray(elems) -> CArray(Array.map(elems, m.expr))
    CRecord(con, fields) -> CRecord(con, Array.map(fields, fun(f) = {
        let (name, value) = f
        (name, m.expr(value))
    }))
    CField(target, name) -> CField(m.expr(target), name)
    CProject(target, index) -> CProject(m.expr(target), index)
    CIndex(target, index) -> CIndex(m.expr(target), m.expr(index))
    CLam(params, body, name) -> CLam(Array.map(params, fun(p) =
        CParam { name = p.name, ty = m.type_(p.ty) }), m.expr(body), name)
    CApp(fn, args) -> CApp(m.expr(fn), Array.map(args, m.expr))
    CTyLam(binders, body) -> CTyLam(binders, m.expr(body))
    CTyApp(fn, args) -> CTyApp(m.expr(fn), Array.map(args, m.type_))
    CLet(name, bound, value, body, binders) ->
        CLet(name, m.type_(bound), m.expr(value), m.expr(body), binders)
    CLetRec(binds, body) -> CLetRec(Array.map(binds, m.bind), m.expr(body))
    CJoin(name, params, body, rest, recursive) ->
        CJoin(name, Array.map(params, fun(p) =
            CParam { name = p.name, ty = m.type_(p.ty) }),
            m.expr(body), m.expr(rest), recursive)
    CJump(name, args) -> CJump(name, Array.map(args, m.expr))
    CRef(value) -> CRef(m.expr(value))
    CDeref(target) -> CDeref(m.expr(target))
    CAssign(target, value) -> CAssign(m.expr(target), m.expr(value))
    CIf(cond, then, otherwise) -> CIf(m.expr(cond), m.expr(then),
        match otherwise {
            Some(o) -> Some(m.expr(o))
            None -> None
        })
    CMatch(scrutinee, alts) -> CMatch(m.expr(scrutinee),
        Array.map(alts, fun(a) = CAlt { pat = a.pat, body = m.expr(a.body) }))
}

-- -- names --------------------------------------------------------------------------

-- Every term name mentioned anywhere inside a term.
--
-- Deliberately an over-approximation: a local that happens to share a top-level
-- binding's name counts as a mention of it. Being wrong in this direction costs
-- a binding kept alive or a call left un-inlined; being wrong in the other costs
-- a program that does not run.
fun namesOf(e : Core) -> Set.Set String {
    let out = Set.new()
    namesInto(e, out)
    out
}

fun namesInto(e : Core, out : Set.Set String) -> Unit = match e.kind {
    CVar(name) -> Set.add(out, name)
    CLit(_) -> {}
    CUnit -> {}
    CCon(_) -> {}
    CPrim(_) -> {}
    CTuple(elems) -> for x in elems { namesInto(x, out) }
    CArray(elems) -> for x in elems { namesInto(x, out) }
    CRecord(_, fields) -> for (_, v) in fields { namesInto(v, out) }
    CField(target, _) -> namesInto(target, out)
    CProject(target, _) -> namesInto(target, out)
    CIndex(target, index) -> {
        namesInto(target, out)
        namesInto(index, out)
    }
    CLam(_, body, _) -> namesInto(body, out)
    CApp(fn, args) -> {
        namesInto(fn, out)
        for a in args { namesInto(a, out) }
    }
    CTyLam(_, body) -> namesInto(body, out)
    CTyApp(fn, _) -> namesInto(fn, out)
    CLet(_, _, value, body, _) -> {
        namesInto(value, out)
        namesInto(body, out)
    }
    CLetRec(binds, body) -> {
        for b in binds { namesInto(b.value, out) }
        namesInto(body, out)
    }
    CJoin(_, _, body, rest, _) -> {
        namesInto(body, out)
        namesInto(rest, out)
    }
    CJump(_, args) -> for a in args { namesInto(a, out) }
    CRef(value) -> namesInto(value, out)
    CDeref(target) -> namesInto(target, out)
    CAssign(target, value) -> {
        namesInto(target, out)
        namesInto(value, out)
    }
    CIf(cond, then, otherwise) -> {
        namesInto(cond, out)
        namesInto(then, out)
        match otherwise {
            Some(o) -> namesInto(o, out)
            None -> {}
        }
    }
    CMatch(scrutinee, alts) -> {
        namesInto(scrutinee, out)
        for a in alts { namesInto(a.body, out) }
    }
}

-- -- printing -----------------------------------------------------------------------

-- Renumbers the binders a pass invented, per binding, in encounter order.
--
-- `%seq24` and `%d52.Show` carry a counter that is global to a whole
-- compilation, so adding a generated name anywhere renumbers every name after
-- it. That makes a golden churn on changes it has nothing to do with, and the
-- numbers mean nothing to a reader anyway -- they exist to be unique, and they
-- still are after this. The same decision the printer already makes for type
-- variables, which are named `a, b, c` in encounter order.
type Aliases = Aliases {
    seen   : Map String String,
    counts : Map String Int,
}

fun newAliases() -> Aliases = Aliases { seen = Map.new(), counts = Map.new() }

fun alias(a : Aliases, name : String) -> String {
    match Map.get(a.seen, name) {
        Some(found) -> return found
        None -> {}
    }
    match generatedParts(name) {
        None -> {
            Map.put(a.seen, name, name)
            name
        }
        Some((hint, suffix)) -> {
            let key = hint + "|" + suffix
            let n = Map.getOr(a.counts, key, 0) + 1
            Map.put(a.counts, key, n)
            let out = "%" + hint + Int.toString(n) + suffix
            Map.put(a.seen, name, out)
            out
        }
    }
}

-- Python matches `^%([A-Za-z]+)(\d+)(\..*)?$`; this is that pattern as a scan.
-- The hint is the letters after the `%`, the digits are dropped and renumbered,
-- and anything from the first `.` onwards is the suffix and is kept.
fun generatedParts(name : String) -> Option (String, String) {
    let cs = String.codePoints(name)
    let chars = Array.new(String.byteLength(name))
    for c in cs { Array.push(chars, c) }
    if len(chars) == 0 || chars[0] != '%' { return None }
    var i = 1
    let hint = Array.new(8)
    while i < len(chars) && Char.isAsciiAlpha(chars[i]) {
        Array.push(hint, chars[i])
        i = i + 1
    }
    if len(hint) == 0 { return None }
    let digitsFrom = i
    while i < len(chars) && Char.isDigit(chars[i]) { i = i + 1 }
    if i == digitsFrom { return None }
    if i == len(chars) { return Some((String.fromCodePoints(hint), "")) }
    if chars[i] != '.' { return None }
    Some((String.fromCodePoints(hint),
          String.fromCodePoints(Array.slice(chars, i, len(chars)))))
}

fun pad(indent : Int) -> String = String.repeat("  ", indent)

-- A readable rendering, for the `core` dump and for tests.
--
-- Types are shown where they are decided -- at a binder, at a type application
-- -- and not on every node, which would bury the term in its own annotations.
-- The checker reads the tree, not this.
fun showExpr(e : Core, indent : Int, n : Namer, a : Aliases) -> String {
    let p = pad(indent)
    match e.kind {
        CLit(l) -> p + showLit(l)
        CUnit -> p + "()"
        CVar(name) -> p + alias(a, name)
        CCon(name) -> p + alias(a, name)
        CPrim(name) -> p + "#" + name
        CTuple(elems) -> p + "(" + String.join(flatEach(elems, n, a), ", ") + ")"
        CArray(elems) -> p + "[" + String.join(flatEach(elems, n, a), ", ") + "]"
        CRecord(con, fields) -> {
            if len(fields) == 0 { return p + con + " {}" }
            let out = Array.new(len(fields) * 2 + 2)
            Array.push(out, p + con + " {")
            for (name, value) in fields {
                Array.push(out, p + "  " + name + " =")
                Array.push(out, showExpr(value, indent + 2, n, a))
            }
            Array.push(out, p + "}")
            String.join(out, "\n")
        }
        CField(target, name) -> p + flat(target, n, a) + "." + alias(a, name)
        CProject(target, index) -> p + flat(target, n, a) + "." + Int.toString(index)
        CIndex(target, index) ->
            p + flat(target, n, a) + "[" + flat(index, n, a) + "]"
        CLam(params, body, _) -> {
            let ps = Array.map(params, fun(q) =
                alias(a, q.name) + " : " + Ty.showAt(q.ty, n, 0))
            p + "fun(" + String.join(ps, ", ") + ") {\n"
              + showExpr(body, indent + 1, n, a) + "\n" + p + "}"
        }
        CApp(fn, args) -> call(Some(fn), "", args, indent, n, a)
        CTyLam(binders, body) -> {
            let bs = Array.map(binders, fun(b) = Ty.showAt(TVar(b), n, 0))
            p + "/\\" + String.join(bs, " ") + ".\n"
              + showExpr(body, indent + 1, n, a)
        }
        CTyApp(fn, args) -> {
            let as_ = Array.map(args, fun(t) = Ty.showAt(t, n, 0))
            p + flat(fn, n, a) + "[" + String.join(as_, ", ") + "]"
        }
        CLet(name, bound, value, body, binders) -> {
            let forall = if len(binders) == 0 { "" } else {
                " forall " + String.join(Array.map(binders,
                    fun(b) = Ty.showAt(TVar(b), n, 0)), " ") + "."
            }
            p + "let " + alias(a, name) + " :" + forall + " "
              + Ty.showAt(bound, n, 0) + " =\n"
              + showExpr(value, indent + 1, n, a) + "\n"
              + showExpr(body, indent, n, a)
        }
        CLetRec(binds, body) -> {
            let out = Array.new(len(binds) * 2 + 2)
            Array.push(out, p + "letrec")
            for b in binds {
                let forall = if len(b.binders) == 0 { "" } else {
                    " forall " + String.join(Array.map(b.binders,
                        fun(v) = Ty.showAt(TVar(v), n, 0)), " ") + "."
                }
                Array.push(out, p + "  " + alias(a, b.name) + " :" + forall + " "
                                + Ty.showAt(b.ty, n, 0) + " =")
                Array.push(out, showExpr(b.value, indent + 2, n, a))
            }
            Array.push(out, showExpr(body, indent, n, a))
            String.join(out, "\n")
        }
        CJoin(name, params, body, rest, recursive) -> {
            let ps = Array.map(params, fun(q) =
                alias(a, q.name) + " : " + Ty.showAt(q.ty, n, 0))
            let kind = if recursive { "join rec" } else { "join" }
            p + kind + " " + alias(a, name) + "(" + String.join(ps, ", ")
              + ") : " + Ty.showAt(e.ty, n, 0) + " = {\n"
              + showExpr(body, indent + 1, n, a) + "\n" + p + "}\n"
              + showExpr(rest, indent, n, a)
        }
        CJump(name, args) -> call(None, "jump " + alias(a, name), args, indent, n, a)
        CRef(value) -> call(None, "ref", [value], indent, n, a)
        CDeref(target) -> p + "!" + flat(target, n, a)
        CAssign(target, value) -> {
            let v = flat(value, n, a)
            let t = flat(target, n, a)
            -- An assigned value that is itself a block, which is what a
            -- dereference-and-add becomes once the dictionary is inlined.
            if String.contains(v, "\n") {
                p + t + " :=\n" + showExpr(value, indent + 1, n, a)
            } else {
                p + t + " := " + v
            }
        }
        CIf(cond, then, otherwise) -> {
            let c = flat(cond, n, a)
            let out = Array.new(6)
            -- A condition that is itself a block -- which is what `&&` lowers to,
            -- and what inlining leaves behind. Flat, it would put a `let` at
            -- column zero between the `if` and its brace.
            if String.contains(c, "\n") {
                Array.push(out, p + "if")
                Array.push(out, showExpr(cond, indent + 1, n, a))
                Array.push(out, p + "{")
                Array.push(out, showExpr(then, indent + 1, n, a))
            } else {
                Array.push(out, p + "if " + c + " {")
                Array.push(out, showExpr(then, indent + 1, n, a))
            }
            match otherwise {
                Some(o) -> {
                    Array.push(out, p + "} else {")
                    Array.push(out, showExpr(o, indent + 1, n, a))
                }
                None -> {}
            }
            Array.push(out, p + "}")
            String.join(out, "\n")
        }
        CMatch(scrutinee, alts) -> {
            let out = Array.new(len(alts) * 2 + 2)
            Array.push(out, p + "match " + flat(scrutinee, n, a) + " {")
            for alt in alts {
                Array.push(out, p + "  " + showPattern(alt.pat) + " ->")
                Array.push(out, showExpr(alt.body, indent + 2, n, a))
            }
            Array.push(out, p + "}")
            String.join(out, "\n")
        }
    }
}

fun showLit(l : Ast.Lit) -> String = literalText(l)

fun flat(e : Core, n : Namer, a : Aliases) -> String =
    String.trim(showExpr(e, 0, n, a))

fun flatEach(es : Array Core, n : Namer, a : Aliases) -> Array String =
    Array.map(es, fun(e) = flat(e, n, a))

-- `fn(args)` or `name(args)`, on one line when every argument fits on one.
--
-- An argument that spans lines -- a lambda, since that is what a `?` makes of
-- the rest of a block, or a `let` chain, since that is what inlining makes of a
-- call -- gets a line of its own, indented under the call.
fun call(fn : Option Core, name : String, args : Array Core, indent : Int,
         n : Namer, a : Aliases) -> String {
    let p = pad(indent)
    -- Arguments before the head, which matters: type variables are numbered in
    -- the order they are met, so rendering the two the other way round renames
    -- every variable in the program and moves every golden.
    let rendered = flatEach(args, n, a)
    let head = match fn {
        Some(f) -> flat(f, n, a)
        None -> name
    }
    var multiline = False
    for r in rendered {
        if String.contains(r, "\n") { multiline = True }
    }
    if !multiline { return p + head + "(" + String.join(rendered, ", ") + ")" }
    let out = Array.new(len(args) + 2)
    Array.push(out, p + head + "(")
    for var i = 0; i < len(args); i = i + 1 {
        let tail = if i < len(args) - 1 { "," } else { "" }
        Array.push(out, showExpr(args[i], indent + 1, n, a) + tail)
    }
    Array.push(out, p + ")")
    String.join(out, "\n")
}

-- Patterns are surface patterns, so their rendering is small and local.
fun showPattern(p : Pat) -> String = match p.kind {
    PVar(name) -> name
    PWild -> "_"
    PLit(l) -> showLit(l)
    PCon(name, args) -> if len(args) == 0 { name } else {
        name + "(" + String.join(Array.map(args, showPattern), ", ") + ")"
    }
    PRecord(name, fields) -> {
        let inner = Array.map(fields, fun(f) = {
            let (label, sub) = f
            label + " = " + showPattern(sub)
        })
        name + " { " + String.join(inner, ", ") + " }"
    }
    PTuple(elems) -> "(" + String.join(Array.map(elems, showPattern), ", ") + ")"
    PAnnot(inner, _) -> showPattern(inner)
}

-- One binding. The namer starts here and is shared by every type printed under
-- it, so the `a` a binder introduces is the `a` its body mentions -- which is
-- the whole reason a reader can follow a type variable from a `forall` to the
-- parameter that uses it.
fun showBind(b : CBind) -> String {
    let n = Ty.namer("")
    let a = newAliases()
    let forall = if len(b.binders) == 0 { "" } else {
        "forall " + String.join(Array.map(b.binders,
            fun(v) = Ty.showAt(TVar(v), n, 0)), " ") + ". "
    }
    b.name + " : " + forall + Ty.showAt(b.ty, n, 0) + " =\n"
      + showExpr(b.value, 1, n, a)
}

-- The whole program, or just one module's part of it. A reader wants their own
-- file's elaboration, not the Prelude's four hundred lines of it.
fun showProgram(program : CProgram, owner : Option String) -> String {
    let out = Array.new(32)
    showGroup(out, program.dicts, "dictionaries", owner)
    showGroup(out, program.binds, "bindings", owner)
    String.trimEnd(String.join(out, "\n")) + "\n"
}

fun showGroup(out : Array String, group : Array CBind, title : String,
              owner : Option String) -> Unit {
    let chosen = Array.new(len(group))
    for b in group {
        let keep = match owner {
            None -> True
            Some(m) -> b.module_ == m
        }
        if keep { Array.push(chosen, b) }
    }
    if len(chosen) == 0 { return {} }
    Array.push(out, "-- " + title)
    for b in chosen {
        Array.push(out, showBind(b))
        Array.push(out, "")
    }
}

fun oops(message : String) -> a = Diag.fail(Diag.whole("internal error", message), "")
