-- String.codePoints, char literal patterns, and `write` (no trailing newline).

fun classify(c : Char) -> String {
    match c {
        'a' -> "vowel"
        'e' -> "vowel"
        '?' -> "punct"
        _   -> "other"
    }
}

-- `String.codePoints` is a lazy view, not an array, so a program that wants
-- random access builds one itself. That is the point: materializing is a cost
-- the caller chooses rather than one `chars` used to charge everybody.
fun collect(s : String) -> Array Char {
    let out = Array.new(String.byteLength(s))
    for c in String.codePoints(s) { Array.push(out, c) }
    out
}

fun main() {
    let cs = collect("ae?z")
    print(Int.toString(len(cs)))

    for c in cs {
        print(classify(c))
    }

    -- Round-trips through the Int/Char conversions. `Char.fromInt` answers in
    -- `Option`, because not every Int is a Unicode scalar value.
    print(Char.toString(Option.unwrapOr(Char.fromInt(Char.toInt('A') + 2), '?')))

    -- `write` adds nothing of its own: these three land on one line, and the
    -- file ends without a trailing newline. Both facts are why this is a golden.
    write("no")
    write("-")
    write("newline")
}
