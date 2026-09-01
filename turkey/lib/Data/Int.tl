module Data.Int (
    toString,
    minValue,
    maxValue,
    compare,
    mod,
    abs,
    addWrapping,
    subWrapping,
    mulWrapping,
    negWrapping,
    addChecked,
    subChecked,
    mulChecked,
    negChecked,
    and,
    or,
    xor,
    not,
    shl,
    shr,
)

import Prelude ()
import Std.Classes
import Data.Bool ()
import Data.Bool.Type (Bool(..))
import Data.Option.Type (Option(..))
import Data.Ordering.Type (Ordering(..))

fun toString(n : Int) -> String = Prim.intToString(n)

-- `Int` is two's-complement 64-bit, so these are constants -- but the low one
-- cannot be *written*. `-9223372036854775808` lexes as a unary minus applied
-- to `9223372036854775808`, which is not an `Int`, so the literal is rejected
-- before the negation can rescue it. Subtracting from the representable
-- maximum is the way round it.
fun minValue() -> Int = -9223372036854775807 - 1
fun maxValue() -> Int = 9223372036854775807

fun compare(a : Int, b : Int) -> Ordering {
    if a < b { return LT }
    if a > b { return GT }
    EQ
}

-- Floored, so the result takes the sign of the *divisor* and is therefore a
-- valid index for every positive `b`. `%` is remainder and takes the sign of
-- the dividend; the difference bites exactly where people reach for it, which
-- is bucket selection (PRIMITIVES.md 1.2).
fun mod(a : Int, b : Int) -> Int {
    let r = a % b
    if r != 0 && (r < 0) != (b < 0) { r + b } else { r }
}

-- Panics on `minValue()`, which has no positive counterpart. That is the
-- trapping behaviour of `neg`, not an extra rule.
fun abs(n : Int) -> Int = if n < 0 { -n } else { n }

fun addWrapping(a : Int, b : Int) -> Int = Prim.intAddWrapping(a, b)
fun subWrapping(a : Int, b : Int) -> Int = Prim.intSubWrapping(a, b)
fun mulWrapping(a : Int, b : Int) -> Int = Prim.intMulWrapping(a, b)
fun negWrapping(a : Int) -> Int = Prim.intNegWrapping(a)

-- The checked forms are written in terms of the wrapping ones and a proof
-- read back off the result, because the language has no way to catch the
-- panic that `+` would raise. `a + b` overflowed exactly when the wrapped sum
-- disagrees with the true sign rule: operands of the same sign must produce
-- that sign.
fun addChecked(a : Int, b : Int) -> Option Int {
    let sum = Prim.intAddWrapping(a, b)
    if (a < 0) == (b < 0) && (sum < 0) != (a < 0) { None } else { Some(sum) }
}

fun subChecked(a : Int, b : Int) -> Option Int {
    let diff = Prim.intSubWrapping(a, b)
    if (a < 0) != (b < 0) && (diff < 0) != (a < 0) { None } else { Some(diff) }
}

fun mulChecked(a : Int, b : Int) -> Option Int {
    -- `a == 0` is the one case the divide-back check cannot be run for, and
    -- `minValue() * -1` is the one case where running it would *itself* trap:
    -- the wrapped product is `minValue()` again, so `product / a` would be
    -- `minValue() / -1`. Both are answered before the division happens.
    if a == 0 || b == 0 { return Some(0) }
    if a == -1 && b == minValue() { return None }
    if b == -1 && a == minValue() { return None }
    let product = Prim.intMulWrapping(a, b)
    -- Dividing back is the cheap check that does not need a wider type: it is
    -- exact whenever the multiplication did not overflow.
    if product / a == b { Some(product) } else { None }
}

fun negChecked(a : Int) -> Option Int =
    if a == minValue() { None } else { Some(-a) }

fun and(a : Int, b : Int) -> Int = Prim.intAnd(a, b)
fun or(a : Int, b : Int) -> Int = Prim.intOr(a, b)
fun xor(a : Int, b : Int) -> Int = Prim.intXor(a, b)
fun not(a : Int) -> Int = Prim.intNot(a)

-- Both panic outside 0..63 rather than masking the shift amount, so that
-- `x << 64` is a reported bug and not a silent identity. `shr` is arithmetic:
-- the sign bit replicates.
fun shl(a : Int, by : Int) -> Int = Prim.intShl(a, by)
fun shr(a : Int, by : Int) -> Int = Prim.intShr(a, by)

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
