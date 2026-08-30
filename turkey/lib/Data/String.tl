module Data.String (length, chars) where

-- A `String` is not an `Array Char`, so its length is a function rather than
-- the `.length` field an array has.
fun length(s : String) -> Int = Prim.stringLength(s)

fun chars(s : String) -> Array Char = Prim.stringChars(s)
