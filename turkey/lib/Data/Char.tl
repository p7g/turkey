module Data.Char (fromInt, toInt, toString, isAscii, maxValue)

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Int ()
import Data.Option.Type (Option(..))

-- A `Char` is a Unicode *scalar value*: 0..10FFFF with the surrogate range
-- D800..DFFF excluded. Not a code point -- the difference is exactly the
-- surrogates, and it matters because a surrogate `Char` could be written into
-- a `String` that then would not be well-formed UTF-8 (PRIMITIVES.md 5).
--
-- A `Char` is also *not* a user-perceived character. That is a grapheme
-- cluster, which may be several scalar values (`e` plus a combining acute, a
-- flag emoji, a family emoji). So `String.codePoints` is not "the characters
-- of the string", and the word "character" is avoided in both.

fun fromInt(code : Int) -> Option Char =
    if Prim.charIsScalar(code) { Some(Prim.charFromInt(code)) } else { None }

fun toInt(c : Char) -> Int = Prim.charToInt(c)

fun toString(c : Char) -> String = Prim.charToString(c)

fun isAscii(c : Char) -> Bool = toInt(c) < 128

fun maxValue() -> Char = Prim.charFromInt(1114111)

-- There is deliberately no `toUpper` / `toLower`. Case mapping is not a
-- per-scalar-value function: `ss` uppercases to two characters, Greek final
-- sigma depends on position in the word, and Turkish dotless i maps
-- differently from every other locale's i. Case therefore lives on `String`,
-- and only once there is a Unicode table to do it correctly (PRIMITIVES.md
-- 4.5). Shipping a per-`Char` version first would be a compatibility trap.

instance Eq Char { fun eq(x, y) = Prim.charEq(x, y) }
instance Ord Char { fun lt(x, y) = Prim.charLt(x, y) }
instance Show Char { fun show(x) = toString(x) }

instance Hash Char {
    fun hashInto(c, h) = update(h, toInt(c))
}
