-- `Either` is the second thing `?` unwinds (delta 45). `Option` says that a
-- value is missing; `Either` says why, which is the whole difference and the
-- reason both exist.
--
-- The payload order is `Left` then `Right`, and the instances are biased to
-- the right: `map`, `pure` and `bind` all work on `Right` and pass a `Left`
-- through untouched. That is forced rather than chosen -- a class parameter is
-- one type, so `Monad (Either l)` fixes `l` and varies `r`, and the bias falls
-- out of which parameter is left over.
module Data.Either (Either(..), isRight, unwrapOr) where

import Data.Bool (Bool(..))

type Either l r = Left(l) | Right(r)

fun isRight(e : Either l r) -> Bool = match e {
    Right(_) -> True
    Left(_) -> False
}

fun unwrapOr(e : Either l r, fallback : r) -> r = match e {
    Right(x) -> x
    Left(_) -> fallback
}
