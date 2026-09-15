-- Join points (plan.txt item 7, M15b).
--
-- Three local functions, and the pass is right about each for a different
-- reason. `joins.opt` is the golden that says which became a label; the
-- `.expected` beside it says the answers did not change, which is the other
-- half of the claim -- this pass writes down a fact about the program and
-- changes nothing about what it computes.

-- Called from both branches and from nowhere else, so it is a label: one copy
-- of the code and two jumps to it, instead of a closure allocated and called
-- twice. This is the shape case-of-case will produce by the hundred once M15d
-- starts duplicating continuations.
fun converge(n : Int) -> Int {
    let tag = fun(x : Int) -> Int { x * 10 + 1 }
    if n > 0 { tag(n) } else { tag(0 - n) }
}

-- Tail-recursive, so it is a loop, and a recursive join point is a loop
-- compiled as a label. Nothing in the surface program says "loop": the fact is
-- derived from where the calls are, which is the whole point of doing this in
-- Core rather than in the desugaring.
fun countdown(n : Int) -> Int {
    fun go(i : Int, acc : Int) -> Int =
        if i <= 0 { acc } else { go(i - 1, acc + i) }
    go(n, 0)
}

-- And one that is not a join point. `twice` calls its argument, so `step`
-- escapes into a parameter and has to stay a closure -- a label is not
-- something you can pass. The `let` surviving in the golden is what says the
-- pass noticed.
fun twice(f : fun(Int) -> Int, x : Int) -> Int = f(f(x))

fun escapes(n : Int) -> Int {
    let step = fun(x : Int) -> Int { x + 3 }
    twice(step, n)
}

fun main() {
    print(converge(4))
    print(converge(0 - 4))
    print(countdown(4))
    print(escapes(1))
}
