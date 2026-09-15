-- A `?` demands `Monad` of whatever it is applied to, and nothing about that
-- demand is special: it comes from `bind`'s own scheme, so a type with no
-- instance reports the same missing instance any method call would (delta 46).

type Pair a = Pair(a, a)

fun first(p : Pair Int) -> Pair Int {
    let x = p?
    Pair(x, x)
}

fun main() {
    print(1)
}
