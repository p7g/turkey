-- `?` is the instance's `bind`, and `do` says which block it unwinds to
-- (delta 46). `monads.tl` beside this one writes the same chains out by hand;
-- everything here is sugar for what that file says in longhand.
--
-- What is *not* here: a `?` inside a loop, and a `return`/`break`/`continue`
-- crossing one. Both are refused for now, and `err_question_in_loop.tl` and
-- `err_question_escape.tl` pin the refusals.

type Box = Box { inner : Option Int }

fun half(n : Int) -> Option Int =
    if n % 2 == 0 { Some(n / 2) } else { None }

-- Straight line. This is the whole of what `?` means when nothing else is
-- going on: one `bind` per `?`, and the rest of the block inside the lambda.
fun quarter(n : Int) -> Option Int {
    let a = half(n)?
    let b = half(a)?
    Some(b)
}

-- Two in one expression, hoisted left to right, and nested inside a call.
fun sum(n : Int, m : Int) -> Option Int {
    Some(half(n)? + half(m)?)
}

-- `do` makes a context anywhere, so a monadic value can be built mid-function.
fun inner(n : Int) -> Option Int {
    let d = do { let x = half(n)?; Some(x * 10) }
    d
}

-- An `if` branch is not a do-context, so a `?` inside one lifts the whole `if`
-- into the monad: each branch is translated with `pure`, and the `if` becomes
-- the left argument of a `bind`.
fun branchy(n : Int) -> Option Int {
    if n > 100 {
        let x = half(n)?
        Some(x)
    } else {
        Some(0)
    }
}

-- A `match` lifts the same way, per arm.
fun classify(o : Option Int, n : Int) -> Option Int {
    match o {
        Some(x) -> {
            let h = half(n)?
            Some(x + h)
        }
        None -> Some(0)
    }
}

-- A `return` *before* any `?` stays where it was written and needs nothing:
-- it is in the prefix, outside every lambda the lowering makes. So the common
-- Rust-shaped early exit works, without `?` itself being an early return.
fun early(n : Int) -> Option Int {
    if n < 0 { return None }
    let x = half(n)?
    Some(x + 1)
}

-- `&&` short-circuits, which no argument to `bind` does. A `?` on its right is
-- read as the `if` the operator already means, so the right operand stays under
-- a branch: `guarded(0, None)` answers rather than failing.
fun guarded(n : Int, o : Option Bool) -> Option Bool {
    let ok = n > 0 && o?
    Some(ok)
}

-- `?` is a suffix like call, index and field, so it chains with them.
fun chained(b : Option Box) -> Option Int {
    let v = b?.inner?
    Some(v)
}

-- A lambda is a context of its own, so this `?` belongs to the callback and
-- `outer` is not monadic at all -- it returns an array of `Option`, not an
-- `Option` of an array.
fun outer(xs : Array Int) -> Array (Option Int) {
    map(xs, fun(n) -> Option Int {
        let h = half(n)?
        Some(h + 1)
    })
}

-- The same sugar at the other two instances. `Either` carries a reason;
-- `Array` invokes the continuation once per element, so one `?` per line is a
-- cross product and not a sequence of failures.
fun both(e : Either String Int) -> Either String Int {
    let x = e?
    Right(x * 2)
}

fun cross(xs : Array Int, ys : Array Int) -> Array Int {
    let x = xs?
    let y = ys?
    pure(x * y)
}

-- A `do` with no `?` in it emits nothing at all: no `bind`, so no `Monad`
-- constraint, so nothing for it to be ambiguous about. It means the block.
fun plain(n : Int) -> Int {
    let v = do {
        let a = n + 1
        a * 2
    }
    v
}

fun main() {
    print(quarter(20))
    print(quarter(6))

    print(sum(10, 20))
    print(sum(10, 21))

    print(inner(8))

    print(branchy(200))
    print(branchy(2))

    print(classify(Some(10), 8))
    print(classify(Some(10), 7))
    print(classify(None, 7))

    print(early(-1))
    print(early(8))

    print(guarded(1, Some(True)))
    print(guarded(0, None))
    print(guarded(1, None))

    print(chained(Some(Box { inner = Some(9) })))
    print(chained(Some(Box { inner = None })))
    print(chained(None))

    print(outer([2, 3, 4]))

    print(both(Right(21)))
    print(both(Left("no")))

    print(cross([1, 2, 3], [10, 20]))
    print(cross([], [10, 20]))

    print(plain(5))
}
