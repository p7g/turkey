module Turkey.Ast (
    Type(..), TypeKind(..),
    Pat(..), PatKind(..),
    Expr(..), ExprKind(..),
    Stmt(..), StmtKind(..),
    Lit(..), litKind,
    Arm(..),
    Pred(..), PredKind(..),
    FunDecl(..),
    ConDecl(..),
    TypeDecl(..),
    FamDecl(..),
    FamBind(..),
    ClassDecl(..),
    InstanceDecl(..),
    ExportItem(..),
    ModuleHeader(..),
    ImportDecl(..),
    Decl(..),
    Program(..),
    patternVars, freshNodeId,
    Child(..), children, exprChildren, stmtChildren,
)

-- Abstract syntax (design.md 3), as the bootstrap compiler represents it.
--
-- Two decisions here that `turkey/ast.py` did not have to make, both forced by
-- the language and both taken now because retrofitting either would touch every
-- node (plan.txt item 9, M20).
--
-- **A mutable record wrapping an immutable variant.** Python's nodes are
-- mutable dataclasses and `turkey/desugar.py` rewrites them *in place*, so
-- everything already holding a node sees the rewrite. Turkey's multi-variant
-- ADTs are immutable by design (design.md 4.5) and its single-variant records
-- are the mutable ones -- so each syntactic category is a record carrying a
-- variant. `Desugar` will assign a new `kind` rather than build a new node, and
-- the rewrite is visible through every reference, exactly as in the Python.
--
-- **An integer identity.** `turkey/typed.py` keys every expression's inferred
-- type by Python object identity. Turkey has no identity hash and its records
-- have reference semantics but no address a program can see, so the parser
-- stamps a monotone `id` on every node and the tables key on that. It is
-- assigned here, at the only place nodes are made, because a node without one
-- is a node inference cannot talk about.
--
-- Declarations are ordinary records: nothing rewrites one in place, and nothing
-- keys a table by one.

import Turkey.Source (Span)

-- Where a node's `id` comes from.
--
-- Program-wide rather than per-file, and that is the whole point: the tables
-- keyed by it -- every expression's inferred type, a use site's evidence, a
-- group's dictionary abstraction -- hold a whole program at once, so an id that
-- restarted per file would have two nodes sharing an entry. Python keys those
-- tables by object identity, which is unique by construction and so never had
-- to say this.
--
-- Never reset, and never printed. Uniqueness is all that is asked of it, and a
-- monotone counter gives that for as many programs as one process compiles.
type NodeIds = NodeIds { next : Int }

let nodeIds = NodeIds { next = 0 }

fun freshNodeId() -> Int {
    nodeIds.next = nodeIds.next + 1
    nodeIds.next
}

-- -- literals ---------------------------------------------------------------------

type Lit
    = LInt(Int)
    | LFloat(Float)
    | LString(String)
    | LChar(Char)

-- The name the type of a literal goes by, which is what a dump prints and what
-- `turkey/parser.py` stores in `PLit.kind`.
fun litKind(l : Lit) -> String = match l {
    LInt(_) -> "Int"
    LFloat(_) -> "Float"
    LString(_) -> "String"
    LChar(_) -> "Char"
}

-- -- type syntax ------------------------------------------------------------------
--
-- What the author wrote. The semantic types inference manipulates are a
-- different thing entirely and arrive with M22.

type Type = Type {
    id   : Int,
    span : Span,
    kind : TypeKind,
}

type TypeKind
    = TEVar(String)
    | TECon(String, Array Type)
    -- An application whose head is a type *variable*. A named head keeps
    -- `TECon`, because that is also what an alias is looked up by, and an alias
    -- must be saturated where a constructor need not be.
    | TEApp(Type, Array Type)
    | TETuple(Array Type)
    | TEFun(Array Type, Type)

-- -- patterns ---------------------------------------------------------------------

type Pat = Pat {
    id   : Int,
    span : Span,
    kind : PatKind,
}

