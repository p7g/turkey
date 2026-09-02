module Boot.AstDump (dump)

-- The canonical spelling of a parse tree, matching `turkey/astdump.py` byte for
-- byte. See that file for the format; the rules are three:
--
-- * scalars are atoms on the node's own line, in field order;
-- * sub-nodes are children on following lines, in field order;
-- * a list is a `list N` line with its elements beneath it, and an absent
--   optional is a `none` line -- both occupying the child position they would
--   have, so a node's arity is fixed and no line is ambiguous.
--
-- Only what the parser writes appears. The node ids do not: they are this
-- implementation's answer to Python's object identity, an implementation
-- detail on both sides, and printing them would make the diff depend on the
-- order in which nodes happen to be allocated.

import Prelude ()
import Std.Classes
import Data.Array as Array
import Data.Array (Array)
import Data.Bool.Type (Bool(..))
import Data.Char as Char
import Data.Float as Float
import Data.Int as Int
import Data.Option.Type (Option(..))
import Data.String as String
import Boot.Ast
import Boot.Source (Span)
import Boot.Token (quote)

type Out = Out {
    parts : String.Builder,
    depth : Int,
}

fun dump(p : Program) -> String {
    let o = Out { parts = String.builder(), depth = 0 }
    program(o, p)
    String.build(o.parts)
}

-- -- emitting -----------------------------------------------------------------------

fun line(o : Out, text : String) -> Unit {
    for var i = 0; i < o.depth; i = i + 1 { String.push(o.parts, "  ") }
    String.push(o.parts, text)
    String.push(o.parts, "\n")
}

fun showSpan(s : Span) -> String = Int.toString(s.line) + ":" + Int.toString(s.col)

fun head(o : Out, tag : String, s : Span, atoms : Array String) -> Unit {
    var text = tag + "@" + showSpan(s)
    for atom in atoms { text = text + " " + atom }
    line(o, text)
}

fun none(o : Out) -> Unit = line(o, "none")

fun indent(o : Out) -> Unit { o.depth = o.depth + 1 }

fun outdent(o : Out) -> Unit { o.depth = o.depth - 1 }

fun listHead(o : Out, count : Int) -> Unit = line(o, "list " + Int.toString(count))

fun names(o : Out, values : Array String) -> Unit {
    listHead(o, len(values))
    indent(o)
    for value in values { line(o, quote(value)) }
    outdent(o)
}

fun boolAtom(b : Bool) -> String = if b { "true" } else { "false" }

fun litAtom(l : Lit) -> String = match l {
    LInt(n) -> Int.toString(n)
    LFloat(x) -> Float.toString(x)
    LString(s) -> quote(s)
    LChar(c) -> quote(Char.toString(c))
}

-- -- the walk -----------------------------------------------------------------------

fun program(o : Out, p : Program) -> Unit {
    head(o, "program", p.span, [])
    indent(o)
    match p.header {
        None -> none(o)
        Some(h) -> header(o, h)
    }
    listHead(o, len(p.imports))
    indent(o)
    for i in p.imports { importDecl(o, i) }
    outdent(o)
    listHead(o, len(p.decls))
    indent(o)
    for d in p.decls { decl(o, d) }
    outdent(o)
    outdent(o)
}

fun header(o : Out, h : ModuleHeader) -> Unit {
    head(o, "header", h.span, [quote(h.name)])
    indent(o)
    match h.exports {
        None -> none(o)
        Some(items) -> exportList(o, items)
    }
    outdent(o)
}

fun exportList(o : Out, items : Array ExportItem) -> Unit {
    listHead(o, len(items))
    indent(o)
    for item in items { exportItem(o, item) }
    outdent(o)
}

fun exportItem(o : Out, e : ExportItem) -> Unit {
    head(o, "export", e.span, [quote(e.name), quote(e.kind)])
    indent(o)
    match e.subs {
        None -> none(o)
        Some(subs) -> names(o, subs)
    }
    outdent(o)
}

fun importDecl(o : Out, i : ImportDecl) -> Unit {
    head(o, "import", i.span, [quote(i.name), boolAtom(i.qualified)])
    indent(o)
    match i.alias {
        None -> none(o)
        Some(a) -> line(o, quote(a))
    }
    match i.items {
        None -> none(o)
        Some(items) -> exportList(o, items)
    }
    match i.hidden {
        None -> none(o)
        Some(items) -> exportList(o, items)
    }
    outdent(o)
}

