-- Constructor callbacks must be closures, including scalar boxing and
-- newtype erasure. The bootstrap's AST walker maps ChExpr over expressions.
type Child = Missing | Child(String)
type Number = NoNumber | Number(Int)
type Wrapped = Wrapped(Int)
type Pair = EmptyPair | Pair(Int, String)

fun apply2(f, x, y) = f(x, y)

fun main() {
    for child in Array.map(["one", "two"], Child) {
        match child {
            Child(text) -> print(text)
            Missing -> print("missing")
        }
    }
    for number in Array.map([3, 4], Number) {
        match number {
            Number(n) -> print(Int.toString(n))
            NoNumber -> print("missing")
        }
    }
    for wrapped in Array.map([5, 6], Wrapped) {
        match wrapped { Wrapped(n) -> print(Int.toString(n)) }
    }
    match apply2(Pair, 7, "seven") {
        Pair(n, text) -> print(Int.toString(n) + " " + text)
        EmptyPair -> print("missing")
    }
    match (Number : fun(Int) -> Number)(8) {
        Number(n) -> print(Int.toString(n))
        NoNumber -> print("missing")
    }
    match (Missing : Child) {
        Missing -> print("missing")
        Child(text) -> print(text)
    }
}
