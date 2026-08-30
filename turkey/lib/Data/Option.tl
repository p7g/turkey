-- `Option` is the price of iteration being a cursor: `next` has to be able to
-- answer "no more" (delta 33). It is declared here, beside the functions over
-- it, rather than in the Prelude.
module Data.Option (Option(..), isSome, unwrapOr) where

import Data.Bool (Bool(..))

type Option a = None | Some(a)

fun isSome(o : Option a) -> Bool = match o {
    Some(_) -> True
    None -> False
}

fun unwrapOr(o : Option a, fallback : a) -> a = match o {
    Some(x) -> x
    None -> fallback
}
