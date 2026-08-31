-- The three classes `?` is written against, used by hand (delta 45). Every
-- chain below is what `?` will desugar to in M12b, so this file is the thing
-- the sugar has to agree with -- and it is worth reading once in longhand
-- before any of it is hidden.
--
-- Nothing here needed a change to the checker. `Monad m` is a class whose
-- variable has kind `* -> *`, discovered from `m a` in `bind`'s signature; the
-- machinery is M4's kinds, M5's classes and M6's dictionaries, unchanged.


-- `Option`: failure with nothing to say.
fun half(n : Int) -> Option Int =
    if n % 2 == 0 { Some(n / 2) } else { None }

fun quarter(n : Int) -> Option Int = bind(half(n), half)

-- `Either l`: failure that says why. The class parameter is `Either l`, so `l`
-- is fixed and `r` varies -- which is the whole reason every `Either` is
-- right-biased.
fun checked(n : Int) -> Either String Int =
    if n == 0 { Left("zero") } else { Right(100 / n) }

fun twoSteps(n : Int) -> Either String Int =
    bind(checked(n), fun(x) = bind(checked(x), fun(y) = Right(x + y)))

-- `Array`: the list monad. Its bind invokes the continuation once per element,
-- which is what makes `?` a bind rather than an early return -- there is no
-- "the" value to return early with.
fun pairs(xs : Array Int, ys : Array Int) -> Array Int =
    bind(xs, fun(x) = bind(ys, fun(y) = pure(x * y)))

-- One function over all three. `pure` is what makes this writable: without it
-- there is no way to get back into `m` from a plain value.
fun thrice[Monad m](x : m a, g : fun(a) -> m a) -> m a =
    bind(x, fun(u) = bind(g(u), fun(v) = g(v)))

-- The superclass is reachable from the subclass's context, so `[Monad m]`
-- alone is enough to call `map` and `pure`.
fun bump[Monad m](x : m Int) -> m Int = map(x, fun(n) = n + 1)

fun main() {
    print(quarter(20))
    print(quarter(6))

    print(twoSteps(4))
    print(twoSteps(0))

    print(pairs([1, 2, 3], [10, 20]))
    print(pairs([], [10, 20]))

    -- The same `thrice` at three different instances.
    print(thrice(Some(80), half))
    print(thrice(Some(81), half))
    print(thrice([1, 2], fun(n) = [n, n * 10]))

    print(bump(Some(41)))
    print(bump([1, 2, 3]))

    -- `map` and `pure` on their own, at an instance the prelude ships.
    print(map(Some(20), fun(n) = n * 2))
    print(map([1, 2, 3], fun(n) = n * n))

    let lifted : Option Int = pure(7)
    print(lifted)

    -- `Either`'s functions, which `Data.Either` exports qualified.
    print(Either.isRight(checked(5)))
    print(Either.unwrapOr(checked(0), -1))
}
