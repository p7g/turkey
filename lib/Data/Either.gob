module Data.Either (Either(..), isRight, unwrapOr, map, fold)

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))

type Either l r = Left(l) | Right(r)

fun isRight(e : Either l r) -> Bool = match e {
    Right(_) -> True
    Left(_) -> False
}

fun unwrapOr(e : Either l r, fallback : r) -> r = match e {
    Right(x) -> x
    Left(_) -> fallback
}

fun map(e : Either l a, f : fun(a) -> b) -> Either l b = match e {
    Right(x) -> Right(f(x))
    Left(x) -> Left(x)
}

fun fold(e : Either l r, onLeft : fun(l) -> a, onRight : fun(r) -> a) -> a = match e {
    Left(x) -> onLeft(x)
    Right(x) -> onRight(x)
}

instance Functor (Either l) {
    fun map(e, f) = map(e, f)
}

instance Applicative (Either l) { fun pure(x) = Right(x) }

instance Monad (Either l) {
    fun bind(e, g) = match e {
        Right(x) -> g(x)
        Left(y) -> Left(y)
    }
}

instance Show (Either l r) : Show l, Show r {
    fun show(e) = match e {
        Left(x) -> Prim.stringConcat(Prim.stringConcat("Left(", show(x)), ")")
        Right(x) -> Prim.stringConcat(Prim.stringConcat("Right(", show(x)), ")")
    }
}

instance Eq (Either l r) : Eq l, Eq r {
    fun eq(a, b) = match (a, b) {
        (Left(l1), Left(l2)) -> l1 == l2
        (Right(r1), Right(r2)) -> r1 == r2
        _ -> False
    }
}

instance Hash (Either l r) : Hash l, Hash r {
    fun hashInto(e, h) = match e {
        Left(x) -> {
            update(h, 0)
            hashInto(x, h)
        }
        Right(x) -> {
            update(h, 1)
            hashInto(x, h)
        }
    }
}
