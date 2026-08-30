-- `for x in xs` is `Iterator`, and the loop variable's type is the family
-- `Item xs`. `Array` is an instance like any other, not a special case.

type Range = Range { from : Int, to : Int }

instance Iterator Range {
    type Item = Int

    fun count(r) = r.to - r.from
    fun nth(r, k) = r.from + k
}

type Pair a = Pair(a, a)

instance Iterator (Pair a) {
    type Item = a

    fun count(p) = 2
    fun nth(p, k) = match p {
        Pair(x, y) -> if k == 0 { x } else { y }
    }
}

-- Inferred over any iterator whose elements add.
fun total(xs) {
    var sum = 0
    for x in xs {
        sum = sum + x
    }
    sum
}

-- `++` is `String`-only, so this one is not general: `Item xs ~ String` is
-- exactly the equation M7 defers, and nothing here would ever decide it.
fun join(xs : Array String) -> String {
    var out = ""
    for x in xs {
        out = out ++ x
    }
    out
}

-- The class is ordinary, so its methods are callable by name.
fun second(p : Pair a) -> a = nth(p, 1)

fun main() {
    print(Int.toString(total([1, 2, 3, 4])))
    print(Int.toString(total(Range { from = 10, to = 15 })))
    print(Int.toString(total(Pair(20, 22))))
    print(join(["x", "y", "z"]))
    print(second(Pair("a", "b")))
    print(Int.toString(count(Range { from = 3, to = 9 })))

    -- `continue` still advances, and `break` leaves early.
    var seen = 0
    let ten = Range { from = 0, to = 10 }
    for i in ten {
        if i % 2 == 0 { continue }
        if i > 6 { break }
        seen = seen + i
    }
    print(Int.toString(seen))
}
