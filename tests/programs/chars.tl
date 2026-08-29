-- String.chars, char literal patterns, and `write` (no trailing newline).

fun classify(c : Char) -> String {
    match c {
        'a' -> "vowel"
        'e' -> "vowel"
        '?' -> "punct"
        _   -> "other"
    }
}

fun main() {
    let cs = String.chars("ae?z")
    print(Int.toString(cs.length))
    print(Int.toString(cs.capacity))

    for c in cs {
        print(classify(c))
    }

    -- Round-trips through the Int/Char conversions.
    print(Char.toString(Char.fromInt(Char.toInt('A') + 2)))

    -- `write` adds nothing of its own: these three land on one line, and the
    -- file ends without a trailing newline. Both facts are why this is a golden.
    write("no")
    write("-")
    write("newline")
}
