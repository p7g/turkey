-- Classes running: every method call below resolves through a dictionary
-- built by turkey/evidence.py. `classes.tl` beside this one checks the same
-- declarations without running any of them.


fun identity(x) = x

class Semigroup a {
    fun combine(a, a) -> a
}

-- `empty` is the method whose class variable appears *only* in its result, so
-- nothing at the call site says which instance to use except the type it is
-- being used at. It is the reason a dictionary has to be passed rather than
-- chosen from an argument.
class Monoid a : Semigroup a {
    fun empty() -> a

    -- A default. One elaboration of this body serves every instance; the
    -- dictionary it belongs to is rebound as each one is built.
    fun repeat(x : a, n : Int) -> a {
        var acc = empty()
        var i = 0
        while i < n {
            acc = combine(acc, x)
            i = i + 1
        }
        return acc
    }
}

instance Semigroup Int {
    fun combine(x, y) = x + y
}

instance Monoid Int {
    fun empty() = 0
}

instance Semigroup String {
    fun combine(x, y) = x + y
}

instance Monoid String {
    fun empty() = ""
}

-- An instance with a context is a *function* from dictionaries to a
-- dictionary: `Semigroup (Array a)` is built from a `Semigroup a`.
instance Semigroup (Array a) : Semigroup a {
    fun combine(xs, ys) {
        let out = Array.new(len(xs) + len(ys))
        for x in xs {
            Array.push(out, x)
        }
        for y in ys {
            Array.push(out, y)
        }
        return out
    }
}

instance Monoid (Array a) : Semigroup a {
    fun empty() = []
}

-- `Functor`, and its `Option` and `Array` instances, moved to the prelude at
-- delta 45 -- `map` below is that class's method, reached through that
-- dictionary, and this file is now one of its callers rather than its author.

-- A method with a context of its own: `Monoid m` is per *call*, so it arrives
-- as an argument where the `Foldable` dictionary arrives by selection.
class Foldable t {
    fun foldMap[Monoid m](t a, fun(a) -> m) -> m

    fun fold[Monoid m](xs : t m) -> m = foldMap(xs, identity)
}

instance Foldable Array {
    fun foldMap(xs, g) {
        var acc = empty()
        for x in xs {
            acc = combine(acc, g(x))
        }
        return acc
    }
}

-- Declared, and reaching `combine` through the superclass of its own context.
fun squash[Monoid a](xs : Array a) -> a {
    var acc = empty()
    for x in xs {
        acc = combine(acc, x)
    }
    return acc
}

-- Inferred: `combine` demands its class, and the context travels out to the
-- scheme with a dictionary parameter behind it.
fun triple(x) = combine(x, combine(x, x))

fun render(xs : Array Int) -> String {
    return foldMap(xs, fun(n) = Int.toString(n) + " ")
}

fun main() {
    -- A `let` bound to a method: the dictionary has to arrive before there is
    -- a function at all.
    let plus = combine

    print(Int.toString(plus(20, 22)))
    print(Int.toString(squash([1, 2, 3, 4])))
    print(squash(["a", "b", "c"]))
    print(triple("ha"))
    print(Int.toString(triple(14)))
    print(repeat("ab", 3))
    print(render(fold([[1, 2], [3], [4, 5, 6]])))
    print(render(map([1, 2, 3], fun(n) = n * 10)))
    match map(Some(41), fun(n) = n + 1) {
        Some(n) -> print(Int.toString(n))
        None -> print("none")
    }
}