-- -- types --------------------------------------------------------------------------

fun typeList(o : Out, ts : Array Type) -> Unit {
    listHead(o, len(ts))
    indent(o)
    for t in ts { typeExpr(o, t) }
    outdent(o)
}

fun typeExpr(o : Out, t : Type) -> Unit = match t.kind {
    TEVar(name) -> head(o, "tevar", t.span, [quote(name)])
    TECon(name, args) -> {
        head(o, "tecon", t.span, [quote(name)])
        indent(o)
        typeList(o, args)
        outdent(o)
    }
    TEApp(fn, args) -> {
        head(o, "teapp", t.span, [])
        indent(o)
        typeExpr(o, fn)
        typeList(o, args)
        outdent(o)
    }
    TETuple(elems) -> {
        head(o, "tetuple", t.span, [])
        indent(o)
        typeList(o, elems)
        outdent(o)
    }
    TEFun(params, ret) -> {
        head(o, "tefun", t.span, [])
        indent(o)
        typeList(o, params)
        typeExpr(o, ret)
        outdent(o)
    }
}

fun optionalType(o : Out, t : Option Type) -> Unit = match t {
    None -> none(o)
    Some(found) -> typeExpr(o, found)
}

-- -- patterns -----------------------------------------------------------------------

fun patList(o : Out, ps : Array Pat) -> Unit {
    listHead(o, len(ps))
    indent(o)
    for p in ps { pat(o, p) }
    outdent(o)
}

fun patFields(o : Out, fields : Array (String, Pat)) -> Unit {
    listHead(o, len(fields))
    indent(o)
    for f in fields {
        line(o, "field " + quote(f.0))
        indent(o)
        pat(o, f.1)
        outdent(o)
    }
    outdent(o)
}

fun pat(o : Out, p : Pat) -> Unit = match p.kind {
    PVar(name) -> head(o, "pvar", p.span, [quote(name)])
    PWild -> head(o, "pwild", p.span, [])
    PCon(name, args) -> {
        head(o, "pcon", p.span, [quote(name)])
        indent(o)
        patList(o, args)
        outdent(o)
    }
    PRecord(name, fields) -> {
        head(o, "precord", p.span, [quote(name)])
        indent(o)
        patFields(o, fields)
        outdent(o)
    }
    PLit(l) -> head(o, "plit", p.span, [quote(litKind(l)), litAtom(l)])
    PTuple(elems) -> {
        head(o, "ptuple", p.span, [])
        indent(o)
        patList(o, elems)
        outdent(o)
    }
    PAnnot(inner, t) -> {
        head(o, "pannot", p.span, [])
        indent(o)
        pat(o, inner)
        typeExpr(o, t)
        outdent(o)
    }
}

-- -- expressions --------------------------------------------------------------------

fun exprList(o : Out, es : Array Expr) -> Unit {
    listHead(o, len(es))
    indent(o)
    for e in es { expr(o, e) }
    outdent(o)
}

fun exprFields(o : Out, fields : Array (String, Expr)) -> Unit {
    listHead(o, len(fields))
    indent(o)
    for f in fields {
        line(o, "field " + quote(f.0))
        indent(o)
        expr(o, f.1)
        outdent(o)
    }
    outdent(o)
}

fun optionalExpr(o : Out, e : Option Expr) -> Unit = match e {
    None -> none(o)
    Some(found) -> expr(o, found)
}

fun optionalStmt(o : Out, s : Option Stmt) -> Unit = match s {
    None -> none(o)
    Some(found) -> stmt(o, found)
}

