module Std.Classes (
    Eq(..),
    Ord(..),
    Add(..),
    Sub(..),
    Mul(..),
    Div(..),
    Rem(..),
    Neg(..),
    Show(..),
    Functor(..),
    Applicative(..),
    Monad(..),
    Iterator(..),
    Index(..),
    Length(..),
    Semigroup(..),
    Monoid(..),
    Foldable(..),
    print,
    write,
    error,
)

import Data.Bool.Type (Bool(..))
import Data.Option.Type (Option(..))

class Eq a {
    fun eq(a, a) -> Bool
    fun ne(x : a, y : a) -> Bool = !eq(x, y)
}

class Ord a : Eq a {
    fun lt(a, a) -> Bool
    fun lte(x : a, y : a) -> Bool = lt(x, y) || eq(x, y)
    fun gt(x : a, y : a) -> Bool = lt(y, x)
    fun gte(x : a, y : a) -> Bool = !lt(x, y)
}

class Add a { fun add(a, a) -> a }
class Sub a { fun sub(a, a) -> a }
class Mul a { fun mul(a, a) -> a }
class Div a { fun div(a, a) -> a }
class Rem a { fun rem(a, a) -> a }
class Neg a { fun neg(a) -> a }

class Show a { fun show(a) -> String }

class Functor f {
    fun map(f a, fun(a) -> b) -> f b
}

class Applicative f : Functor f {
    fun pure(a) -> f a
}

class Monad m : Applicative m {
    fun bind(m a, fun(a) -> m b) -> m b
}

class Iterator c {
    type Item c
    type Cursor c

    fun iter(c) -> Cursor c
    fun next(c, Cursor c) -> Option (Item c)
}

class Index c {
    type IndexKey c
    type IndexItem c

    fun get(c, IndexKey c) -> IndexItem c
    fun set(c, IndexKey c, IndexItem c) -> Unit
}

class Length c { fun len(c) -> Int }

class Semigroup a { fun combine(a, a) -> a }

class Monoid a : Semigroup a { fun empty() -> a }

class Foldable f {
    fun foldMap[Monoid m](f a, fun(a) -> m) -> m
    fun fold[Monoid m](xs : f m) -> m = foldMap(xs, fun(x) { return x })
}

fun print(x) = Prim.print(show(x))
fun write(x) = Prim.write(show(x))
fun error(message : String) -> a = Prim.error(message)
