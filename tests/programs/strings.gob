-- The string surface a lexer needs: the opaque index, the two file-shaped
-- splits, ASCII classification, and integer parsing (M18b, M18c).

import Data.String as S

-- A whitespace-separated tokenizer written on `Index` alone, which is the
-- thing `String.Index` was added for. No byte offset appears anywhere in it,
-- so it cannot cut a character in half.
fun tokens(source : String) -> Array String {
    let out = []
    var i = S.start(source)
    loop {
        loop {
            match S.decode(source, i) {
                None -> { break }
                Some(c) -> {
                    if !Char.isSpace(c) { break }
                    match S.step(source, i) {
                        None -> { break }
                        Some(p) -> { i = p.1 }
                    }
                }
            }
        }
        if S.atEnd(source, i) { break }
        let from = i
        loop {
            match S.step(source, i) {
                None -> { break }
                Some(p) -> {
                    if Char.isSpace(p.0) { break }
                    i = p.1
                }
            }
        }
        Array.push(out, S.slice(source, from, i))
    }
    out
}

fun showAll(label : String, xs : Array String) -> Unit {
    var out = label + ":"
    for x in xs { out = out + " [" + x + "]" }
    print(out)
}

fun parsed(text : String) -> String = match Int.parse(text) {
    Some(n) -> Int.toString(n)
    None -> "None"
}

fun main() {
    let source = "héllo  wörld\tagain"
    showAll("tokens", tokens(source))
    print("bytes " + Int.toString(S.byteLength(source)))

    -- An index is comparable and orderable by position, which is what the two
    -- ends of a slice have to be.
    print(Bool.toString(S.start(source) == S.start(source)))
    print(Bool.toString(S.start(source) < S.end(source)))
    match S.find(source, "wörld", S.start(source)) {
        Some(at) -> print("tail " + S.slice(source, at, S.end(source)))
        None -> print("tail missing")
    }

    showAll("lines", S.lines("a\nb\nc"))
    showAll("lines trailing", S.lines("a\nb\n"))
    showAll("lines crlf", S.lines("a\r\nb\r\n"))
    showAll("lines blank", S.lines("a\n\nb"))
    print("lines empty " + Int.toString(len(S.lines(""))))
    print("lines newline " + Int.toString(len(S.lines("\n"))))
    showAll("words", S.words("  two   words  "))
    print("words empty " + Int.toString(len(S.words("   "))))

    print(Bool.toString(Char.isDigit('7')) + Bool.toString(Char.isDigit('x')))
    print(Bool.toString(Char.isAsciiAlpha('x')) + Bool.toString(Char.isAsciiAlpha('7')))
    print(Bool.toString(Char.isAsciiAlphanumeric('7'))
          + Bool.toString(Char.isAsciiAlphanumeric('_')))
    print(Bool.toString(Char.isSpace('\n')) + Bool.toString(Char.isSpace('x')))

    -- Both ends of the 64-bit range parse, and one past either does not. The
    -- accumulator runs negative for exactly this reason.
    print(parsed("0") + " " + parsed("42") + " " + parsed("-42") + " " + parsed("+7"))
    print(parsed("9223372036854775807"))
    print(parsed("-9223372036854775808"))
    print(parsed("9223372036854775808"))
    print(parsed("-9223372036854775809"))
    print(parsed("") + " " + parsed("-") + " " + parsed("12a") + " " + parsed(" 1"))
}
