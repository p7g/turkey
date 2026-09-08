-- A missing constructor is an error since SPEC-DELTAS 61.
fun unwrap(o : Option Int) -> Int = match o {
    Some(x) -> x
}

fun main() {
    print(Int.toString(unwrap(Some(1))))
}
