module Data.Byte (
    toInt,
    fromInt,
    truncate,
    minValue,
    maxValue,
    and,
    or,
    xor,
    not,
    shl,
    shr,
)

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Int ()
import Data.Int as Int
import Data.Option.Type (Option(..))

-- `Byte` is an unsigned 8-bit integer, and it exists for exactly one reason:
-- to be the element of `Array Byte`, which is how bytes enter and leave a
-- `String` (`String.toBytes` / `String.fromBytes`) and how any future I/O
-- layer will speak.
--
-- It is therefore a *storage* type, and it has no arithmetic instances at
-- all -- no `Add`, `Sub`, `Mul`, `Div`, `Neg`. Byte arithmetic goes through
-- `Int`, which dodges the whole "does `u8 + u8` wrap, trap, or promote"
-- question. Adding instances later is backward-compatible; removing them
-- would not be (PRIMITIVES.md 2).

fun toInt(b : Byte) -> Int = Prim.byteToInt(b)

fun fromInt(n : Int) -> Option Byte =
    if n >= 0 && n <= 255 { Some(Prim.byteFromInt(n)) } else { None }

-- The low eight bits, total and explicitly lossy. The name is the warning.
fun truncate(n : Int) -> Byte = Prim.byteFromInt(Int.and(n, 255))

fun minValue() -> Byte = Prim.byteFromInt(0)
fun maxValue() -> Byte = Prim.byteFromInt(255)

fun and(a : Byte, b : Byte) -> Byte =
    Prim.byteFromInt(Int.and(toInt(a), toInt(b)))

fun or(a : Byte, b : Byte) -> Byte =
    Prim.byteFromInt(Int.or(toInt(a), toInt(b)))

fun xor(a : Byte, b : Byte) -> Byte =
    Prim.byteFromInt(Int.xor(toInt(a), toInt(b)))

fun not(a : Byte) -> Byte = Prim.byteFromInt(Int.xor(toInt(a), 255))

-- Shifting happens in `Int` and the result is narrowed, so bits shifted off
-- the top are discarded. The shift amount is `Int.shl`'s, so it panics outside
-- 0..63 rather than masking.
fun shl(a : Byte, by : Int) -> Byte = truncate(Int.shl(toInt(a), by))
fun shr(a : Byte, by : Int) -> Byte = Prim.byteFromInt(Int.shr(toInt(a), by))

instance Eq Byte { fun eq(x, y) = Prim.byteEq(x, y) }
instance Ord Byte { fun lt(x, y) = Prim.byteLt(x, y) }
instance Show Byte { fun show(b) = Prim.intToString(toInt(b)) }

instance Hash Byte {
    fun hashInto(b, h) = update(h, toInt(b))
}
