module Data.Ordering (Ordering(..), reverse, rank, isLess, isEqual, isGreater)

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Int ()
import Data.Ordering.Type (Ordering(..))

-- Three-way comparison, which the class hierarchy was missing entirely.
--
-- `Ord` answers in `Bool`, so every ordered algorithm asks twice: once for
-- `lt` and once for `eq`. That is merely wasteful for `Int`, but for `Float`
-- it is not expressible at all -- IEEE comparison has four outcomes, not two,
-- and the fourth (unordered) is what `Float.totalCompare` exists to resolve
-- (PRIMITIVES.md 3.2b).

fun reverse(o : Ordering) -> Ordering = match o {
    LT -> GT
    EQ -> EQ
    GT -> LT
}

fun rank(o : Ordering) -> Int = match o {
    LT -> 0
    EQ -> 1
    GT -> 2
}

fun isLess(o : Ordering) -> Bool = match o {
    LT -> True
    _ -> False
}

fun isEqual(o : Ordering) -> Bool = match o {
    EQ -> True
    _ -> False
}

fun isGreater(o : Ordering) -> Bool = match o {
    GT -> True
    _ -> False
}

instance Eq Ordering { fun eq(x, y) = rank(x) == rank(y) }
instance Ord Ordering { fun lt(x, y) = rank(x) < rank(y) }

instance Show Ordering {
    fun show(o) = match o {
        LT -> "LT"
        EQ -> "EQ"
        GT -> "GT"
    }
}

instance Hash Ordering {
    fun hashInto(o, h) = update(h, rank(o))
}