type PatKind
    = PVar(String)
    | PWild
    | PCon(String, Array Pat)
    -- The punning form `C { x }` is expanded to `x = x` by the parser, so a
    -- field is always a pair.
    | PRecord(String, Array (String, Pat))
    | PLit(Lit)
    | PTuple(Array Pat)
    | PAnnot(Pat, Type)

-- -- expressions ------------------------------------------------------------------

type Expr = Expr {
    id   : Int,
    span : Span,
    kind : ExprKind,
}

type ExprKind
    = ELit(Lit)
    | EUnit
    -- The `Bool` is set when the parser wrote this node itself to mean a
    -- *class method* -- the `add` a `+` desugars to. Resolution leaves such a
    -- node alone, so a module defining its own `add` shadows the Prelude's for
    -- ordinary calls without quietly capturing every `+` in the file.
    | EVar(String, Bool)
    | ECon(String)
    | ETuple(Array Expr)
    | EArray(Array Expr)
    | ERecord(String, Array (String, Expr))
    | ELambda(Array Pat, Option Type, Expr)
    | ECall(Expr, Array Expr)
    -- The two trailing references are `Index`'s `get` and `set`, filled in by
    -- resolution and consumed by desugaring, which turns every bracket into
    -- the call it means. The parser writes `None` for both.
    | EIndex(Expr, Expr, Option Expr, Option Expr)
    | EField(Expr, String)
    | EProject(Expr, Int)
    -- The trailing `Option Expr` of each operator form is the method it means,
    -- as a marked reference. `!` is not a method and leaves it `None`.
    | EUnary(String, Expr, Option Expr)
    | EBinary(String, Expr, Expr, Option Expr)
    | EAnnot(Expr, Type)
    | EIf(Expr, Expr, Option Expr)
    | EWhile(Expr, Expr)
    -- The two trailing references are `Iterator`'s `iter` and `next`, so that
    -- solving demands `Iterator` of the sequence and elaboration hands the loop
    -- its dictionary without the loop being a special case anywhere.
    | EForIn(Pat, Expr, Expr, Option Expr, Option Expr)
    | EForC(Option Stmt, Expr, Option Stmt, Expr)
    | ELoop(Expr)
    | EMatch(Expr, Array Arm)
    | EReturn(Option Expr)
    | EBreak(Option Expr)
    | EContinue
    | EBlock(Array Stmt)
    | EQuestion(Expr, Option Expr)
    | EDo(Expr)

type Arm = Arm {
    span     : Span,
    patterns : Array Pat,
    body     : Expr,
}

-- -- statements -------------------------------------------------------------------

type Stmt = Stmt {
    id   : Int,
    span : Span,
    kind : StmtKind,
}

type StmtKind
    = SLet(Pat, Expr)
    | SVar(Pat, Expr)
    | SFun(FunDecl)
    | SAssign(Expr, Expr)
    | SExpr(Expr)

-- -- declarations -----------------------------------------------------------------

-- One constraint as written: `Ord a`, or an equality `Item c ~ Op`. The same
-- node serves a `fun`'s context, a class's superclass list and an instance's
-- context, because they all mean the same thing and differ only in where they
-- are attached.
type Pred = Pred {
    span : Span,
    kind : PredKind,
}

type PredKind
    = PClass(String, Type)
    | PEq(Type, Type)

-- A function, or -- with no body -- a bare signature, which is legal only
-- inside a `class`. A signature's parameters are types rather than binders, and
-- are still stored as patterns: a parameter of a stated type with no name is
-- exactly an annotated wildcard, so a method's type is read off a signature and
-- off a defaulted method by the same code.
type FunDecl = FunDecl {
    -- From the same supply as every other node's, so a table keyed by node id
    -- can hold declarations and expressions at once. What wants one is the
    -- dictionary abstraction a signature-checked `fun` is given: Python hangs
    -- it on the node, and the modules that define its type sit above this one.
    id      : Int,
    span    : Span,
    name    : String,
    params  : Array Pat,
    ret     : Option Type,
    body    : Option Expr,
    context : Array Pred,
    -- Set on the local `fun` a lifted loop becomes, and on nothing else. Such
    -- a function has one call site plus its own recursion, so there is nothing
    -- for a scheme to be used at twice -- and generalizing it left a variable
    -- in the `Brk` slot that nothing could solve, which kept every lifted loop
    -- out of reach of the inliner. See `Turkey.Desugar.loopFun`.
    monomorphic : Bool,
}

