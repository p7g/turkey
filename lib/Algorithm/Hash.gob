module Algorithm.Hash (FNV1a, fnv1a)

import Prelude ()
import Std.Classes
import Data.Int ()
import Data.Int as Int

-- The mixer has to *wrap*, and now says so.
--
-- The old DJB2 here computed `h * 33 + x` and then masked back to 32 bits on
-- every step. That was not a design choice about hash quality; it was the only
-- way to keep an unbounded `Int` from growing without limit. With `Int` at 64
-- bits and `*` trapping on overflow, the masking version would panic the
-- moment a full-width key reached it -- so the arithmetic moves to the
-- explicit wrapping primitives, which is what a mixer wanted all along
-- (PRIMITIVES.md 1.4).
--
-- FNV-1a over 64 bits, rather than a 32-bit DJB2: the accumulator is now
-- honestly 64 bits wide, so there is no reason to throw half of it away.

type FNV1a = FNV1a { h : Int }

-- The 64-bit FNV offset basis, 14695981039346656037, is larger than `Int`'s
-- maximum, so it is written as the two's-complement value it denotes.
fun fnv1a() -> FNV1a = FNV1a { h = -3750763034362895579 }

instance Hasher FNV1a {
    fun digest(d) = d.h
    fun update(d, x) {
        -- Byte at a time, low end first, so that the avalanche actually
        -- happens; xor-ing a whole word in once would leave the high bytes
        -- barely mixed.
        for var i = 0; i < 64; i = i + 8 {
            d.h = Int.xor(d.h, Int.and(Int.shr(x, i), 255))
            d.h = Int.mulWrapping(d.h, 1099511628211)
        }
    }
}
