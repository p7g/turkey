-- `for x in xs` is `Iterator`: `iter` makes a cursor and `next` advances it,
-- answering `Option (Item xs)`. Both the element type and the cursor type are
-- associated families, so the loop variable's type is computed from the
-- container and nobody has to write it down.
--
-- The linked list below is why iteration is cursor-based rather than indexed:
-- it has no kth element to hand out, and under an indexed protocol a loop over
-- it would be quadratic. `Array` is one instance among these, not a special
-- case.

type Range = Range { from : Int, to : Int }
type RangeCursor = RangeCursor { at : Int }

instance Iterator Range {
    type Item = Int
    type Cursor = RangeCursor

    fun iter(r) = RangeCursor { at = r.from }

    fun next(r, cur) {
        if cur.at >= r.to { return None }
        let x = cur.at
        cur.at = cur.at + 1
        Some(x)
    }
}

type List a = Nil | Cons(a, List a)
type ListCursor a = ListCursor { rest : List a }

instance Iterator (List a) {
    type Item = a
    type Cursor = ListCursor a

    fun iter(xs) = ListCursor { rest = xs }

    fun next(xs, cur) = match cur.rest {
        Nil -> None
        Cons(h, t) -> {
            cur.rest = t
            Some(h)
        }
    }
}

type Pair a = Pair(a, a)
type PairCursor = PairCursor { taken : Int }

instance Iterator (Pair a) {
    type Item = a
    type Cursor = PairCursor

    fun iter(p) = PairCursor { taken = 0 }

    fun next(p, cur) {
        let k = cur.taken
        cur.taken = k + 1
        if k >= 2 { return None }
        match p {
            Pair(x, y) -> if k == 0 { Some(x) } else { Some(y) }
        }
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
fun firstOf(xs) {
    let cur = iter(xs)
    next(xs, cur)
}

fun main() {
    print(total([1, 2, 3, 4]))
    print(total(Range { from = 10, to = 15 }))
    print(total(Pair(20, 22)))
    print(total(Cons(1, Cons(2, Cons(39, Nil)))))
    print(join(["x", "y", "z"]))
    print(firstOf(Pair("a", "b")))
    let empty : List Int = Nil
    print(firstOf(empty))

    -- `continue` still advances, and `break` leaves early.
    var seen = 0
    let ten = Range { from = 0, to = 10 }
    for i in ten {
        if i % 2 == 0 { continue }
        if i > 6 { break }
        seen = seen + i
    }
    print(seen)
}
