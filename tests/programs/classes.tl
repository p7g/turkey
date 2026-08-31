-- Classes and instances, checked but not yet run: a method call still has no
-- dictionary to resolve it, so `main` stays out of the way and the point of
-- this program is `classes.types` beside it.


type Either l r = Left(l) | Right(r)

fun identity(x) = x

class Semigroup a {
    fun combine(a, a) -> a
}

-- A superclass uses `:`, not the `[...]` that constrains a function. The two
-- positions are disjoint, so `[...]` keeps meaning exactly one thing: a
-- context on a *value*'s type.
class Monoid a : Semigroup a {
    fun empty() -> a

    -- A method with a body is a default. Its type is still the class's, read
    -- off the annotations it is required to carry.
    fun double(x : a) -> a = combine(x, x)
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

-- The instance head is an `atype`, so a partial application parenthesizes.
-- `Either l` is what makes the class variable of kind `* -> *` reachable.
class Functor f {
    fun map(f a, fun(a) -> b) -> f b
}

instance Functor Option {
    fun map(opt, g) = match opt {
        Some(x) -> Some(g(x))
        None -> None
    }
}

instance Functor (Either l) {
    fun map(e, g) = match e {
        Right(x) -> Right(g(x))
        Left(y) -> Left(y)
    }
}

-- `[Monoid a]` constrains the `a` that the annotation below introduces; it is
-- a context, not a binder.
fun squash[Monoid a](xs : Array a) -> a {
    var acc = empty()
    for x in xs {
        acc = combine(acc, x)
    }
    return acc
}

-- Inferred rather than declared: `combine` demands its class, and the context
-- travels out to the scheme the same way a `HasField` does.
fun triple(x) = combine(x, combine(x, x))

fun main() {
    print(Int.toString(identity(41) + 1))
}