fun expr(o : Out, e : Expr) -> Unit = match e.kind {
    ELit(l) -> head(o, "elit", e.span, [quote(litKind(l)), litAtom(l)])
    EUnit -> head(o, "eunit", e.span, [])
    EVar(name, isMethod) -> head(o, "evar", e.span, [quote(name), boolAtom(isMethod)])
    ECon(name) -> head(o, "econ", e.span, [quote(name)])
    ETuple(elems) -> {
        head(o, "etuple", e.span, [])
        indent(o)
        exprList(o, elems)
        outdent(o)
    }
    EArray(elems) -> {
        head(o, "earray", e.span, [])
        indent(o)
        exprList(o, elems)
        outdent(o)
    }
    ERecord(con, fields) -> {
        head(o, "erecord", e.span, [quote(con)])
        indent(o)
        exprFields(o, fields)
        outdent(o)
    }
    ELambda(params, ret, body) -> {
        head(o, "elambda", e.span, [])
        indent(o)
        patList(o, params)
        optionalType(o, ret)
        expr(o, body)
        outdent(o)
    }
    ECall(fn, args) -> {
        head(o, "ecall", e.span, [])
        indent(o)
        expr(o, fn)
        exprList(o, args)
        outdent(o)
    }
    EIndex(arr, index) -> {
        head(o, "eindex", e.span, [])
        indent(o)
        expr(o, arr)
        expr(o, index)
        outdent(o)
    }
    EField(obj, name) -> {
        head(o, "efield", e.span, [quote(name)])
        indent(o)
        expr(o, obj)
        outdent(o)
    }
    EProject(obj, index) -> {
        head(o, "eproject", e.span, [Int.toString(index)])
        indent(o)
        expr(o, obj)
        outdent(o)
    }
    EUnary(op, operand, fn) -> {
        head(o, "eunary", e.span, [quote(op)])
        indent(o)
        expr(o, operand)
        optionalExpr(o, fn)
        outdent(o)
    }
    EBinary(op, left, right, fn) -> {
        head(o, "ebinary", e.span, [quote(op)])
        indent(o)
        expr(o, left)
        expr(o, right)
        optionalExpr(o, fn)
        outdent(o)
    }
    EAnnot(inner, t) -> {
        head(o, "eannot", e.span, [])
        indent(o)
        expr(o, inner)
        typeExpr(o, t)
        outdent(o)
    }
    EIf(cond, then, otherwise) -> {
        head(o, "eif", e.span, [])
        indent(o)
        expr(o, cond)
        expr(o, then)
        optionalExpr(o, otherwise)
        outdent(o)
    }
    EWhile(cond, body) -> {
        head(o, "ewhile", e.span, [])
        indent(o)
        expr(o, cond)
        expr(o, body)
        outdent(o)
    }
    EForIn(p, iterable, body, iterFn, nextFn) -> {
        head(o, "eforin", e.span, [])
        indent(o)
        pat(o, p)
        expr(o, iterable)
        expr(o, body)
        optionalExpr(o, iterFn)
        optionalExpr(o, nextFn)
        outdent(o)
    }
    EForC(init, cond, step, body) -> {
        head(o, "eforc", e.span, [])
        indent(o)
        optionalStmt(o, init)
        expr(o, cond)
        optionalStmt(o, step)
        expr(o, body)
        outdent(o)
    }
    ELoop(body) -> {
        head(o, "eloop", e.span, [])
        indent(o)
        expr(o, body)
        outdent(o)
    }
    EMatch(scrutinee, arms) -> {
        head(o, "ematch", e.span, [])
        indent(o)
        expr(o, scrutinee)
        listHead(o, len(arms))
        indent(o)
        for a in arms { arm(o, a) }
        outdent(o)
        outdent(o)
    }
    EReturn(value) -> {
        head(o, "ereturn", e.span, [])
        indent(o)
        optionalExpr(o, value)
        outdent(o)
    }
    EBreak(value) -> {
        head(o, "ebreak", e.span, [])
        indent(o)
        optionalExpr(o, value)
        outdent(o)
    }
    EContinue -> head(o, "econtinue", e.span, [])
    EBlock(stmts) -> {
        head(o, "eblock", e.span, [])
        indent(o)
        stmtList(o, stmts)
        outdent(o)
    }
    EQuestion(inner, bindFn) -> {
        head(o, "equestion", e.span, [])
        indent(o)
        expr(o, inner)
        optionalExpr(o, bindFn)
        outdent(o)
    }
    EDo(body) -> {
        head(o, "edo", e.span, [])
        indent(o)
        expr(o, body)
        outdent(o)
    }
}

fun arm(o : Out, a : Arm) -> Unit {
    head(o, "arm", a.span, [])
    indent(o)
    patList(o, a.patterns)
    expr(o, a.body)
    outdent(o)
}

-- -- statements ---------------------------------------------------------------------

fun stmtList(o : Out, ss : Array Stmt) -> Unit {
    listHead(o, len(ss))
    indent(o)
    for s in ss { stmt(o, s) }
    outdent(o)
}

