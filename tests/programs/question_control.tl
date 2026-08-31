-- What crosses a bind (delta 47). `question.tl` covers `?` where control runs
-- straight through; this is the other half, and the reason it is a separate
-- milestone.
--
-- A `return`, `break` or `continue` cannot *escape* through a `bind`. Escaping
-- is not something a `bind` does, and for `Array` -- whose bind runs its
-- continuation once per element -- there is nothing coherent for an escape to
-- mean. So a transfer travels as a value instead, in the Prelude's `Flow`, and
-- every statement after a `?` runs only if the one before it fell through.
--
-- A loop is the same problem seen twice over: its continuation is not known
-- until its body has run, so it cannot be a lambda written once. It becomes a
-- recursive local function that answers with a `Flow`.

fun half(n : Int) -> Option Int =
    if n % 2 == 0 { Some(n / 2) } else { None }

-- `return` after a `?`, which is the case `question.tl`'s `early` could not do:
-- there the `return` was in the prefix, here it is inside the lambda.
fun clamp(o : Option Int) -> Option Int {
    let x = o?
    if x > 3 { return None }
    Some(x * 2)
}

-- A `?` inside a `for` body. The loop becomes a recursive function, and a
-- `None` anywhere ends the whole thing.
fun total(xs : Array (Option Int)) -> Option Int {
    var t = 0
    for x in xs {
        let n = x?
        t = t + n
    }
    Some(t)
}

-- `break` out of a lifted loop.
fun upto(xs : Array (Option Int), limit : Int) -> Option Int {
    var t = 0
    for x in xs {
        let n = x?
        if t + n > limit { break }
        t = t + n
    }
    Some(t)
}

-- `continue`.
fun evens(xs : Array (Option Int)) -> Option Int {
    var t = 0
    for x in xs {
        let n = x?
        if n % 2 == 1 { continue }
        t = t + n
    }
    Some(t)
}

-- `return` from inside a lifted loop, which has to travel out through the
-- loop's own `Flow` and then out of the block's.
fun firstBig(xs : Array (Option Int)) -> Option Int {
    for x in xs {
        let n = x?
        if n > 10 { return Some(n) }
    }
    None
}

-- `while`, with the `?` in the body.
fun countdown(a : Option Int) -> Option Int {
    var i = 3
    var t = 0
    while i > 0 {
        let n = a?
        t = t + n
        i = i - 1
    }
    Some(t)
}

-- `loop` with a break value: the loop's `Fall` is what carries it, which is
-- why a lifted loop may still be bound to a name.
fun untilBig(a : Option Int) -> Option Int {
    var i = 0
    let found = loop {
        let n = a?
        i = i + 1
        if i * n > 5 { break i * n }
    }
    Some(found)
}

-- C-style `for`. `continue` still has to run the step, or this would not end.
fun cstyle(a : Option Int) -> Option Int {
    var t = 0
    for var i = 0; i < 5; i = i + 1 {
        let n = a?
        if i % 2 == 1 { continue }
        t = t + n * i
    }
    Some(t)
}

-- The same shapes over `Either`, so that what travels is a reason and not just
-- an absence.
fun checked(xs : Array Int) -> Either String Int {
    var t = 0
    for x in xs {
        let n = if x < 0 { Left("negative") } else { Right(x) }?
        if n > 100 { return Left("too big") }
        t = t + n
    }
    Right(t)
}

-- And over `Array`, where the continuation runs many times. This is the case
-- worth staring at, and the answer is stranger than it looks: `[90, 90, 90]`.
--
-- Three, not four, because iteration is a *cursor* (delta 33) and a cursor is
-- one mutable object. The list monad's bind explores the branches depth-first,
-- and they all share that cursor -- so the first branch to run exhausts it and
-- the later ones find the loop already over. And all three answers are `90`
-- because `t` is one mutable cell too, read by `pure(t)` after the whole
-- traversal has finished mutating it.
--
-- Nothing about that is a decision `?` made. `spreadByHand` below is the same
-- loop with the recursion and the `bind` written out and no `?` anywhere, and
-- it prints the same thing -- which is the claim `?` was defined by: it is
-- `bind` plus a lambda, and behaves exactly as writing that out would.
fun spread(xs : Array Int) -> Array Int {
    var t = 0
    for x in [1, 2] {
        let n = xs?
        t = t + x * n
    }
    pure(t)
}

fun spreadByHand(xs : Array Int) -> Array Int {
    var t = 0
    let seq = [1, 2]
    let cur = iter(seq)
    fun go() -> Array Unit {
        match next(seq, cur) {
            None -> pure(())
            Some(x) -> bind(xs, fun(n) {
                t = t + x * n
                go()
            })
        }
    }
    bind(go(), fun(_) = pure(t))
}

fun main() {
    print(clamp(Some(1)))
    print(clamp(Some(9)))
    print(clamp(None))

    print(total([Some(1), Some(2), Some(3)]))
    print(total([Some(1), None, Some(3)]))
    print(total([]))

    print(upto([Some(1), Some(2), Some(3), Some(100)], 10))
    print(upto([Some(1), None], 10))

    print(evens([Some(1), Some(2), Some(3), Some(4)]))

    print(firstBig([Some(1), Some(20), Some(3)]))
    print(firstBig([Some(1), Some(2)]))
    print(firstBig([Some(1), None]))

    print(countdown(Some(5)))
    print(countdown(None))

    print(untilBig(Some(2)))

    print(cstyle(Some(1)))

    print(checked([1, 2, 3]))
    print(checked([1, -2, 3]))
    print(checked([1, 200, 3]))

    -- The differential test `plan.txt` asks for, in the one place it matters
    -- most: these two must agree, and the second contains no `?`.
    print(spread([10, 20]))
    print(spreadByHand([10, 20]))
}
