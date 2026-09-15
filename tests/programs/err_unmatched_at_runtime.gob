-- SPEC-DELTAS 61: a non-exhaustive match is a compile error, so the thing this
-- program used to demonstrate -- reaching an unhandled arm at run time -- is
-- no longer reachable from source. It is kept, under the name it had, because
-- what it documents is still true and is now stronger: this program does not
-- compile, and the panic it used to reach cannot happen.
type Color = Red | Green | Blue

fun describe(c : Color) -> String = match c {
    Red -> "red"
    Green -> "green"
}

fun main() { print(describe(Blue)) }
