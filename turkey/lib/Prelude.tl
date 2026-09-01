module Prelude (
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
    Array,
    module Array,
    module Bool,
    module Char,
    module Either,
    module Float,
    module Int,
    module Option,
    module String,
)

import Std.Classes

import Data.Bool (Bool(..))
import Data.Either (Either(..))
import Data.Option (Option(..))
import Data.Array (Array)

import Data.Array as Array
import Data.Bool as Bool
import Data.Char as Char
import Data.Either as Either
import Data.Float as Float
import Data.Int as Int
import Data.Option as Option
import Data.String as String
import Data.Tuple ()

type Flow a b r = Fall(a) | Brk(b) | Cont | Ret(r)
