module Data.Float (toString)

import Prelude ()
import Std.Classes

fun toString(x : Float) -> String = Prim.floatToString(x)

instance Eq Float { fun eq(x, y) = Prim.floatEq(x, y) }
instance Ord Float { fun lt(x, y) = Prim.floatLt(x, y) }
instance Add Float { fun add(x, y) = Prim.floatAdd(x, y) }
instance Sub Float { fun sub(x, y) = Prim.floatSub(x, y) }
instance Mul Float { fun mul(x, y) = Prim.floatMul(x, y) }
instance Div Float { fun div(x, y) = Prim.floatDiv(x, y) }
instance Neg Float { fun neg(x) = Prim.floatNeg(x) }
instance Show Float { fun show(x) = toString(x) }
