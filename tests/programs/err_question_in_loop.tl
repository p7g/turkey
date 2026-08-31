-- M12b's stated gap, pinned so it cannot go quiet (delta 46).
--
-- A loop body is not a do-context, so this `?` has to lift the loop itself into
-- the monad -- which means turning it into a recursive function whose result
-- carries whether the body fell through, broke or continued. That is delta 47,
-- and this file goes away when it lands.

fun total(xs : Array (Option Int)) -> Option Int {
    var t = 0
    for x in xs {
        let n = x?
        t = t + n
    }
    Some(t)
}

fun main() {
    print(total([Some(1), Some(2)]))
}
