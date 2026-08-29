-- design.md section 5.1: a non-exhaustive match warns at compile time and
-- panics if the unhandled case is actually reached.
type Color = Red | Green | Blue

fun describe(c : Color) -> String = match c {
    Red -> "red"
    Green -> "green"
}

fun main() { print(describe(Blue)) }
