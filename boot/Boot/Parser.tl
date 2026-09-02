module Boot.Parser (parse, collectTycons)

-- Recursive descent (design.md 3), ported from `turkey/parser.py`.
--
-- The decisions that file records hold here unchanged: types and expressions
-- have separate entry points, which is what tells `fun(Int) -> Int` from
-- `fun(x) -> Int = e`; `noRecord` is set while reading the scrutinee of
-- `if`/`while`/`match` and the iterable of `for ... in`, so `if Foo { ... }` is
-- a condition and a block rather than a record construction; and a type
-- declaration is classified per design.md 7 against the type constructor names
-- a pre-pass collected, so a type may mention one declared later in the file.
--
-- One thing genuinely differs, and it is the language's doing. Python
-- backtracks by *catching* a `ParseError` in two places -- deciding whether a
-- method is a signature, and whether a `for` is the `in` form or the C-style
-- one. Turkey has no exceptions, so neither speculation can be a failed parse
-- that is caught and undone. Both are decided instead by a token scan that
-- looks for the one thing that distinguishes the two readings, before either is
-- attempted. `_rhsHasAlternatives` was already such a scan in the Python, so
-- this is the shape the file was reaching for rather than a workaround: a
-- speculative parse decides by failing, and a scan decides by looking.

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
import Boot.Diag as Diag
import Boot.Lexer as Lexer
import Boot.Source (Source, Span)
import Boot.Token as Token
import Boot.Token (Kind, Kind(..), Token, Token(..), Value, Value(..))

type P = P {
    toks     : Array Token,
    i        : Int,
    tycons   : Array String,
    noRecord : Bool,
    file     : String,
    nextId   : Int,
}

fun parse(source : Source, known : Array String) -> Program {
    let toks = Lexer.tokenize(source)
    let p = P {
        toks = toks,
        i = 0,
        tycons = Array.append(collectTycons(toks), known),
        noRecord = False,
        file = source.name,
        nextId = 0,
    }
    parseProgram(p)
}

-- design.md 7's pre-pass: every `type CONID` name declared in this file.
fun collectTycons(toks : Array Token) -> Array String {
    let names = builtinTycons()
    for var i = 0; i + 1 < len(toks); i = i + 1 {
        if Token.isWord(toks[i].kind, "type") {
            match toks[i + 1].kind {
                KConid -> Array.push(names, text(toks[i + 1]))
                _ -> {}
            }
        }
    }
    names
}

fun builtinTycons() -> Array String =
    ["Int", "Float", "String", "Char", "Bool", "Unit", "Array"]

-- -- token helpers ------------------------------------------------------------------

fun cur(p : P) -> Token = p.toks[p.i]

fun peek(p : P, ahead : Int) -> Token {
    let at = p.i + ahead
    if at >= len(p.toks) { return p.toks[len(p.toks) - 1] }
    p.toks[at]
}

fun kindOf(p : P) -> String = Token.kindName(cur(p).kind)

fun at(p : P, name : String) -> Bool = kindOf(p) == name

fun atAny(p : P, names : Array String) -> Bool = Array.contains(names, kindOf(p))

fun advance(p : P) -> Token {
    let tok = cur(p)
    match tok.kind {
        KEof -> {}
        _ -> { p.i = p.i + 1 }
    }
    tok
}

fun eat(p : P, name : String) -> Bool {
    if at(p, name) {
        advance(p)
        True
    } else {
        False
    }
}

fun expect(p : P, name : String) -> Token = expectWhat(p, name, "'" + name + "'")

-- `expect` in statement position. A block answers with its last statement, so a
-- branch ending in `expect` answers a `Token` while the sibling branch ending
-- in a `while` answers `Unit`, and the two do not unify. This is the one-word
-- fix, used wherever the token consumed is not the answer.
fun skipToken(p : P, name : String) -> Unit {
    expect(p, name)
    ()
}

fun skipWhat(p : P, name : String, what : String) -> Unit {
    expectWhat(p, name, what)
    ()
}

fun expectWhat(p : P, name : String, what : String) -> Token {
    if !at(p, name) {
        oops(p, "expected " + what + ", found " + describe(cur(p)), cur(p).span)
    }
    advance(p)
}

fun oops(p : P, message : String, where : Span) -> a =
    Diag.fail(Diag.at("parse error", message, where), p.file)

fun describe(tok : Token) -> String = match tok.kind {
    KEof -> "end of input"
    KNewline -> "end of line"
    KInt -> "INT '" + text(tok) + "'"
    KFloat -> "FLOAT '" + text(tok) + "'"
    KString -> "STRING '" + text(tok) + "'"
    KChar -> "CHAR '" + text(tok) + "'"
    KIdent -> "IDENT '" + text(tok) + "'"
    KConid -> "CONID '" + text(tok) + "'"
    _ -> "'" + Token.kindName(tok.kind) + "'"
}

-- A token's value as text, which is what an error message quotes and what a
-- name is read from.
fun text(tok : Token) -> String = match tok.value {
    VText(s) -> s
    VInt(n) -> Int.toString(n)
    VFloat(x) -> Float.toString(x)
    VChar(c) -> Char.toString(c)
    VNone -> ""
}

fun skipNewlines(p : P) -> Unit {
    while at(p, "NEWLINE") { advance(p) }
}

fun setNoRecord(p : P, flag : Bool) -> Bool {
    let saved = p.noRecord
    p.noRecord = flag
    saved
}

-- -- node construction --------------------------------------------------------------
--
-- Every node gets a fresh id here, which is the only place nodes are made.

fun freshId(p : P) -> Int {
    p.nextId = p.nextId + 1
    p.nextId
}

