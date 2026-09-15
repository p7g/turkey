module Data.Bool (Bool(..), toString, not)

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))

fun toString(b : Bool) -> String = match b {
    True -> "True"
    False -> "False"
}

fun not(b : Bool) -> Bool = match b {
    True -> False
    False -> True
}

instance Eq Bool { fun eq(x, y) = Prim.boolEq(x, y) }
instance Ord Bool { fun lt(x, y) = Prim.boolLt(x, y) }
instance Show Bool { fun show(x) = toString(x) }
