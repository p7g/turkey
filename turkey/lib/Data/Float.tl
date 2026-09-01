module Data.Float (
    toString,
    parse,
    fromInt,
    truncate,
    floor,
    ceil,
    round,
    trunc,
    abs,
    isNaN,
    isInfinite,
    isFinite,
    nan,
    infinity,
    bits,
    fromBits,
    totalCompare,
    fmod,
    remainder,
)

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Int ()
import Data.Int as Int
import Data.Option.Type (Option(..))
import Data.Ordering.Type (Ordering(..))

-- `Float` is IEEE 754 binary64 in the default rounding mode, roundTiesToEven.
-- There is no dynamic rounding mode and no access to the exception flags
-- (PRIMITIVES.md 3).

fun toString(x : Float) -> String = Prim.floatToString(x)

-- Accepts everything `toString` produces, `Infinity` and `NaN` included --
-- which matters because those two have no literal syntax, so parsing is the
-- only way to write them down (PRIMITIVES.md 3.3).
fun parse(s : String) -> Option Float =
    if Prim.floatCanParse(s) { Some(Prim.floatParse(s)) } else { None }

-- Total, but exact only to 2^53; past that it rounds to nearest, ties to
-- even. Not a widening conversion, whatever the names suggest.
fun fromInt(n : Int) -> Float = Prim.intToFloat(n)

-- `None` for NaN, either infinity, and anything whose truncation falls
-- outside `Int`. The `Option` is not fussiness: LLVM's `fptosi` is *poison*
-- out of range, so an unchecked version would be real undefined behaviour in
-- the native backend (PRIMITIVES.md 3.4).
fun truncate(x : Float) -> Option Int =
    if Prim.floatFitsInt(x) { Some(Prim.floatTruncate(x)) } else { None }

fun floor(x : Float) -> Float = Prim.floatFloor(x)
fun ceil(x : Float) -> Float = Prim.floatCeil(x)
fun round(x : Float) -> Float = Prim.floatRound(x)
fun trunc(x : Float) -> Float = Prim.floatTrunc(x)

-- Clears the sign bit, so `abs(-0.0)` is `0.0` and `abs(NaN)` is a NaN. Not
-- written as `if x < 0` -- that comparison is false for NaN and for `-0.0`.
fun abs(x : Float) -> Float = fromBits(Int.and(bits(x), 9223372036854775807))

fun isNaN(x : Float) -> Bool = Prim.floatIsNaN(x)

fun infinity() -> Float = 1.0 / 0.0
fun nan() -> Float = 0.0 / 0.0

fun isInfinite(x : Float) -> Bool = x == infinity() || x == -infinity()
fun isFinite(x : Float) -> Bool = !isNaN(x) && !isInfinite(x)

-- The raw bit pattern. This is the *only* way to observe a NaN payload or
-- tell `0.0` from `-0.0`; nothing else in the language distinguishes them,
-- and there is deliberately no `Hash Float` built on it (PRIMITIVES.md 3.2c).
fun bits(x : Float) -> Int = Prim.floatBits(x)
fun fromBits(n : Int) -> Float = Prim.floatFromBits(n)

-- IEEE 754 `totalOrder`, as a three-way comparison:
--
--     -NaN < -Infinity < ... < -0.0 < +0.0 < ... < +Infinity < +NaN
--
-- Any sort or ordered structure that must not misbehave on NaN uses this
-- rather than `<`. The transformation is the standard one: the bit patterns
-- of non-negative doubles already increase monotonically, and flipping every
-- bit but the sign of a negative one reverses its run into place.
fun orderKey(x : Float) -> Int {
    let i = bits(x)
    Int.xor(i, Int.and(Int.shr(i, 63), 9223372036854775807))
}

fun totalCompare(x : Float, y : Float) -> Ordering {
    let a = orderKey(x)
    let b = orderKey(y)
    if a < b { return LT }
    if a > b { return GT }
    EQ
}

-- C's `fmod` (the remainder of truncating division) and IEEE's `remainder`
-- (of round-to-nearest division) disagree, and neither deserves the `%`
-- operator, so there is no `Rem Float` and both are spelled out.
fun fmod(a : Float, b : Float) -> Float = Prim.floatFmod(a, b)
fun remainder(a : Float, b : Float) -> Float = Prim.floatRemainder(a, b)

-- `Eq Float` and `Ord Float` are the IEEE comparison predicates, and they are
-- the one pair of instances in the language that does not satisfy its class's
-- laws: `Eq` is not reflexive (`NaN != NaN`) and `Ord` is not total (every
-- comparison with NaN is false). Turkey has one `Ord` and no `PartialOrd`
-- split, so there is nowhere in the type system to put the distinction; it is
-- written down here instead.
--
-- Every one of the four `Ord` methods is overridden. Inheriting the defaults
-- would derive `gte(x, y)` as `!lt(x, y)`, which makes `gte(NaN, 1.0)` true
-- while `lte(NaN, 1.0)` is false -- neither IEEE nor anything else
-- (PRIMITIVES.md 3.2a).
instance Eq Float { fun eq(x, y) = Prim.floatEq(x, y) }

instance Ord Float {
    fun lt(x, y) = Prim.floatLt(x, y)
    fun lte(x, y) = Prim.floatLte(x, y)
    fun gt(x, y) = Prim.floatGt(x, y)
    fun gte(x, y) = Prim.floatGte(x, y)
}

instance Add Float { fun add(x, y) = Prim.floatAdd(x, y) }
instance Sub Float { fun sub(x, y) = Prim.floatSub(x, y) }
instance Mul Float { fun mul(x, y) = Prim.floatMul(x, y) }
-- IEEE division: zero divisors give infinities and NaN, never a panic.
instance Div Float { fun div(x, y) = Prim.floatDiv(x, y) }
instance Neg Float { fun neg(x) = Prim.floatNeg(x) }
instance Show Float { fun show(x) = toString(x) }
