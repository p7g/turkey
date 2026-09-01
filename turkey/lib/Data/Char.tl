module Data.Char (fromInt, toInt, toString)

import Prelude ()
import Std.Classes

fun fromInt(code : Int) -> Char = Prim.charFromInt(code)

fun toInt(c : Char) -> Int = Prim.charToInt(c)

fun toString(c : Char) -> String = Prim.charToString(c)

instance Eq Char { fun eq(x, y) = Prim.charEq(x, y) }
instance Ord Char { fun lt(x, y) = Prim.charLt(x, y) }
instance Show Char { fun show(x) = toString(x) }

instance Hash Char {
    fun hashInto(c, h) = update(h, toInt(c))
}
