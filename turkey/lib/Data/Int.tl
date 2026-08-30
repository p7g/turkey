module Data.Int (toString) where

-- Arithmetic and comparison are class methods (delta 32); this is what is left
-- that has a name of its own.
fun toString(n : Int) -> String = Prim.intToString(n)
