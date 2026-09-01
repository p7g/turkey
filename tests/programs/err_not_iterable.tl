-- `for ... in` runs on `Iterator`, and a `String` is not one: its code points
-- are reached with `String.codePoints`, which gives a view that is.
fun main() {
    for c in "abc" {
        print(Char.toString(c))
    }
}
