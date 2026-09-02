module Boot.Lexer (tokenize, tokenizeRaw, applyNewlineRule)

-- The tokenizer, in two passes (design.md 2, SPEC-DELTAS.md 11).
--
-- `tokenizeRaw` emits a NEWLINE for every line break; `applyNewlineRule` then
-- decides which of them terminate a production. Keeping the two apart is what
-- makes the subtlest part of the lexical structure testable on its own, and it
-- is how `turkey/lexer.py` is arranged too -- this is a port, and a port that
-- reorganized things would be much harder to diff.
--
-- Two deliberate differences from the Python, both in the direction of the
-- spec. Python asks `str.isalpha` and `str.isdigit`, which are Unicode-wide, so
-- a Devanagari digit or a Greek letter would start a numeral or an identifier
-- there; design.md 2.1 says `[a-z_]` and `[0-9]`, and that is what this asks.
-- And an integer literal outside `Int`'s range is a lex error here rather than
-- an unbounded token, which is what PRIMITIVES.md 1.3 requires.

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
import Boot.Diag as Diag
import Boot.Diag (Diag)
import Boot.Source (Source, Span, span)
import Boot.Token (Kind, Kind(..), Token, Token(..), Value, Value(..), isOp)

type Lexer = Lexer {
    src  : String,
    file : String,
    at   : String.Index,
    line : Int,
    col  : Int,
}

-- -- character helpers -----------------------------------------------------------

fun peek(lx : Lexer) -> Option Char = String.decode(lx.src, lx.at)

-- `peek(lx, n)` in the Python. Stepping is O(n) in the offset rather than O(1),
-- which costs nothing: no caller looks further than two characters ahead.
fun peekAt(lx : Lexer, ahead : Int) -> Option Char {
    var here = lx.at
    var left = ahead
    while left > 0 {
        match String.step(lx.src, here) {
            None -> { return None }
            Some(p) -> { here = p.1 }
        }
        left = left - 1
    }
    String.decode(lx.src, here)
}

fun atEnd(lx : Lexer) -> Bool = String.atEnd(lx.src, lx.at)

-- One code point, keeping the line and column in step. Columns count code
-- points, which is what `turkey/errors.py` counts.
fun bump(lx : Lexer) -> Unit {
    match String.step(lx.src, lx.at) {
        None -> {}
        Some(p) -> {
            lx.at = p.1
            if p.0 == '\n' {
                lx.line = lx.line + 1
                lx.col = 1
            } else {
                lx.col = lx.col + 1
            }
        }
    }
}

fun bumpBy(lx : Lexer, count : Int) -> Unit {
    for var i = 0; i < count; i = i + 1 { bump(lx) }
}

fun here(lx : Lexer) -> Span = span(lx.file, lx.line, lx.col)

fun oops(lx : Lexer, message : String, where : Span) -> a =
    Diag.fail(Diag.at("lex error", message, where), lx.file)

-- Does the source read `needle` at `start`? The needles are one and two
-- characters long, so this is a comparison and not a search.
fun matchesAt(src : String, start : String.Index, needle : String) -> Bool {
    var here = start
    for wanted in String.codePoints(needle) {
        match String.step(src, here) {
            None -> { return False }
            Some(p) -> {
                if p.0 != wanted { return False }
                here = p.1
            }
        }
    }
    True
}

fun charIs(c : Option Char, wanted : Char) -> Bool = match c {
    Some(found) -> found == wanted
    None -> False
}

fun charIsDigit(c : Option Char) -> Bool = match c {
    Some(found) -> Char.isDigit(found)
    None -> False
}

fun charIsSign(c : Option Char) -> Bool = match c {
    Some(found) -> found == '+' || found == '-'
    None -> False
}

-- -- the tables -------------------------------------------------------------------

fun keywords() -> Array String = [
    "type", "class", "instance", "fun", "let", "var", "match", "if", "else",
    "while", "for", "in", "loop", "return", "break", "continue", "module",
    "import", "export", "as", "hiding", "do",
]

