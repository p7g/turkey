-- What `?` costs after the optimizations (plan.txt item 7, M15d).
--
-- `question.tl` says `?` is a general bind and shows the chain it desugars to.
-- This file is the other end: the same chain, put through the Core passes, and
-- `question_opt.opt` is what it becomes.
--
-- The claim item 4 was deleted in favour of is that no pass here knows what a
-- monad is. Inlining reaches `Option`'s `bind` the way it reaches any small
-- known function; beta reduces the continuation the desugaring built;
-- case-of-known-constructor selects the arm; case-of-case moves the `match`
-- next to the `if` that built what it scrutinises. Between them the `Option`
-- disappears -- no allocation, no closure, no call -- and what is left is the
-- nested `if` that Rust's `?` compiles to and that a C programmer writes by
-- hand.
--
-- The chain has to be monomorphic for that to happen, which is why this file
-- is deliberately plain. `question_control.tl` is the counter-example: its `?`
-- sits under a `Flow` whose type still has a variable in it, so item 6 leaves
-- the `bind` polymorphic, its call site is a type application rather than a
-- name, and nothing here can see through it.

fun half(n : Int) -> Option Int =
    if n % 2 == 0 { Some(n / 2) } else { None }

-- Two `?` in sequence. Each is a `bind` and a lambda before the passes run.
fun quarter(n : Int) -> Option Int {
    let a = half(n)?
    let b = half(a)?
    Some(b)
}

-- And one where the two halves are used, so the continuation is not a
-- rename: `a` is still live when `b` arrives.
fun both(n : Int) -> Option Int {
    let a = half(n)?
    let b = half(a)?
    Some(a + b)
}

fun main() {
    print(quarter(8))
    print(quarter(6))
    print(quarter(3))
    print(both(8))
}