fun stmt(o : Out, s : Stmt) -> Unit = match s.kind {
    SLet(p, value) -> {
        head(o, "slet", s.span, [])
        indent(o)
        pat(o, p)
        expr(o, value)
        outdent(o)
    }
    SVar(p, value) -> {
        head(o, "svar", s.span, [])
        indent(o)
        pat(o, p)
        expr(o, value)
        outdent(o)
    }
    SFun(d) -> {
        head(o, "sfun", s.span, [])
        indent(o)
        funDecl(o, d)
        outdent(o)
    }
    SAssign(target, value) -> {
        head(o, "sassign", s.span, [])
        indent(o)
        expr(o, target)
        expr(o, value)
        outdent(o)
    }
    SExpr(inner) -> {
        head(o, "sexpr", s.span, [])
        indent(o)
        expr(o, inner)
        outdent(o)
    }
}

-- -- declarations -------------------------------------------------------------------

fun predList(o : Out, ps : Array Pred) -> Unit {
    listHead(o, len(ps))
    indent(o)
    for p in ps { pred(o, p) }
    outdent(o)
}

fun pred(o : Out, p : Pred) -> Unit = match p.kind {
    PClass(name, arg) -> {
        head(o, "pred", p.span, [quote(name)])
        indent(o)
        typeExpr(o, arg)
        outdent(o)
    }
    PEq(left, right) -> {
        head(o, "eqpred", p.span, [])
        indent(o)
        typeExpr(o, left)
        typeExpr(o, right)
        outdent(o)
    }
}

fun funList(o : Out, ds : Array FunDecl) -> Unit {
    listHead(o, len(ds))
    indent(o)
    for d in ds { funDecl(o, d) }
    outdent(o)
}

fun funDecl(o : Out, d : FunDecl) -> Unit {
    head(o, "fun", d.span, [quote(d.name)])
    indent(o)
    patList(o, d.params)
    optionalType(o, d.ret)
    optionalExpr(o, d.body)
    predList(o, d.context)
    outdent(o)
}

fun typeFields(o : Out, fields : Array (String, Type)) -> Unit {
    listHead(o, len(fields))
    indent(o)
    for f in fields {
        line(o, "field " + quote(f.0))
        indent(o)
        typeExpr(o, f.1)
        outdent(o)
    }
    outdent(o)
}

fun conDecl(o : Out, c : ConDecl) -> Unit {
    head(o, "con", c.span, [quote(c.name)])
    indent(o)
    typeList(o, c.args)
    match c.fields {
        None -> none(o)
        Some(fields) -> typeFields(o, fields)
    }
    outdent(o)
}

fun typeDecl(o : Out, t : TypeDecl) -> Unit {
    head(o, "type", t.span, [quote(t.name)])
    indent(o)
    names(o, t.params)
    match t.variants {
        None -> none(o)
        Some(variants) -> {
            listHead(o, len(variants))
            indent(o)
            for v in variants { conDecl(o, v) }
            outdent(o)
        }
    }
    optionalType(o, t.alias)
    outdent(o)
}

fun classDecl(o : Out, c : ClassDecl) -> Unit {
    head(o, "class", c.span, [quote(c.name), quote(c.param)])
    indent(o)
    predList(o, c.supers)
    funList(o, c.methods)
    listHead(o, len(c.families))
    indent(o)
    for f in c.families { head(o, "family", f.span, [quote(f.name), quote(f.param)]) }
    outdent(o)
    outdent(o)
}

fun instanceDecl(o : Out, i : InstanceDecl) -> Unit {
    head(o, "instance", i.span, [quote(i.cls)])
    indent(o)
    typeExpr(o, i.head)
    predList(o, i.context)
    funList(o, i.methods)
    listHead(o, len(i.families))
    indent(o)
    for f in i.families {
        head(o, "fambind", f.span, [quote(f.name)])
        indent(o)
        typeExpr(o, f.body)
        outdent(o)
    }
    outdent(o)
    outdent(o)
}

fun decl(o : Out, d : Decl) -> Unit = match d {
    DStmt(s) -> stmt(o, s)
    DType(t) -> typeDecl(o, t)
    DClass(c) -> classDecl(o, c)
    DInstance(i) -> instanceDecl(o, i)
}