fun mkType(p : P, s : Span, k : TypeKind) -> Type =
    Type { id = freshId(p), span = s, kind = k }

fun mkPat(p : P, s : Span, k : PatKind) -> Pat =
    Pat { id = freshId(p), span = s, kind = k }

fun mkExpr(p : P, s : Span, k : ExprKind) -> Expr =
    Expr { id = freshId(p), span = s, kind = k }

fun mkStmt(p : P, s : Span, k : StmtKind) -> Stmt =
    Stmt { id = freshId(p), span = s, kind = k }

-- A marked method reference: the `add` a `+` means. Marked, so that a module
-- defining its own `add` does not quietly capture every `+` in the file.
fun method(p : P, s : Span, name : String) -> Expr =
    mkExpr(p, s, EVar(name, True))

-- -- tables -------------------------------------------------------------------------

fun literalKinds() -> Array String = ["INT", "FLOAT", "STRING", "CHAR"]

fun exprStart() -> Array String = [
    "INT", "FLOAT", "STRING", "CHAR",
    "IDENT", "CONID", "(", "[", "{", "-", "!",
    "fun", "if", "match", "while", "for", "loop", "return", "break", "continue",
    "do",
]

fun patternStart() -> Array String = ["INT", "FLOAT", "STRING", "CHAR", "IDENT", "CONID", "("]

-- Loosest first (design.md 3.5).
fun precedence() -> Array (Array String) = [
    ["||"],
    ["&&"],
    ["==", "!="],
    ["<", "<=", ">", ">="],
    ["+", "-"],
    ["*", "/", "%"],
]

fun binaryMethod(op : String) -> Option String {
    if op == "+" { return Some("add") }
    if op == "-" { return Some("sub") }
    if op == "*" { return Some("mul") }
    if op == "/" { return Some("div") }
    if op == "%" { return Some("rem") }
    if op == "==" { return Some("eq") }
    if op == "!=" { return Some("ne") }
    if op == "<" { return Some("lt") }
    if op == "<=" { return Some("lte") }
    if op == ">" { return Some("gt") }
    if op == ">=" { return Some("gte") }
    None
}

-- -- program ------------------------------------------------------------------------

fun parseProgram(p : P) -> Program {
    let s = cur(p).span
    skipNewlines(p)
    var header : Option ModuleHeader = None
    if at(p, "module") { header = Some(parseModuleHeader(p)) }
    let imports = Array.new(4)
    let decls = Array.new(16)
    skipNewlines(p)
    while !at(p, "EOF") {
        if at(p, "import") {
            Array.push(imports, parseImport(p))
        } else {
            if at(p, "type") {
                Array.push(decls, DType(parseTypeDecl(p)))
            } else {
                if at(p, "class") {
                    Array.push(decls, DClass(parseClassDecl(p)))
                } else {
                    if at(p, "instance") {
                        Array.push(decls, DInstance(parseInstanceDecl(p)))
                    } else {
                        if at(p, "fun") {
                            let s2 = cur(p).span
                            Array.push(decls, DStmt(mkStmt(p, s2, SFun(parseFunDecl(p, False)))))
                        } else {
                            if at(p, "let") || at(p, "var") {
                                Array.push(decls, DStmt(parseBindingStmt(p)))
                            } else {
                                oops(p, "expected a top-level declaration, found "
                                        + describe(cur(p)), cur(p).span)
                            }
                        }
                    }
                }
            }
        }
        endOfStatement(p)
    }
    Program { span = s, header = header, imports = imports, decls = decls }
}

fun endOfStatement(p : P) -> Unit {
    if at(p, "NEWLINE") {
        skipNewlines(p)
    } else {
        if !at(p, "EOF") && !at(p, "}") {
            oops(p, "expected end of statement, found " + describe(cur(p)), cur(p).span)
        }
    }
}

fun parseModuleHeader(p : P) -> ModuleHeader {
    let s = expect(p, "module").span
    let name = parseModname(p)
    var exports : Option (Array ExportItem) = None
    if at(p, "(") {
        let items = Array.new(8)
        advance(p)
        while !at(p, ")") {
            Array.push(items, parseExportItem(p))
            if !eat(p, ",") { break }
        }
        skipToken(p, ")")
        exports = Some(items)
    }
    ModuleHeader { span = s, name = name, exports = exports }
}

fun parseExportItem(p : P) -> ExportItem {
    -- `module M` re-exports everything in scope under that qualification. It is
    -- the one export form that names no entity.
    if at(p, "module") {
        let s = advance(p).span
        return ExportItem { span = s, name = parseModname(p), kind = "module", subs = None }
    }
    let tok = advance(p)
    if !isNameToken(tok) {
        oops(p, "expected an export name, found " + describe(tok), tok.span)
    }
    var subs : Option (Array String) = None
    if isConid(tok) && at(p, "(") {
        advance(p)
        let names = Array.new(4)
        while !at(p, ")") {
            Array.push(names, text(advance(p)))
            if !eat(p, ",") { break }
        }
        skipToken(p, ")")
        subs = Some(names)
    }
    ExportItem { span = tok.span, name = text(tok), kind = "name", subs = subs }
}

fun isNameToken(tok : Token) -> Bool = match tok.kind {
    KIdent -> True
    KConid -> True
    _ -> False
}

fun isConid(tok : Token) -> Bool = match tok.kind {
    KConid -> True
    _ -> False
}

