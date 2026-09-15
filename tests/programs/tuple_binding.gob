-- A binding whose pattern introduces several names is still ONE item: its
-- right-hand side must be inferred and evaluated exactly once. Keying the
-- top-level dependency graph by bound name instead of by item split this
-- across two components and ran `pair()` twice.

fun pair() -> (Int, Int) {
    print("evaluated once")
    (1, 2)
}

let (a, b) = pair()

fun main() {
    print(Int.toString(a + b))
}
