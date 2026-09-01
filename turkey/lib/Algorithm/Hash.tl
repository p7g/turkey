module Algorithm.Hash (djb2)

import Std.Classes
import Data.Int ()

type DJB2 = DJB2 { h : Int }

fun djb2() -> DJB2 = DJB2 { h = 5381 }

instance Hasher DJB2 {
    fun digest(d) = d.h
    fun update(d, x) {
        d.h = d.h * 33 + x
        d.h = d.h % 4294967296 -- truncate to 32 bits
    }
}
