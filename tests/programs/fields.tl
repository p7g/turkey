-- Field access is a `HasField` predicate, not a question asked of the
-- substitution mid-traversal, so none of this depends on the order the
-- checker happens to visit things in (this retires SPEC-DELTAS.md entry 7).

type Cell = Cell { n : Int }
type Box a = Box { n : a, tag : String }

-- Unannotated, so the receiver is never resolved here at all. The demand
-- travels in the scheme instead, and `Cell` and `Box Int` both meet it --
-- structural polymorphism reached through a predicate, in a language whose
-- records are otherwise nominal.
fun get(r) = r.n

fun bump(r) { r.n = r.n + 1 }

-- `len` before the index, and after it. Both used to be needed to make
-- the same program mean two different things.
fun before(a) {
    let n = len(a)
    let x = a[0]
    n + x
}

fun after(a) {
    let x = a[0]
    let n = len(a)
    n + x
}

fun main() {
    let c = Cell { n = 1 }
    let b = Box { n = 41, tag = "b" }
    bump(b)
    print(Int.toString(get(c) + get(b)))
    print(Int.toString(before([2])))
    print(Int.toString(after([2])))
}
