module Prelude (
    const,
    print,
    write,
    error,
    Eq(..),
    Ord(..),
    Add(..),
    Sub(..),
    Mul(..),
    Div(..),
    Rem(..),
    Neg(..),
    Show(..),
    Hash(..),
    Functor(..),
    Applicative(..),
    Monad(..),
    Iterator(..),
    Index(..),
    Length(..),
    Semigroup(..),
    Monoid(..),
    Foldable(..),
    Bool(..),
    Option(..),
    Either(..),
    Ordering(..),
    Array,
    Map,
    module Array,
    module Bool,
    module Byte,
    module Char,
    module Either,
    module Float,
    module Int,
    module Map,
    module Option,
    module Ordering,
    module String,
)

import Std.Classes

import Algorithm.Hash (fnv1a)
import Data.Bool (Bool(..))
import Data.Either (Either(..))
import Data.Option (Option(..))
import Data.Ordering (Ordering(..))
import Data.Array (Array)
import Data.Map (Map)

import Data.Array as Array
import Data.Bool as Bool
import Data.Byte as Byte
import Data.Char as Char
import Data.Either as Either
import Data.Float as Float
import Data.Int as Int
import Data.Map as Map
import Data.Option as Option
import Data.Ordering as Ordering
import Data.String as String
import Data.Tuple ()

type Flow a b r = Fall(a) | Brk(b) | Cont | Ret(r)

fun hash[Hash a](a : a) -> Int {
    let h = fnv1a()
    hashInto(a, h)
    digest(h)
}
