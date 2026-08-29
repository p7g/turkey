-- Exhaustiveness warnings (design.md section 5.1). A missing case is a warning;
-- reaching one at run time is a panic.

type Option a = None | Some(a)
type Color = Red | Green | Blue

-- Exhaustive: every constructor is covered.
fun describe(c : Color) -> String = match c {
    Red -> "red"
    Green -> "green"
    Blue -> "blue"
}

-- Exhaustive through a tuple, including the nested Bool.
fun pair(p : (Color, Bool)) -> Int = match p {
    (Red, true) -> 1
    (Red, false) -> 2
    (Green, _) -> 3
    (Blue, _) -> 4
}

-- Not exhaustive: `None` is missing.
fun unwrap(o : Option Int) -> Int = match o {
    Some(x) -> x
}

-- Not exhaustive at depth: `Some(Some(_))` is missing.
fun flatten(o : Option (Option Int)) -> Int = match o {
    None -> 0
    Some(None) -> 1
}

-- Not exhaustive: Int has too many values to enumerate.
fun small(n : Int) -> Bool = match n {
    0 -> true
    1 -> true
}

fun main() {
    print(describe(Green))
    print(Int.toString(pair((Blue, true))))
    print(Int.toString(flatten(Some(None))))
    print(Bool.toString(small(0)))
}