fun parseModname(p : P) -> String {
    let parts = Array.new(2)
    Array.push(parts, text(expectWhat(p, "CONID", "a module name")))
    while at(p, ".") {
        match peek(p, 1).kind {
            KConid -> {
                advance(p)
                Array.push(parts, text(advance(p)))
            }
            _ -> { break }
        }
    }
    String.join(parts, ".")
}

fun parseImport(p : P) -> ImportDecl {
    let s = expect(p, "import").span
    let name = parseModname(p)
    var alias : Option String = None
    var items : Option (Array ExportItem) = None
    var hidden : Option (Array ExportItem) = None
    if eat(p, "as") { alias = Some(text(expectWhat(p, "CONID", "a module alias"))) }
    -- An alias and a selective list are independent: `import M as S (f)` names
    -- the module and narrows what it brings. Aliased imports are
    -- qualified-only; unaliased ones bring both spellings.
    if at(p, "hiding") {
        advance(p)
        hidden = Some(parseParenNameList(p))
    } else {
        if at(p, "(") { items = Some(parseParenNameList(p)) }
    }
    ImportDecl {
        span = s, name = name, alias = alias, items = items, hidden = hidden,
        qualified = isSome(alias),
    }
}

fun isSome(o : Option a) -> Bool = match o {
    Some(_) -> True
    None -> False
}

fun parseParenNameList(p : P) -> Array ExportItem {
    skipToken(p, "(")
    let names = Array.new(4)
    while !at(p, ")") {
        let item = parseExportItem(p)
        if item.kind == "module" {
            oops(p, "'module' may appear in an export list, not in an import's "
                    + "name list: a module re-exports, it does not import", item.span)
        }
        Array.push(names, item)
        if !eat(p, ",") { break }
    }
    skipToken(p, ")")
    names
}

-- -- type declarations ---------------------------------------------------------------

fun parseTypeDecl(p : P) -> TypeDecl {
    let s = expect(p, "type").span
    let name = text(expectWhat(p, "CONID", "a type name"))
    let params = Array.new(2)
    while at(p, "IDENT") { Array.push(params, text(advance(p))) }
    skipToken(p, "=")

    -- design.md 7. A `|` or a `{` payload means data type outright. A bare
    -- `CONID args*` is a data type when the head does not name an existing type
    -- constructor -- or when it names the type being declared, which is the
    -- newtype case and not a recursive alias.
    if at(p, "CONID") {
        let headName = text(cur(p))
        if Token.kindName(peek(p, 1).kind) == "{" || rhsHasAlternatives(p) {
            return parseDataRhs(p, s, name, params)
        }
        if !Array.contains(p.tycons, headName) || headName == name {
            return parseDataRhs(p, s, name, params)
        }
    }
    TypeDecl {
        span = s, name = name, params = params, variants = None,
        alias = Some(parseTypeExpr(p)),
    }
}

-- Look ahead for a top-level `|` before the declaration ends.
fun rhsHasAlternatives(p : P) -> Bool {
    var depth = 0
    var j = p.i
    while j < len(p.toks) {
        let kind = Token.kindName(p.toks[j].kind)
        if kind == "(" || kind == "[" || kind == "{" {
            depth = depth + 1
        } else {
            if kind == ")" || kind == "]" || kind == "}" {
                if depth == 0 { return False }
                depth = depth - 1
            } else {
                if depth == 0 {
                    if kind == "|" { return True }
                    if kind == "NEWLINE" || kind == "EOF" { return False }
                }
            }
        }
        j = j + 1
    }
    False
}

fun parseDataRhs(p : P, s : Span, name : String, params : Array String) -> TypeDecl {
    let variants = Array.new(4)
    Array.push(variants, parseConDecl(p))
    while eat(p, "|") {
        skipNewlines(p)
        Array.push(variants, parseConDecl(p))
    }
    TypeDecl { span = s, name = name, params = params, variants = Some(variants), alias = None }
}

fun parseConDecl(p : P) -> ConDecl {
    let tok = expectWhat(p, "CONID", "a constructor name")
    if at(p, "{") {
        return ConDecl {
            span = tok.span, name = text(tok), args = Array.new(0),
            fields = Some(parseRecordPayload(p)),
        }
    }
    let args = Array.new(2)
    if eat(p, "(") {
        -- A value constructor is an uncurried function, so it declares its
        -- payload the way a function declares parameters. Type application
        -- inside stays juxtaposed -- `Loop(Array Op)`.
        while !at(p, ")") {
            Array.push(args, parseTypeExpr(p))
            if !eat(p, ",") { break }
        }
        skipToken(p, ")")
    } else {
        if at(p, "IDENT") || at(p, "CONID") || at(p, "fun") {
            oops(p, "constructor '" + text(tok) + "' must parenthesize its payload: write '"
                    + text(tok) + "(...)'", cur(p).span)
        }
    }
    ConDecl { span = tok.span, name = text(tok), args = args, fields = None }
}

fun parseRecordPayload(p : P) -> Array (String, Type) {
    skipToken(p, "{")
    skipNewlines(p)
    let fields = Array.new(4)
    let seen = Array.new(4)
    while !at(p, "}") {
        let tok = expectWhat(p, "IDENT", "a field name")
        if Array.contains(seen, text(tok)) {
            oops(p, "duplicate field '" + text(tok) + "'", tok.span)
        }
        Array.push(seen, text(tok))
        skipToken(p, ":")
        Array.push(fields, (text(tok), parseTypeExpr(p)))
        skipNewlines(p)
        if !eat(p, ",") { break }
        skipNewlines(p)
    }
    skipToken(p, "}")
    fields
}

-- -- type expressions ----------------------------------------------------------------

