module Data.Char (fromInt, toInt, toString) where

fun fromInt(code : Int) -> Char = Prim.charFromInt(code)

fun toInt(c : Char) -> Int = Prim.charToInt(c)

fun toString(c : Char) -> String = Prim.charToString(c)
