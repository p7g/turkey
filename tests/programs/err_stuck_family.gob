class Container c {
    type Elem c
    fun first(c) -> Elem c
}

instance Container (Array a) {
    type Elem = a
    fun first(xs) = xs[0]
}

-- `Elem c` is whatever the instance for `c` says it is, and no instance has
-- been named -- so nothing here makes it an `Int`.
fun firstInt[Container c](xs : c) -> Int = first(xs)

fun main() {
    print(Int.toString(firstInt([1, 2])))
}