-- Longest match first: `<=` must beat `<` and `->` must beat `-`. `?` is a
-- postfix operator and no two-character operator begins with one, so it sits
-- with the singles.
fun operators() -> Array String = [
    "==", "!=", "<=", ">=", "->", "&&", "||", "..",
    "=", "<", ">", "+", "-", "*", "/", "%", "!", ":", ",", ".", "|", "~", "?",
    "{", "}", "(", ")", "[", "]",
]

fun hexDigits() -> String = "0123456789abcdefABCDEF"

-- -- pass one ----------------------------------------------------------------------

fun tokenizeRaw(source : Source) -> Array Token {
    let lx = Lexer {
        src = source.text,
        file = source.name,
        at = String.start(source.text),
        line = 1,
        col = 1,
    }
    let tokens = Array.new(64)
    loop {
        skipInlineTrivia(lx)
        if atEnd(lx) { break }
        let start = here(lx)
        match peek(lx) {
            None -> { break }
            Some(c) -> {
                if c == '\n' {
                    bump(lx)
                    Array.push(tokens, plain(KNewline, VText("\n"), start))
                } else {
                    if c == ';' {
                        bump(lx)
                        Array.push(tokens, forcedNewline(start))
                    } else {
                        if Char.isDigit(c) {
                            -- After a dot, digits are a projection selector, so
                            -- a following dot must not fold the selector and its
                            -- successor into one FLOAT: `x.0.1` is two chained
                            -- projections.
                            Array.push(tokens, lexNumber(lx, start, afterDot(tokens)))
                        } else {
                            if c == '"' {
                                Array.push(tokens, lexString(lx, start))
                            } else {
                                if c == '\'' {
                                    Array.push(tokens, lexChar(lx, start))
                                } else {
                                    if Char.isAsciiAlpha(c) || c == '_' {
                                        Array.push(tokens, lexWord(lx, start))
                                    } else {
                                        Array.push(tokens, lexOperator(lx, start, c))
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Array.push(tokens, plain(KEof, VText(""), here(lx)))
    tokens
}

fun afterDot(tokens : Array Token) -> Bool = match Array.last(tokens) {
    None -> False
    Some(t) -> isOp(t.kind, ".")
}

fun plain(kind : Kind, value : Value, where : Span) -> Token =
    Token { kind = kind, value = value, span = where, forced = False }

fun forcedNewline(where : Span) -> Token =
    Token { kind = KNewline, value = VText(";"), span = where, forced = True }

-- Spaces, tabs, carriage returns and comments -- but never a newline, which is
-- a token.
fun skipInlineTrivia(lx : Lexer) -> Unit {
    loop {
        if atEnd(lx) { break }
        match peek(lx) {
            None -> { break }
            Some(c) -> {
                if c == ' ' || c == '\t' || c == '\r' {
                    bump(lx)
                } else {
                    if c == '-' && charIs(peekAt(lx, 1), '-') {
                        while !atEnd(lx) {
                            if charIs(peek(lx), '\n') { break }
                            bump(lx)
                        }
                    } else {
                        if c == '{' && charIs(peekAt(lx, 1), '-') {
                            skipBlockComment(lx)
                        } else {
                            break
                        }
                    }
                }
            }
        }
    }
}

-- Nested, so `{- {- -} -}` is one comment.
fun skipBlockComment(lx : Lexer) -> Unit {
    let start = here(lx)
    var depth = 0
    loop {
        if atEnd(lx) { oops(lx, "unterminated block comment", start) }
        if charIs(peek(lx), '{') && charIs(peekAt(lx, 1), '-') {
            bumpBy(lx, 2)
            depth = depth + 1
        } else {
            if charIs(peek(lx), '-') && charIs(peekAt(lx, 1), '}') {
                bumpBy(lx, 2)
                depth = depth - 1
                if depth == 0 { break }
            } else {
                bump(lx)
            }
        }
    }
}

-- -- numbers -----------------------------------------------------------------------

fun lexNumber(lx : Lexer, start : Span, afterDot : Bool) -> Token {
    let from = lx.at
    while charIsDigit(peek(lx)) { bump(lx) }
    -- FLOAT is `[0-9]+.[0-9]+`: a `.` only continues the number when a digit
    -- follows, so `1.foo` stays an INT and a field access.
    if !afterDot && charIs(peek(lx), '.') && charIsDigit(peekAt(lx, 1)) {
        bump(lx)
        while charIsDigit(peek(lx)) { bump(lx) }
        -- An exponent, and only after a fractional part, so `1e10` is still two
        -- tokens. What it buys is round-tripping: `show` on a large `Float`
        -- prints one, and the string it produces has to lex back.
        if charIs(peek(lx), 'e') || charIs(peek(lx), 'E') {
            if charIsDigit(peekAt(lx, 1)) {
                bumpBy(lx, 1)
                while charIsDigit(peek(lx)) { bump(lx) }
            } else {
                if charIsSign(peekAt(lx, 1)) && charIsDigit(peekAt(lx, 2)) {
                    bumpBy(lx, 2)
                    while charIsDigit(peek(lx)) { bump(lx) }
                }
            }
        }
        let text = String.slice(lx.src, from, lx.at)
        match Float.parse(text) {
            Some(x) -> { return plain(KFloat, VFloat(x), start) }
            None -> { oops(lx, "malformed floating-point literal " + text, start) }
        }
    }
    let text = String.slice(lx.src, from, lx.at)
    match Int.parse(text) {
        Some(n) -> plain(KInt, VInt(n), start)
        -- PRIMITIVES.md 1.3: `Int` is 64-bit, so a literal past its range is a
        -- compile error rather than something that wraps or widens.
        None -> oops(lx, "integer literal out of range: " + text, start)
    }
}

-- -- strings and characters ----------------------------------------------------------

fun lexString(lx : Lexer, start : Span) -> Token {
    bump(lx)
    let b = String.builder()
    loop {
        match peek(lx) {
            None -> { oops(lx, "unterminated string literal", start) }
            Some(c) -> {
                if c == '\n' { oops(lx, "newline in string literal", start) }
                if c == '"' {
                    bump(lx)
                    break
                }
                if c == '\\' {
                    String.push(b, Char.toString(lexEscape(lx)))
                } else {
                    String.push(b, Char.toString(c))
                    bump(lx)
                }
            }
        }
    }
    plain(KString, VText(String.build(b)), start)
}

fun lexChar(lx : Lexer, start : Span) -> Token {
    bump(lx)
    var value = '?'
    match peek(lx) {
        None -> { oops(lx, "unterminated character literal", start) }
        Some(c) -> {
            if c == '\\' {
                value = lexEscape(lx)
            } else {
                value = c
                bump(lx)
            }
        }
    }
    if !charIs(peek(lx), '\'') {
        oops(lx, "character literal must contain exactly one character", start)
    }
    bump(lx)
    plain(KChar, VChar(value), start)
}

-- One escape, as a single Unicode scalar value.
--
-- `\u{H...H}` takes one to six hex digits: four cannot reach past the BMP, so
-- `"\u{1F600}"` would otherwise be unwritable. There is no `\xNN`, and an
-- escape may not name a surrogate -- together those two rules are what make "a
-- string literal is well-formed UTF-8" true by construction (PRIMITIVES.md 4.6).
fun lexEscape(lx : Lexer) -> Char {
    let start = here(lx)
    bump(lx)
    var c = '?'
    match peek(lx) {
        None -> { oops(lx, "unterminated escape sequence", start) }
        Some(found) -> { c = found }
    }
    bump(lx)
    if c == 'u' { return lexUnicodeEscape(lx, start) }
    if c == 'n' { return '\n' }
    if c == 't' { return '\t' }
    if c == 'r' { return '\r' }
    if c == '0' { return '\0' }
    if c == '\\' { return '\\' }
    if c == '"' { return '"' }
    if c == '\'' { return '\'' }
    oops(lx, "unknown escape sequence '\\" + Char.toString(c) + "'", start)
}

fun lexUnicodeEscape(lx : Lexer, start : Span) -> Char {
    let malformed =
        "\\u escape must be written \\u{...}, with one to six hex digits in the braces"
    if !charIs(peek(lx), '{') { oops(lx, malformed, start) }
    bump(lx)
    let from = lx.at
    var count = 0
    while isHexDigit(peek(lx)) {
        bump(lx)
        count = count + 1
    }
    if count < 1 || count > 6 || !charIs(peek(lx), '}') { oops(lx, malformed, start) }
    let digits = String.slice(lx.src, from, lx.at)
    bump(lx)
    let code = hexValue(digits)
    match Char.fromInt(code) {
        Some(c) -> c
        None -> oops(lx, "\\u{" + digits + "} is not a Unicode scalar value: "
                         + notScalarBecause(code), start)
    }
}

fun notScalarBecause(code : Int) -> String =
    if code <= 57343 {
        "the surrogates D800..DFFF are not characters, and a string may not contain one"
    } else {
        "values above 10FFFF are not characters, and a string may not contain one"
    }

fun isHexDigit(c : Option Char) -> Bool = match c {
    None -> False
    Some(found) -> String.contains(hexDigits(), Char.toString(found))
}

fun hexValue(digits : String) -> Int {
    var total = 0
    for c in String.codePoints(digits) {
        total = total * 16 + hexDigitValue(c)
    }
    total
}

fun hexDigitValue(c : Char) -> Int {
    let code = Char.toInt(c)
    if code >= 48 && code <= 57 { return code - 48 }
    if code >= 97 && code <= 102 { return code - 87 }
    code - 55
}

-- -- words and operators --------------------------------------------------------------

fun lexWord(lx : Lexer, start : Span) -> Token {
    let from = lx.at
    loop {
        match peek(lx) {
            None -> { break }
            Some(c) -> {
                if Char.isAsciiAlphanumeric(c) || c == '_' || c == '\'' {
                    bump(lx)
                } else {
                    break
                }
            }
        }
    }
    let text = String.slice(lx.src, from, lx.at)
    if Array.contains(keywords(), text) { return plain(KWord(text), VText(text), start) }
    -- design.md 2.3: the leading character decides. `_` and lowercase are
    -- variables; uppercase are type and value constructors.
    if startsUpper(text) { plain(KConid, VText(text), start) }
    else { plain(KIdent, VText(text), start) }
}

fun startsUpper(text : String) -> Bool = match String.decode(text, String.start(text)) {
    None -> False
    Some(c) -> {
        let code = Char.toInt(c)
        code >= 65 && code <= 90
    }
}

fun lexOperator(lx : Lexer, start : Span, c : Char) -> Token {
    for op in operators() {
        if matchesAt(lx.src, lx.at, op) {
            bumpBy(lx, String.codePointCount(op))
            return plain(KOp(op), VText(op), start)
        }
    }
    oops(lx, "unexpected character '" + Char.toString(c) + "'", start)
}

-- -- pass two: the newline rule ----------------------------------------------------
--
-- A newline survives iff the token before it can end a production and the one
-- after it can start a sibling. A newline from `;` always survives.
--
-- Bracket nesting is a *stack* and not a counter, because `(` and `{` disagree
-- about whether a line break matters and either can contain the other. Inside
-- `(` or `[` a break is wrapping, so it goes; inside `{` it separates block
-- statements and match arms, so it stays. Only the innermost bracket decides --
-- otherwise the `{ }` in `f(match x { ... })` would inherit the call's
-- suppression and its arms would run together.

fun applyNewlineRule(tokens : Array Token) -> Array Token {
    let out = Array.new(len(tokens))
    let stack = Array.new(8)
    var i = 0
    while i < len(tokens) {
        let tok = tokens[i]
        match tok.kind {
            KNewline -> {
                -- Collapse a run of blank lines into one decision.
                var runEnd = i
                var forced = False
                while runEnd < len(tokens) {
                    match tokens[runEnd].kind {
                        KNewline -> {
                            forced = forced || tokens[runEnd].forced
                            runEnd = runEnd + 1
                        }
                        _ -> { break }
                    }
                }
                if keepNewline(out, tokens, runEnd, forced, stack) {
                    Array.push(out, Token {
                        kind = KNewline,
                        value = tokens[i].value,
                        span = tokens[i].span,
                        forced = forced,
                    })
                }
                i = runEnd
            }
            _ -> {
                if isOpen(tok.kind) {
                    Array.push(stack, tok.kind)
                } else {
                    if isClose(tok.kind) && len(stack) > 0 {
                        Array.pop(stack)
                    }
                }
                Array.push(out, tok)
                i = i + 1
            }
        }
    }
    out
}

fun keepNewline(out : Array Token, tokens : Array Token, runEnd : Int,
                forced : Bool, stack : Array Kind) -> Bool {
    let prev = Array.last(out)
    let next = Array.at(tokens, runEnd)
    match prev {
        None -> { return False }
        Some(_) -> {}
    }
    match next {
        None -> { return False }
        Some(after) -> {
            match after.kind {
                KEof -> { return False }
                _ -> {}
            }
            if forced { return True }
            match Array.last(stack) {
                None -> {}
                Some(open) -> {
                    if isOp(open, "(") || isOp(open, "[") { return False }
                }
            }
            match prev {
                None -> False
                Some(before) -> canEnd(before.kind) && canStart(after.kind)
            }
        }
    }
}

fun isOpen(k : Kind) -> Bool = isOp(k, "(") || isOp(k, "[") || isOp(k, "{")

fun isClose(k : Kind) -> Bool = isOp(k, ")") || isOp(k, "]") || isOp(k, "}")

fun isLiteral(k : Kind) -> Bool = match k {
    KInt -> True
    KFloat -> True
    KString -> True
    KChar -> True
    _ -> False
}

-- design.md 2.4's preceding condition: tokens that can legally end a
-- production. `break`, `continue` and bare `return` are here even though
-- design.md omits them, because a statement ending in one still has to be
-- terminable by a newline. `?` is here because it is a *postfix* operator:
-- `let x = f()?` is a complete statement whose last token is the `?`.
fun canEnd(k : Kind) -> Bool {
    if isLiteral(k) { return True }
    match k {
        KIdent -> True
        KConid -> True
        KWord(word) -> word == "break" || word == "continue" || word == "return"
        KOp(op) -> op == ")" || op == "]" || op == "}" || op == "?"
        _ -> False
    }
}

-- design.md 2.4's following condition: tokens that can legally start a sibling
-- production. `else` and `|` are deliberately absent. That is what lets
-- `} else` stay one expression, and what lets a match arm's alternative
-- patterns be split across lines -- the newline before the `|` disappears.
fun canStart(k : Kind) -> Bool {
    if isLiteral(k) { return True }
    match k {
        KIdent -> True
        KConid -> True
        KWord(word) -> Array.contains(starters(), word)
        KOp(op) -> op == "(" || op == "[" || op == "{" || op == "-" || op == "!"
        _ -> False
    }
}

fun starters() -> Array String = [
    "let", "var", "fun", "type", "class", "instance", "if", "match",
    "while", "for", "loop", "return", "break", "continue", "module",
    "import", "do",
]

fun tokenize(source : Source) -> Array Token =
    applyNewlineRule(tokenizeRaw(source))