-- One variant of a data type. Exactly one of `args` and `fields` is meaningful.
type ConDecl = ConDecl {
    span   : Span,
    name   : String,
    args   : Array Type,
    fields : Option (Array (String, Type)),
}

type TypeDecl = TypeDecl {
    span     : Span,
    name     : String,
    params   : Array String,
    variants : Option (Array ConDecl),
    alias    : Option Type,
}

-- `type Elem c` inside a class: an associated type family. Its argument must be
-- the class's own parameter -- there is nothing else in scope for it to be a
-- function of -- so its arity is one and the parameter is named rather than
-- implied.
type FamDecl = FamDecl {
    span  : Span,
    name  : String,
    param : String,
}

-- `type Elem = a` inside an instance: what the family is, here.
type FamBind = FamBind {
    span : Span,
    name : String,
    body : Type,
}

type ClassDecl = ClassDecl {
    span     : Span,
    name     : String,
    param    : String,
    supers   : Array Pred,
    methods  : Array FunDecl,
    families : Array FamDecl,
}

type InstanceDecl = InstanceDecl {
    span     : Span,
    cls      : String,
    head     : Type,
    context  : Array Pred,
    methods  : Array FunDecl,
    families : Array FamBind,
}

-- One entry of an export list, or of an import's `(...)` / `hiding (...)`.
-- `kind` is "name" for `f`, `T(A, B)` and `T(..)`, and "module" for the
-- re-export form, which names no entity of its own.
type ExportItem = ExportItem {
    span : Span,
    name : String,
    kind : String,
    subs : Option (Array String),
}

type ModuleHeader = ModuleHeader {
    span    : Span,
    name    : String,
    exports : Option (Array ExportItem),
}

type ImportDecl = ImportDecl {
    span      : Span,
    name      : String,
    alias     : Option String,
    items     : Option (Array ExportItem),
    -- `hidden`, not `hiding`: `hiding` is a reserved word
    -- (design.md 2.1) and so cannot be a field name.
    hidden    : Option (Array ExportItem),
    qualified : Bool,
}

-- A top-level item. Statements are only `let`, `var` and `fun` up here; the
-- parser is what enforces that, not this type.
type Decl
    = DStmt(Stmt)
    | DType(TypeDecl)
    | DClass(ClassDecl)
    | DInstance(InstanceDecl)

type Program = Program {
    span    : Span,
    header  : Option ModuleHeader,
    imports : Array ImportDecl,
    decls   : Array Decl,
}

-- Every variable a pattern binds.
--
-- A structural question about the tree and nothing more, which is why it lives
-- here rather than in the one stage that happens to ask it first: `Modules`
-- wants a `let`'s binders to know what a module declares, `Resolve` wants a
-- match arm's to know what is local, and `Infer` will want a parameter's.
-- `turkey/deps.py` holds the Python's copy for the historical reason that
-- dependency ordering asked first.
fun patternVars(p : Pat) -> Array String {
    let out = Array.new(4)
    collectPatternVars(p, out)
    out
}

fun collectPatternVars(p : Pat, out : Array String) -> Unit = match p.kind {
    PVar(name) -> Array.push(out, name)
    PWild -> {}
    PLit(_) -> {}
    PCon(_, args) -> for a in args { collectPatternVars(a, out) }
    PRecord(_, fields) -> for (_, sub) in fields { collectPatternVars(sub, out) }
    PTuple(elems) -> for e in elems { collectPatternVars(e, out) }
    PAnnot(inner, _) -> collectPatternVars(inner, out)
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
