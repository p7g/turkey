-- A positional pattern is total: it stands for every field, so it must supply
-- one sub-pattern per field. `Rect { width }` is the way to look at just one.
type Rect = Rect { width : Int, height : Int }

fun main() {
    match Rect(2, 3) {
        Rect(w) -> print(Int.toString(w))
    }
}
