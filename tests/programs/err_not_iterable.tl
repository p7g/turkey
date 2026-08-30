-- `for ... in` runs on `Iterator`, and a `String` is not one: its characters
-- are reached with `String.chars`, which gives an `Array` that is.
fun main() {
    for c in "abc" {
        print(Char.toString(c))
    }
}
