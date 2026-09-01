module Data.String (length, chars, fromChars, join)

import Std.Classes
import Data.Array (Array)
import Data.Array as Array
import Data.Bool.Type (Bool(..))
import Data.Char as Char

fun length(s : String) -> Int = Prim.stringLength(s)

fun chars(s : String) -> Array Char = Prim.stringChars(s)

fun fromChars(xs : Array Char) -> String {
    var out = ""
    for c in xs { out = Prim.stringConcat(out, Char.toString(c)) }
    out
}

fun join(parts : Array String, separator : String) -> String {
    var out = ""
    var rest = False
    for part in parts {
        if rest { out = Prim.stringConcat(out, separator) }
        out = Prim.stringConcat(out, part)
        rest = True
    }
    out
}

instance Eq String { fun eq(x, y) = Prim.stringEq(x, y) }
instance Ord String { fun lt(x, y) = Prim.stringLt(x, y) }
instance Add String { fun add(x, y) = Prim.stringConcat(x, y) }
instance Show String { fun show(x) = x }
instance Semigroup String { fun combine(x, y) = add(x, y) }
instance Monoid String { fun empty() = "" }
instance Length String { fun len(s) = length(s) }