fun parseTypeExpr(p : P) -> Type {
    let head = parseAtype(p)
    let args = Array.new(2)
    while at(p, "IDENT") || at(p, "CONID") || at(p, "(") {
        Array.push(args, parseAtype(p))
    }
    if len(args) == 0 { return head }
    match head.kind {
        TECon(name, existing) -> {
            if len(existing) == 0 {
                return mkType(p, head.span, TECon(name, args))
            }
            oops(p, "only a type constructor or a type variable can be applied "
                    + "to arguments", head.span)
        }
        -- A variable in head position: `f a`, which is what a higher-kinded
        -- parameter looks like at the surface.
        TEVar(_) -> mkType(p, head.span, TEApp(head, args))
        _ -> oops(p, "only a type constructor or a type variable can be applied "
                     + "to arguments", head.span)
    }
}

fun parseAtype(p : P) -> Type {
    let tok = cur(p)
    match tok.kind {
        KIdent -> {
            advance(p)
            mkType(p, tok.span, TEVar(text(tok)))
        }
        KConid -> {
            advance(p)
            -- A qualified type constructor: `S.Node`. The dotted chain is one
            -- name, resolved as one.
            let parts = Array.new(2)
            Array.push(parts, text(tok))
            while at(p, ".") {
                match peek(p, 1).kind {
                    KConid -> {
                        advance(p)
                        Array.push(parts, text(advance(p)))
                    }
                    _ -> { break }
                }
            }
            mkType(p, tok.span, TECon(String.join(parts, "."), Array.new(0)))
        }
        _ -> parseAtypeRest(p, tok)
    }
}

fun parseAtypeRest(p : P, tok : Token) -> Type {
    if at(p, "fun") {
        advance(p)
        skipToken(p, "(")
        let params = Array.new(2)
        while !at(p, ")") {
            Array.push(params, parseTypeExpr(p))
            if !eat(p, ",") { break }
        }
        skipToken(p, ")")
        skipWhat(p, "->", "'->' and a return type")
        return mkType(p, tok.span, TEFun(params, parseTypeExpr(p)))
    }
    if at(p, "(") {
        advance(p)
        let elems = Array.new(2)
        Array.push(elems, parseTypeExpr(p))
        while eat(p, ",") {
            if at(p, ")") {
                if len(elems) == 1 {
                    oops(p, "a singleton tuple '(x,)' is not supported", cur(p).span)
                }
                break
            }
            Array.push(elems, parseTypeExpr(p))
        }
        skipToken(p, ")")
        if len(elems) == 1 { return elems[0] }
        return mkType(p, tok.span, TETuple(elems))
    }
    oops(p, "expected a type, found " + describe(tok), tok.span)
}

-- -- functions -----------------------------------------------------------------------

fun parseFunDecl(p : P, allowSignature : Bool) -> FunDecl {
    let s = expect(p, "fun").span
    let name = text(expectWhat(p, "IDENT", "a function name"))
    let context = parseContext(p)
    if allowSignature && looksLikeSignature(p) {
        return parseSignature(p, s, name, context)
    }
    let params = parseParamList(p)
    var ret : Option Type = None
    if eat(p, "->") { ret = Some(parseTypeExpr(p)) }
    FunDecl {
        span = s, name = name, params = params, ret = ret,
        body = Some(parseFunBody(p)), context = context,
    }
}

-- Is this method a signature rather than a definition?
--
-- Python decides by *trying*: it parses the parameter list as types and undoes
-- that on failure or when a body turns up. With no exceptions the same question
-- is answered by looking, and what it looks for is the body: a definition has
-- an `=` or a `{` after its parameter list and a signature has neither.
--
-- The scan starts at the `(` and runs to the end of the declaration at bracket
-- depth zero. Nothing inside the parameter list or inside a return type can be
-- mistaken for a body, since both are nested; and a return type contains no
-- `=` or `{` at any depth.
fun looksLikeSignature(p : P) -> Bool {
    if !at(p, "(") { return False }
    var depth = 0
    var j = p.i
    while j < len(p.toks) {
        let kind = Token.kindName(p.toks[j].kind)
        if kind == "(" || kind == "[" || kind == "{" {
            if depth == 0 && kind == "{" { return False }
            depth = depth + 1
        } else {
            if kind == ")" || kind == "]" || kind == "}" {
                if depth == 0 { return True }
                depth = depth - 1
            } else {
                if depth == 0 {
                    if kind == "=" { return False }
                    if kind == "NEWLINE" || kind == "EOF" { return True }
                }
            }
        }
        j = j + 1
    }
    True
}

fun parseSignature(p : P, s : Span, name : String, context : Array Pred) -> FunDecl {
    skipToken(p, "(")
    let types = Array.new(2)
    while !at(p, ")") {
        Array.push(types, parseTypeExpr(p))
        if !eat(p, ",") { break }
    }
    skipToken(p, ")")
    if !eat(p, "->") {
        oops(p, "method '" + name + "' has no body, so it is a signature and must "
                + "state a return type", cur(p).span)
    }
    let ret = parseTypeExpr(p)
    -- A parameter of a stated type with no name is an anonymous binder.
    let params = Array.new(len(types))
    for t in types {
        Array.push(params, mkPat(p, t.span, PAnnot(mkPat(p, t.span, PWild), t)))
    }
    FunDecl {
        span = s, name = name, params = params, ret = Some(ret), body = None,
        context = context,
    }
}

