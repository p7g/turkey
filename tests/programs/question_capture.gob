-- What a mutable local does across a `?` (delta 46).
--
-- Everything after a `?` moves inside a lambda, so a `var` declared before it
-- and written after it is now written *from a closure*. The language has always
-- captured by reference over a shared scope -- a lambda that writes an
-- enclosing `var` writes through to it -- but nothing in `design.md` said so,
-- because until `?` no program could easily tell. It can now, and this is where
-- it is written down.
--
-- The rule that decides every case below is the one from `plan.txt`: `?` is
-- `bind` plus a lambda and must behave identically to writing that out. So how
-- many times the writes happen is not a question about `?` at all. It is a
-- question about the instance, and the answer is however many times that
-- instance's `bind` invokes its continuation.

-- `Some`'s bind calls the continuation once, so the write happens once.
-- `None`'s never calls it, so the write never happens -- and nothing can
-- observe that it did not, which is the point: the answer is `None` either way.
fun once(a : Option Int) -> Option Int {
    var seen = 0
    let x = a?
    seen = seen + x
    Some(seen)
}

-- `Array`'s bind calls the continuation once per element, and they all share
-- the one `count`, because there is one scope and the lambda closed over it.
-- So this counts every invocation rather than resetting.
fun each(xs : Array Int) -> Array Int {
    var count = 0
    let x = xs?
    count = count + 1
    pure(count * 100 + x)
}

-- The same shape with two `?`s: the inner continuation runs once per pair, so
-- `count` walks the cross product in order.
fun pairs(xs : Array Int, ys : Array Int) -> Array Int {
    var count = 0
    let x = xs?
    let y = ys?
    count = count + 1
    pure(count * 1000 + x * 10 + y)
}

-- A `var` written *before* the `?` is not captured at all: that statement is in
-- the prefix, outside every lambda the lowering makes.
fun before(a : Option Int) -> Option Int {
    var base = 1
    base = base + 9
    let x = a?
    Some(base + x)
}

fun main() {
    print(once(Some(5)))
    print(once(None))
    print(each([7, 8, 9]))
    print(pairs([1, 2], [3, 4]))
    print(before(Some(2)))
}
