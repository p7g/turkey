-- Missing at depth: `Some(Some(_))` is not named.
fun flatten(o : Option (Option Int)) -> Int = match o {
    None -> 0
    Some(None) -> 1
}

fun main() {
    print(Int.toString(flatten(None)))
}
