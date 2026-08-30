class Semigroup a {
    fun combine(a, a) -> a
}

instance Semigroup Int {
    fun combine(x, y) = x + y
}

fun main() {
    -- `Bool` is a type, but nothing has said how to combine two of them.
    print(Int.toString(1))
    let _ = combine(true, false)
}
