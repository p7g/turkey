-- The four loop forms, and what they all become (plan.txt item 7, M16a).
--
-- `while`, `loop`, the C-style `for` and `for ... in` are four constructs in
-- the surface language and *none* in Core: `turkey/lower.py` makes each of
-- them a recursive join point and the jumps into it, on the way down from the
-- AST. `return`, `break` and `continue`, which named their target by where
-- they sat, are jumps that name it.
--
-- Read the golden for the shape of each. `loops.mono` beside it is the same
-- program before the optimizer runs -- both already in join points, since
-- there is no stage of the pipeline at which a loop node exists to be seen.

-- `while`, with a `var` the body writes. The cell survives the collapse -- a
-- join point's parameters are not what a mutable binding is.
fun total(n : Int) -> Int {
    var t = 0
    var i = 0
    while i < n {
        t = t + i
        i = i + 1
    }
    t
}

-- `loop`, whose `break` carries a value. That is why a loop's join takes a
-- parameter at all: `break v` is a jump with an argument.
fun firstSquareOver(limit : Int) -> Int {
    var i = 0
    loop {
        i = i + 1
        if i * i > limit { break i * i }
    }
}

-- The C-style `for`, whose `continue` runs the step before the test -- so the
-- step is a join of its own and `continue` names that one.
fun oddsBelow(n : Int) -> Int {
    var seen = 0
    for var i = 0; i < n; i = i + 1 {
        if i % 2 == 0 { continue }
        seen = seen + 1
    }
    seen
}

-- `for ... in`, whose cursor is made explicit here for the first time: the
-- `iter` and `next` calls were already terms in the Core node -- that is how
-- the `Iterator` dictionary reaches them -- and what this adds is the binding
-- that holds the cursor and the `Option` match that reads `next`'s answer.
-- The early `return` is a jump to the function's own join, out of a loop.
fun firstEven(xs : Array Int) -> Option Int {
    for x in xs {
        if x % 2 == 0 { return Some(x) }
    }
    None
}

fun main() {
    print(total(5))
    print(firstSquareOver(20))
    print(oddsBelow(7))
    print(firstEven([1, 3, 4, 7]))
    print(firstEven([1, 3, 5]))
}