-- `[C a, Item c ~ Op]` -- a context, not a binder.
--
-- Each entry is read as a *type expression* first and classified after, because
-- nothing shorter can tell the two forms apart: `Item c ~ Op` and
-- `Show (Elem c)` begin identically and the `~` is two atypes away.
fun parseContext(p : P) -> Array Pred {
    if !at(p, "[") { return Array.new(0) }
    advance(p)
    let preds = Array.new(2)
    loop {
        Array.push(preds, parsePredEntry(p))
        if !eat(p, ",") { break }
        if at(p, "]") { break }
    }
    skipToken(p, "]")
    preds
}

fun parsePredEntry(p : P) -> Pred {
    let s = cur(p).span
    let written = parseTypeExpr(p)
    if eat(p, "~") {
        return Pred { span = s, kind = PEq(written, parseTypeExpr(p)) }
    }
    match written.kind {
        TECon(name, args) -> {
            if len(args) == 1 {
                return Pred { span = s, kind = PClass(name, args[0]) }
            }
            oops(p, contextShape(), s)
        }
        _ -> oops(p, contextShape(), s)
    }
}

fun contextShape() -> String =
    "a context entry is a class applied to one type, as in 'Ord a', or an "
    + "equality, as in 'Item c ~ Op'"

-- -- classes and instances -------------------------------------------------------------

fun parseClassDecl(p : P) -> ClassDecl {
    let s = expect(p, "class").span
    let name = text(expectWhat(p, "CONID", "a class name"))
    let param = text(expectWhat(p, "IDENT", "the class parameter"))
    let supers = Array.new(2)
    if eat(p, ":") {
        loop {
            let tok = cur(p)
            let className = parseModname(p)
            Array.push(supers, Pred {
                span = tok.span, kind = PClass(className, parseAtype(p)),
            })
            if !eat(p, ",") { break }
            skipNewlines(p)
            if at(p, "{") { break }
        }
    }
    skipNewlines(p)
    let body = parseClassBody(p, True, param)
    ClassDecl {
        span = s, name = name, param = param, supers = supers,
        methods = body.0, families = body.1,
    }
}

fun parseInstanceDecl(p : P) -> InstanceDecl {
    let s = expect(p, "instance").span
    let name = parseModname(p)
    -- An `atype`, so a partially applied head parenthesizes.
    let head = parseAtype(p)
    let context = parseInstanceContext(p)
    skipNewlines(p)
    let body = parseInstanceBody(p)
    InstanceDecl {
        span = s, cls = name, head = head, context = context,
        methods = body.0, families = body.1,
    }
}

fun parseInstanceContext(p : P) -> Array Pred {
    if !eat(p, ":") { return Array.new(0) }
    let preds = Array.new(2)
    loop {
        Array.push(preds, parsePredEntry(p))
        if !eat(p, ",") { break }
        skipNewlines(p)
        if at(p, "{") { break }
    }
    preds
}

-- The `{ ... }` of a class: methods, and family *declarations*.
fun parseClassBody(p : P, isClass : Bool, param : String)
    -> (Array FunDecl, Array FamDecl) {
    skipToken(p, "{")
    skipNewlines(p)
    let methods = Array.new(4)
    let families = Array.new(2)
    while !at(p, "}") {
        if at(p, "type") {
            let s = expect(p, "type").span
            let name = text(expectWhat(p, "CONID", "a type family name"))
            let written = text(expectWhat(p, "IDENT", "'" + param + "', the class parameter"))
            Array.push(families, FamDecl { span = s, name = name, param = written })
        } else {
            if at(p, "fun") {
                Array.push(methods, parseFunDecl(p, isClass))
            } else {
                oops(p, "expected a method or a 'type', found " + describe(cur(p)),
                     cur(p).span)
            }
        }
        endOfStatement(p)
    }
    skipToken(p, "}")
    (methods, families)
}

-- The `{ ... }` of an instance: methods, and family *definitions*. The two
-- bodies cannot share a function because what a `type` means differs, and a
-- pair of lists is what each answers.
fun parseInstanceBody(p : P) -> (Array FunDecl, Array FamBind) {
    skipToken(p, "{")
    skipNewlines(p)
    let methods = Array.new(4)
    let families = Array.new(2)
    while !at(p, "}") {
        if at(p, "type") {
            let s = expect(p, "type").span
            let name = text(expectWhat(p, "CONID", "a type family name"))
            skipToken(p, "=")
            Array.push(families, FamBind { span = s, name = name, body = parseTypeExpr(p) })
        } else {
            if at(p, "fun") {
                Array.push(methods, parseFunDecl(p, False))
            } else {
                oops(p, "expected a method or a 'type', found " + describe(cur(p)),
                     cur(p).span)
            }
        }
        endOfStatement(p)
    }
    skipToken(p, "}")
    (methods, families)
}

fun parseParamList(p : P) -> Array Pat {
    skipToken(p, "(")
    let saved = setNoRecord(p, False)
    let params = Array.new(4)
    while !at(p, ")") {
        Array.push(params, parsePattern(p))
        if !eat(p, ",") { break }
    }
    skipToken(p, ")")
    p.noRecord = saved
    params
}

fun parseFunBody(p : P) -> Expr {
    if eat(p, "=") {
        let saved = setNoRecord(p, False)
        let body = parseExpr(p)
        p.noRecord = saved
        return body
    }
    if at(p, "{") { return parseBlock(p) }
    oops(p, "expected '=' or a block to begin a function body, found "
            + describe(cur(p)), cur(p).span)
}

-- -- statements ---------------------------------------------------------------------

fun parseBindingStmt(p : P) -> Stmt {
    let tok = advance(p)
    let saved = setNoRecord(p, False)
    let pattern = parsePattern(p)
    skipToken(p, "=")
    let value = parseExpr(p)
    p.noRecord = saved
    if Token.isWord(tok.kind, "let") {
        mkStmt(p, tok.span, SLet(pattern, value))
    } else {
        mkStmt(p, tok.span, SVar(pattern, value))
    }
}

