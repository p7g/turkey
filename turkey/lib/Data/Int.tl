module Data.Int (toString)

import Std.Classes
fun toString(n : Int) -> String = Prim.intToString(n)

instance Eq Int { fun eq(x, y) = Prim.intEq(x, y) }
instance Ord Int { fun lt(x, y) = Prim.intLt(x, y) }
instance Add Int { fun add(x, y) = Prim.intAdd(x, y) }
instance Sub Int { fun sub(x, y) = Prim.intSub(x, y) }
instance Mul Int { fun mul(x, y) = Prim.intMul(x, y) }
instance Div Int { fun div(x, y) = Prim.intDiv(x, y) }
instance Rem Int { fun rem(x, y) = Prim.intRem(x, y) }
instance Neg Int { fun neg(x) = Prim.intNeg(x) }
instance Show Int { fun show(x) = toString(x) }

instance Hash Int {
    fun hashInto(i, h) = update(h, i)
}
