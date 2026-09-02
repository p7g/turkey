module Boot.Token (
    Kind, Kind(..),
    Value, Value(..),
    Token, Token(..),
    kindName,
    isOp,
    isWord,
    canonical,
    quote,
)

-- Tokens, and the one spelling of a token that two implementations can agree
-- on.
--
-- `turkey/lexer.py` keys a token by a *string* kind, which is what a Python
-- dataclass makes cheap. Here a kind is an ADT, so a walker that forgets a case
-- is told about it -- and the keyword and operator cases still carry their text,
-- because for those the text *is* the kind and a hundred nullary constructors
-- would buy nothing.

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Array as Array
import Data.Array (Array)
import Data.Char as Char
import Data.Float as Float
import Data.Int as Int
import Data.Option.Type (Option(..))
import Data.String as String
import Boot.Source (Span)

type Kind
    = KInt
    | KFloat
    | KString
    | KChar
    | KIdent
    | KConid
    | KNewline
    | KEof
    | KWord(String)
    | KOp(String)

type Value
    = VInt(Int)
    | VFloat(Float)
    | VText(String)
    | VChar(Char)
    | VNone

type Token = Token {
    kind   : Kind,
    value  : Value,
    span   : Span,
    -- A NEWLINE that came from a `;`. It survives the section 2.4 filter
    -- unconditionally, and it is the one thing about a NEWLINE that cannot be
    -- recovered from its kind.
    forced : Bool,
}

fun kindName(k : Kind) -> String = match k {
    KInt -> "INT"
    KFloat -> "FLOAT"
    KString -> "STRING"
    KChar -> "CHAR"
    KIdent -> "IDENT"
    KConid -> "CONID"
    KNewline -> "NEWLINE"
    KEof -> "EOF"
    KWord(word) -> word
    KOp(op) -> op
}

fun isOp(k : Kind, op : String) -> Bool = match k {
    KOp(found) -> found == op
    _ -> False
}

fun isWord(k : Kind, word : String) -> Bool = match k {
    KWord(found) -> found == word
    _ -> False
}

-- -- the canonical dump ---------------------------------------------------------
--
-- `line:col KIND` with an optional payload, matching `Token.canonical` in
-- `turkey/lexer.py` byte for byte. Nothing in it is the host's: the float
-- spelling is the one PRIMITIVES.md 3.3 defines and the escapes are design.md
-- 2.1's, so the same token stream prints the same way from Python and from
-- here. That is the whole point of the format -- M19's test is a diff.

fun canonical(t : Token) -> String =
    showSpanOf(t) + " " + kindName(t.kind) + payload(t)

fun showSpanOf(t : Token) -> String =
    Int.toString(t.span.line) + ":" + Int.toString(t.span.col)

fun payload(t : Token) -> String = match t.kind {
    KIdent -> " " + text(t.value)
    KConid -> " " + text(t.value)
    KInt -> " " + intText(t.value)
    KFloat -> " " + floatText(t.value)
    KString -> " " + quote(text(t.value))
    KChar -> " " + quote(charText(t.value))
    KNewline -> if t.forced { " forced" } else { "" }
    _ -> ""
}

fun text(v : Value) -> String = match v {
    VText(s) -> s
    _ -> ""
}

fun intText(v : Value) -> String = match v {
    VInt(n) -> Int.toString(n)
    _ -> ""
}

fun floatText(v : Value) -> String = match v {
    VFloat(x) -> Float.toString(x)
    _ -> ""
}

fun charText(v : Value) -> String = match v {
    VChar(c) -> Char.toString(c)
    _ -> ""
}

-- The six named escapes of design.md 2.1, and `\u{...}` for anything else that
-- is not printable. There is deliberately no `\xNN`, so a control character has
-- exactly one spelling.
fun quote(s : String) -> String {
    let b = String.builder()
    String.push(b, "\"")
    for c in String.codePoints(s) { String.push(b, escaped(c)) }
    String.push(b, "\"")
    String.build(b)
}

fun escaped(c : Char) -> String {
    let code = Char.toInt(c)
    if code == 92 { return "\\\\" }
    if code == 34 { return "\\\"" }
    if code == 10 { return "\\n" }
    if code == 9 { return "\\t" }
    if code == 13 { return "\\r" }
    if code == 0 { return "\\0" }
    if code < 32 || code == 127 { return "\\u{" + hex(code) + "}" }
    Char.toString(c)
}

fun hex(n : Int) -> String {
    if n == 0 { return "0" }
    -- Least significant digit first, then reversed: there is no integer
    -- formatting primitive that takes a base.
    let digits = Array.new(4)
    var value = n
    while value > 0 {
        Array.push(digits, hexDigit(value % 16))
        value = value / 16
    }
    let b = String.builder()
    var i = len(digits) - 1
    while i >= 0 {
        String.push(b, Char.toString(digits[i]))
        i = i - 1
    }
    String.build(b)
}

fun hexDigit(n : Int) -> Char =
    match Char.fromInt(if n < 10 { 48 + n } else { 87 + n }) {
        Some(c) -> c
        None -> '?'
    }
