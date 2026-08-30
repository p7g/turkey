-- `+` is `Add.add` and nothing more (design.md 8.2), so using it on a type
-- with no instance is a missing instance, reported like any other.
type Point = Point { x : Int, y : Int }

fun main() {
    let a = Point { x = 1, y = 2 }
    let b = a + a
    print(Int.toString(b.x))
}
