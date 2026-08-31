-- The other half of M12b's gap (delta 46), and the more surprising one.
--
-- The `return` here sits *after* a `?`, so the lowering would put it inside the
-- lambda it hands to `bind` -- where a `return` means "return from the lambda",
-- which is not what anyone wrote. Getting it out again means carrying it
-- through `bind` as a value, because a value is the only thing a `bind`
-- propagates. That is delta 47.
--
-- `question.tl`'s `early` shows the case that already works: a `return` *before*
-- every `?` stays in the prefix, outside every lambda, and needs nothing.

fun clamp(o : Option Int) -> Option Int {
    let x = o?
    if x > 3 { return None }
    Some(x)
}

fun main() {
    print(clamp(Some(1)))
}
