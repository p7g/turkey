-- Polymorphic recursion: the program monomorphization cannot finish, and the
-- reason `turkey/mono.py` has a cap at all.
--
-- `depth` has a complete signature, so its recursive call has a *scheme* to
-- instantiate (`infer.declared_scheme` says so outright) and instantiates it at
-- `Pair a` -- a larger type than the one it was called at. So the set of types
-- `depth` is used at is `Int`, `Pair Int`, `Pair (Pair Int)`, ... and it does
-- not end, even though the program itself plainly does: `n` counts down.
--
-- Nothing here is exotic. It is the standard reason monomorphization is
-- undecidable, and this file exists so that the suite contains one program the
-- specializer has to give up on rather than only ones it happens to finish.

type Pair a = Pair(a, a)

fun depth(x : a, n : Int) -> Int {
    if n <= 0 {
        return 0
    }
    return 1 + depth(Pair(x, x), n - 1)
}

-- The `Pair` is real: `size` reads one, so the recursion is not building a
-- value nothing looks at.
fun size(p : Pair a) -> Int = match p {
    Pair(_, _) -> 2
}

fun main() {
    print(Int.toString(depth(1, 3)))
    print(Int.toString(depth("a", 5)))
    print(Int.toString(size(Pair(1, 2))))
}