fun parseStmt(p : P) -> Stmt {
    if at(p, "let") || at(p, "var") { return parseBindingStmt(p) }
    if at(p, "fun") {
        match peek(p, 1).kind {
            KIdent -> {
                let s = cur(p).span
                return mkStmt(p, s, SFun(parseFunDecl(p, False)))
            }
            _ -> {}
        }
    }
    let s = cur(p).span
    let saved = setNoRecord(p, False)
    let e = parseExpr(p)
    -- Assignment: the grammar puts these only in the C-style `for` header, but
    -- blocks plainly need them too.
    if at(p, "=") {
        advance(p)
        checkAssignable(p, e, s)
        let value = parseExpr(p)
        p.noRecord = saved
        return mkStmt(p, s, SAssign(e, value))
    }
    p.noRecord = saved
    mkStmt(p, s, SExpr(e))
}

fun checkAssignable(p : P, e : Expr, s : Span) -> Unit = match e.kind {
    EProject(_, _) -> oops(p, "numeric projections are read-only", s)
    EVar(_, _) -> {}
    EField(_, _) -> {}
    EIndex(_, _) -> {}
    _ -> oops(p, "left side of an assignment must be a variable, a record field, "
                 + "or an array element", s)
}

fun parseBlock(p : P) -> Expr {
    let s = expect(p, "{").span
    let saved = setNoRecord(p, False)
    let stmts = Array.new(8)
    skipNewlines(p)
    while !at(p, "}") {
        if at(p, "EOF") { oops(p, "unterminated block", s) }
        Array.push(stmts, parseStmt(p))
        endOfStatement(p)
    }
    skipToken(p, "}")
    p.noRecord = saved
    mkExpr(p, s, EBlock(stmts))
}

-- -- patterns ------------------------------------------------------------------------

fun parsePattern(p : P) -> Pat {
    let pattern = parsePatternAtom(p)
    if at(p, ":") {
        advance(p)
        return mkPat(p, pattern.span, PAnnot(pattern, parseTypeExpr(p)))
    }
    pattern
}

fun parsePatternAtom(p : P) -> Pat {
    let tok = cur(p)
    match tok.kind {
        KIdent -> {
            advance(p)
            if text(tok) == "_" { mkPat(p, tok.span, PWild) }
            else { mkPat(p, tok.span, PVar(text(tok))) }
        }
        KConid -> parseConPattern(p)
        _ -> parsePatternRest(p, tok)
    }
}

fun parsePatternRest(p : P, tok : Token) -> Pat {
    if atAny(p, literalKinds()) {
        advance(p)
        return mkPat(p, tok.span, PLit(literalOf(tok)))
    }
    if at(p, "(") {
        advance(p)
        let elems = Array.new(2)
        Array.push(elems, parsePattern(p))
        while eat(p, ",") {
            if at(p, ")") {
                if len(elems) == 1 {
                    oops(p, "a singleton tuple '(x,)' is not supported", cur(p).span)
                }
                break
            }
            Array.push(elems, parsePattern(p))
        }
        skipToken(p, ")")
        if len(elems) == 1 { return elems[0] }
        return mkPat(p, tok.span, PTuple(elems))
    }
    oops(p, "expected a pattern, found " + describe(tok), tok.span)
}

fun literalOf(tok : Token) -> Lit = match tok.value {
    VInt(n) -> LInt(n)
    VFloat(x) -> LFloat(x)
    VChar(c) -> LChar(c)
    VText(s) -> LString(s)
    VNone -> LString("")
}

fun parseConPattern(p : P) -> Pat {
    let tok = expect(p, "CONID")
    if at(p, "{") {
        advance(p)
        skipNewlines(p)
        let fields = Array.new(4)
        while !at(p, "}") {
            let name = expectWhat(p, "IDENT", "a field name")
            -- Punning: `C { x }` binds `x` from field `x`.
            var sub = mkPat(p, name.span, PVar(text(name)))
            if eat(p, "=") { sub = parsePattern(p) }
            Array.push(fields, (text(name), sub))
            skipNewlines(p)
            if !eat(p, ",") { break }
            skipNewlines(p)
        }
        skipToken(p, "}")
        return mkPat(p, tok.span, PRecord(text(tok), fields))
    }
    let args = Array.new(2)
    if at(p, "(") {
        advance(p)
        while !at(p, ")") {
            Array.push(args, parsePattern(p))
            if !eat(p, ",") { break }
        }
        skipToken(p, ")")
    } else {
        if atAny(p, patternStart()) {
            oops(p, "constructor pattern '" + text(tok) + "' must parenthesize its "
                    + "arguments: write '" + text(tok) + "(...)'", cur(p).span)
        }
    }
    mkPat(p, tok.span, PCon(text(tok), args))
}

-- -- expressions ---------------------------------------------------------------------

fun parseExpr(p : P) -> Expr {
    let e = parseBinary(p, 0)
    if at(p, ":") {
        advance(p)
        return mkExpr(p, e.span, EAnnot(e, parseTypeExpr(p)))
    }
    e
}

fun parseBinary(p : P, level : Int) -> Expr {
    let levels = precedence()
    if level >= len(levels) { return parseUnary(p) }
    var left = parseBinary(p, level + 1)
    while Array.contains(levels[level], kindOf(p)) {
        let op = Token.kindName(advance(p).kind)
        let right = parseBinary(p, level + 1)
        var fn : Option Expr = None
        match binaryMethod(op) {
            Some(name) -> { fn = Some(method(p, left.span, name)) }
            None -> {}
        }
        left = mkExpr(p, left.span, EBinary(op, left, right, fn))
    }
    left
}

