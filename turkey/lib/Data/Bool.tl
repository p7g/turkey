-- design.md section 8.4. `Bool` is declared here rather than in the Prelude
-- because it is the type this module is about, and an instance for it should
-- be able to live beside it (delta 43). Nothing about it is built in: `True`
-- and `False` are ordinary constructors, and `if` demands this type by name
-- (delta 36).
module Data.Bool (Bool(..), toString, not) where

type Bool = False | True

fun toString(b : Bool) -> String = match b {
    True -> "True"
    False -> "False"
}

fun not(b : Bool) -> Bool = match b {
    True -> False
    False -> True
}