fun parseUnary(p : P) -> Expr {
    if at(p, "!") || at(p, "-") {
        let tok = advance(p)
        let op = Token.kindName(tok.kind)
        var fn : Option Expr = None
        if op == "-" { fn = Some(method(p, tok.span, "neg")) }
        return mkExpr(p, tok.span, EUnary(op, parseUnary(p), fn))
    }
    parsePostfix(p)
}

fun parsePostfix(p : P) -> Expr {
    var e = parseAtom(p)
    loop {
        if at(p, "(") {
            advance(p)
            let saved = setNoRecord(p, False)
            let args = Array.new(4)
            while !at(p, ")") {
                Array.push(args, parseExpr(p))
                if !eat(p, ",") { break }
            }
            skipToken(p, ")")
            p.noRecord = saved
            e = mkExpr(p, e.span, ECall(e, args))
        } else {
            if at(p, "[") {
                advance(p)
                let saved = setNoRecord(p, False)
                let index = parseExpr(p)
                p.noRecord = saved
                skipToken(p, "]")
                e = mkExpr(p, e.span, EIndex(e, index))
            } else {
                if at(p, ".") && memberFollows(p) {
                    advance(p)
                    let member = advance(p)
                    match member.kind {
                        KInt -> { e = mkExpr(p, e.span, EProject(e, intOf(member))) }
                        _ -> { e = mkExpr(p, e.span, EField(e, text(member))) }
                    }
                } else {
                    if at(p, "?") {
                        -- A suffix like the other three, so it binds tighter
                        -- than any operator and chains with them. `bind` is
                        -- marked, so a program's own `bind` is not what a `?`
                        -- means.
                        advance(p)
                        e = mkExpr(p, e.span,
                                   EQuestion(e, Some(method(p, e.span, "bind"))))
                    } else {
                        break
                    }
                }
            }
        }
    }
    e
}

fun memberFollows(p : P) -> Bool = match peek(p, 1).kind {
    KIdent -> True
    KInt -> True
    _ -> False
}

fun intOf(tok : Token) -> Int = match tok.value {
    VInt(n) -> n
    _ -> 0
}

fun parseAtom(p : P) -> Expr {
    let tok = cur(p)
    if atAny(p, literalKinds()) {
        advance(p)
        return mkExpr(p, tok.span, ELit(literalOf(tok)))
    }
    match tok.kind {
        KIdent -> {
            advance(p)
            mkExpr(p, tok.span, EVar(text(tok), False))
        }
        KConid -> parseConidAtom(p)
        _ -> parseAtomRest(p, tok)
    }
}

fun parseAtomRest(p : P, tok : Token) -> Expr {
    if at(p, "(") {
        advance(p)
        if at(p, ")") {
            advance(p)
            return mkExpr(p, tok.span, EUnit)
        }
        let saved = setNoRecord(p, False)
        let elems = Array.new(2)
        Array.push(elems, parseExpr(p))
        while eat(p, ",") {
            if at(p, ")") {
                if len(elems) == 1 {
                    oops(p, "a singleton tuple '(x,)' is not supported", cur(p).span)
                }
                break
            }
            Array.push(elems, parseExpr(p))
        }
        skipToken(p, ")")
        p.noRecord = saved
        if len(elems) == 1 { return elems[0] }
        return mkExpr(p, tok.span, ETuple(elems))
    }
    if at(p, "[") {
        advance(p)
        let saved = setNoRecord(p, False)
        let elems = Array.new(4)
        while !at(p, "]") {
            Array.push(elems, parseExpr(p))
            if !eat(p, ",") { break }
        }
        skipToken(p, "]")
        p.noRecord = saved
        return mkExpr(p, tok.span, EArray(elems))
    }
    if at(p, "{") { return parseBlock(p) }
    if at(p, "do") {
        advance(p)
        return mkExpr(p, tok.span, EDo(parseBlock(p)))
    }
    if at(p, "fun") {
        advance(p)
        let params = parseParamList(p)
        var ret : Option Type = None
        if eat(p, "->") { ret = Some(parseTypeExpr(p)) }
        return mkExpr(p, tok.span, ELambda(params, ret, parseFunBody(p)))
    }
    if at(p, "if") { return parseIf(p) }
    if at(p, "while") {
        advance(p)
        let cond = parseScrutinee(p)
        return mkExpr(p, tok.span, EWhile(cond, parseBlock(p)))
    }
    if at(p, "for") { return parseFor(p) }
    if at(p, "loop") {
        advance(p)
        return mkExpr(p, tok.span, ELoop(parseBlock(p)))
    }
    if at(p, "match") { return parseMatch(p) }
    if at(p, "return") {
        advance(p)
        return mkExpr(p, tok.span, EReturn(parseOptionalValue(p)))
    }
    if at(p, "break") {
        advance(p)
        return mkExpr(p, tok.span, EBreak(parseOptionalValue(p)))
    }
    if at(p, "continue") {
        advance(p)
        return mkExpr(p, tok.span, EContinue)
    }
    oops(p, "expected an expression, found " + describe(tok), tok.span)
}

-- A CONID atom is a constructor, a record construction, or -- when followed by
-- `.` and a lowercase name -- a qualified variable.
fun parseConidAtom(p : P) -> Expr {
    let tok = expect(p, "CONID")
    let parts = Array.new(2)
    Array.push(parts, text(tok))
    while at(p, ".") {
        match peek(p, 1).kind {
            KConid -> {
                advance(p)
                Array.push(parts, text(advance(p)))
            }
            _ -> { break }
        }
    }
    if at(p, ".") {
        match peek(p, 1).kind {
            KIdent -> {
                advance(p)
                Array.push(parts, text(advance(p)))
                return mkExpr(p, tok.span, EVar(String.join(parts, "."), False))
            }
            _ -> {}
        }
    }
    let name = String.join(parts, ".")
    if at(p, "{") && !p.noRecord {
        advance(p)
        skipNewlines(p)
        let fields = Array.new(4)
        let saved = setNoRecord(p, False)
        while !at(p, "}") {
            let fname = expectWhat(p, "IDENT", "a field name")
            -- Punning: `C { x }` is `C { x = x }`, as in patterns.
            var value = mkExpr(p, fname.span, EVar(text(fname), False))
            if eat(p, "=") { value = parseExpr(p) }
            Array.push(fields, (text(fname), value))
            skipNewlines(p)
            if !eat(p, ",") { break }
            skipNewlines(p)
        }
        p.noRecord = saved
        skipToken(p, "}")
        return mkExpr(p, tok.span, ERecord(name, fields))
    }
    mkExpr(p, tok.span, ECon(name))
}

fun parseOptionalValue(p : P) -> Option Expr {
    if atAny(p, exprStart()) { return Some(parseExpr(p)) }
    None
}

fun parseScrutinee(p : P) -> Expr {
    let saved = setNoRecord(p, True)
    let e = parseExpr(p)
    p.noRecord = saved
    e
}

fun parseIf(p : P) -> Expr {
    let s = expect(p, "if").span
    let cond = parseScrutinee(p)
    let then = parseBlock(p)
    var otherwise : Option Expr = None
    if eat(p, "else") {
        if at(p, "if") { otherwise = Some(parseIf(p)) }
        else { otherwise = Some(parseBlock(p)) }
    }
    mkExpr(p, s, EIf(cond, then, otherwise))
}

-- `for pat in expr` and the C-style header are told apart by `in`, and Python
-- finds it by parsing a pattern and seeing what follows. Here the scan looks
-- for the keyword itself, at bracket depth zero and before the loop's body or
-- the first `;`. A nested `for ... in` is inside braces, so it is never at
-- depth zero here.
fun parseFor(p : P) -> Expr {
    let s = expect(p, "for").span
    if forInFollows(p) {
        let saved = setNoRecord(p, False)
        let pattern = parsePattern(p)
        p.noRecord = saved
        skipToken(p, "in")
        let iterable = parseScrutinee(p)
        let body = parseBlock(p)
        return mkExpr(p, s, EForIn(
            pattern, iterable, body,
            Some(method(p, iterable.span, "iter")),
            Some(method(p, iterable.span, "next"))))
    }
    let saved = setNoRecord(p, False)
    var init : Option Stmt = None
    if !at(p, "NEWLINE") { init = Some(parseStmt(p)) }
    skipWhat(p, "NEWLINE", "';' after the loop initializer")
    let cond = parseExpr(p)
    skipWhat(p, "NEWLINE", "';' after the loop condition")
    var step : Option Stmt = None
    if !at(p, "{") { step = Some(parseStmt(p)) }
    p.noRecord = saved
    mkExpr(p, s, EForC(init, cond, step, parseBlock(p)))
}

fun forInFollows(p : P) -> Bool {
    var depth = 0
    var j = p.i
    while j < len(p.toks) {
        let kind = Token.kindName(p.toks[j].kind)
        if kind == "(" || kind == "[" || kind == "{" {
            if depth == 0 && kind == "{" { return False }
            depth = depth + 1
        } else {
            if kind == ")" || kind == "]" || kind == "}" {
                if depth == 0 { return False }
                depth = depth - 1
            } else {
                if depth == 0 {
                    if kind == "in" { return True }
                    if kind == "NEWLINE" || kind == "EOF" { return False }
                }
            }
        }
        j = j + 1
    }
    False
}

fun parseMatch(p : P) -> Expr {
    let s = expect(p, "match").span
    let scrutinee = parseScrutinee(p)
    skipToken(p, "{")
    let saved = setNoRecord(p, False)
    let arms = Array.new(4)
    skipNewlines(p)
    while !at(p, "}") {
        if at(p, "EOF") { oops(p, "unterminated match expression", s) }
        Array.push(arms, parseMatchArm(p))
        -- A `|` here begins the next arm: the newline before it was dropped by
        -- design.md 2.4, so it is the only separator left.
        if at(p, "NEWLINE") {
            skipNewlines(p)
        } else {
            if !at(p, "}") && !at(p, "|") {
                oops(p, "expected the next match arm, found " + describe(cur(p)),
                     cur(p).span)
            }
        }
    }
    skipToken(p, "}")
    p.noRecord = saved
    if len(arms) == 0 { oops(p, "a match expression needs at least one arm", s) }
    mkExpr(p, s, EMatch(scrutinee, arms))
}

fun parseMatchArm(p : P) -> Arm {
    -- A leading `|` is allowed. Because a newline before `|` is always dropped,
    -- a `|` before the arm's `->` continues the pattern list while a `|` after a
    -- finished arm begins the next one, so the two readings never collide.
    eat(p, "|")
    let s = cur(p).span
    let patterns = Array.new(2)
    Array.push(patterns, parsePattern(p))
    while eat(p, "|") {
        skipNewlines(p)
        Array.push(patterns, parsePattern(p))
    }
    skipWhat(p, "->", "'->' after the arm's patterns")
    Arm { span = s, patterns = patterns, body = parseExpr(p) }
}
