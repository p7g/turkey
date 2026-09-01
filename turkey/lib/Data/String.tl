module Data.String (
    Bytes,
    CodePoints,
    Builder,
    byteLength,
    isEmpty,
    codePointCount,
    bytes,
    codePoints,
    fromCodePoints,
    toBytes,
    fromBytes,
    builder,
    push,
    build,
    join,
    startsWith,
    endsWith,
    contains,
    stripPrefix,
    stripSuffix,
    split,
    splitOnce,
    replace,
    repeat,
    trim,
    trimStart,
    trimEnd,
)

import Prelude ()
import Std.Classes
import Data.Array (Array)
import Data.Array as Array
import Data.Bool.Type (Bool(..))
import Data.Byte ()
import Data.Char as Char
import Data.Int ()
import Data.Option.Type (Option(..))
import Data.Tuple ()

-- A `String` is an immutable sequence of bytes that is guaranteed to be
-- well-formed UTF-8. That is the whole model, and every operation here is
-- judged by whether it preserves the invariant. It is not "a sequence of
-- characters" and not "a sequence of code points" -- the code points are a
-- *view*, and so are the bytes, and later so will the graphemes be
-- (PRIMITIVES.md 4).
--
-- Note what is absent. There is no `length`, because "how long is this
-- string" has three defensible answers and the old one silently gave the
-- code-point count; there is no `Index String` and no `Length String`, for
-- the same reason; and no byte offset ever reaches the surface language, so
-- there is no way to construct a slice that cuts a character in half.

-- O(1). The only cheap count a string has.
fun byteLength(s : String) -> Int = Prim.stringByteLength(s)

fun isEmpty(s : String) -> Bool = byteLength(s) == 0

-- Deliberately O(n) and deliberately spelled out, so that it can never be
-- mistaken for the cheap one.
fun codePointCount(s : String) -> Int {
    var n = 0
    for _c in codePoints(s) { n = n + 1 }
    n
}

-- -- views -------------------------------------------------------------------
--
-- Lazy, not materialized. The old `chars` built a whole `Array Char` per call;
-- these walk the encoding in place. Making them views is also what leaves room
-- for `graphemes`, which cannot be an array of a primitive at all, since a
-- grapheme cluster is a substring rather than a scalar value.

type Bytes = Bytes { source : String }
type CodePoints = CodePoints { source : String }

-- Both views walk byte offsets, so they share one cursor. The offset is
-- private to this module; nothing exported ever returns one.
type ByteCursor = ByteCursor { at : Int }

fun bytes(s : String) -> Bytes = Bytes { source = s }
fun codePoints(s : String) -> CodePoints = CodePoints { source = s }

instance Iterator Bytes {
    type Item = Byte
    type Cursor = ByteCursor
    fun iter(b) = ByteCursor { at = 0 }
    fun next(b, cur) {
        if cur.at >= Prim.stringByteLength(b.source) { return None }
        let x = Prim.stringByteAt(b.source, cur.at)
        cur.at = cur.at + 1
        Some(x)
    }
}

instance Iterator CodePoints {
    type Item = Char
    type Cursor = ByteCursor
    fun iter(cp) = ByteCursor { at = 0 }
    fun next(cp, cur) {
        if cur.at >= Prim.stringByteLength(cp.source) { return None }
        let c = Prim.stringDecodeAt(cp.source, cur.at)
        cur.at = Prim.stringNextIndex(cp.source, cur.at)
        Some(c)
    }
}

-- -- interchange --------------------------------------------------------------
--
-- The only door in and out, and the entire reason `Byte` exists. Going out is
-- total; coming in is checked, because arbitrary bytes are not a string.

fun toBytes(s : String) -> Array Byte = Prim.stringToBytes(s)

fun fromBytes(bs : Array Byte) -> Option String =
    if Prim.stringIsValidUtf8(bs) { Some(Prim.stringFromBytes(bs)) } else { None }

-- -- building ----------------------------------------------------------------
--
-- `+` in a loop is quadratic. A builder collects the pieces and joins once,
-- which is what `fromCodePoints` and `join` are both written on.

type Builder = Builder { parts : Array String }

fun builder() -> Builder = Builder { parts = Array.new(8) }

fun push(b : Builder, part : String) -> Unit = Array.push(b.parts, part)

fun build(b : Builder) -> String = Prim.stringConcatAll(b.parts)

fun fromCodePoints(cs : Array Char) -> String {
    let b = builder()
    for c in cs { push(b, Char.toString(c)) }
    build(b)
}

fun join(parts : Array String, separator : String) -> String {
    let b = builder()
    var rest = False
    for part in parts {
        if rest { push(b, separator) }
        push(b, part)
        rest = True
    }
    build(b)
}

fun repeat(s : String, times : Int) -> String {
    let b = builder()
    for var i = 0; i < times; i = i + 1 { push(b, s) }
    build(b)
}

-- -- searching and cutting ----------------------------------------------------
--
-- Search-shaped, with no index type. `Prim.stringFind` answers in byte
-- offsets and `Prim.stringSlice` consumes them, but neither escapes this
-- module: an offset a program could compute is the one decision that cannot
-- be taken back, because `slice` would then have to either validate or admit
-- ill-formed strings. An opaque `String.Index` can be added when something
-- genuinely needs it (PRIMITIVES.md 4.3).
--
-- UTF-8 is self-synchronizing, so a well-formed needle only ever matches at a
-- character boundary. That is why searching bytes for bytes is safe here.

-- Both ask the byte search, rather than slicing off a prefix of the right
-- length and comparing. Slicing would be wrong: `startsWith("é", "a")` would
-- cut the two-byte `é` in half, and `Prim.stringSlice` panics on a
-- non-boundary offset rather than inventing a replacement character. The
-- search cannot make that mistake, because a well-formed needle only ever
-- matches at a boundary.
fun startsWith(s : String, prefix : String) -> Bool =
    Prim.stringFind(s, prefix, 0) == 0

fun endsWith(s : String, suffix : String) -> Bool =
    Prim.stringRfind(s, suffix) == byteLength(s) - byteLength(suffix)

fun contains(s : String, needle : String) -> Bool =
    Prim.stringFind(s, needle, 0) >= 0

fun stripPrefix(s : String, prefix : String) -> Option String =
    if startsWith(s, prefix) {
        Some(Prim.stringSlice(s, byteLength(prefix), byteLength(s)))
    } else {
        None
    }

fun stripSuffix(s : String, suffix : String) -> Option String =
    if endsWith(s, suffix) {
        Some(Prim.stringSlice(s, 0, byteLength(s) - byteLength(suffix)))
    } else {
        None
    }

fun split(s : String, separator : String) -> Array String {
    let out = Array.new(4)
    -- Splitting on the empty string means "between every pair of characters",
    -- and the characters here are code points.
    if isEmpty(separator) {
        for c in codePoints(s) { Array.push(out, Char.toString(c)) }
        return out
    }
    let width = byteLength(separator)
    var start = 0
    loop {
        let at = Prim.stringFind(s, separator, start)
        if at < 0 { break }
        Array.push(out, Prim.stringSlice(s, start, at))
        start = at + width
    }
    Array.push(out, Prim.stringSlice(s, start, byteLength(s)))
    out
}

fun splitOnce(s : String, separator : String) -> Option (String, String) {
    if isEmpty(separator) { return None }
    let at = Prim.stringFind(s, separator, 0)
    if at < 0 { return None }
    Some((
        Prim.stringSlice(s, 0, at),
        Prim.stringSlice(s, at + byteLength(separator), byteLength(s)),
    ))
}

fun replace(s : String, from : String, to : String) -> String =
    if isEmpty(from) { s } else { join(split(s, from), to) }

-- ASCII whitespace only, and named as a limitation rather than hidden: the
-- full Unicode White_Space property needs a table, and tables belong in the
-- `Data.Unicode` that does not exist yet.
fun isSpace(c : Char) -> Bool {
    let n = Char.toInt(c)
    n == 32 || n == 9 || n == 10 || n == 13 || n == 11 || n == 12
}

fun trimStart(s : String) -> String {
    let n = byteLength(s)
    var i = 0
    while i < n {
        if !isSpace(Prim.stringDecodeAt(s, i)) { break }
        i = Prim.stringNextIndex(s, i)
    }
    Prim.stringSlice(s, i, n)
}

fun trimEnd(s : String) -> String {
    let n = byteLength(s)
    var i = 0
    var end = 0
    while i < n {
        let next = Prim.stringNextIndex(s, i)
        if !isSpace(Prim.stringDecodeAt(s, i)) { end = next }
        i = next
    }
    Prim.stringSlice(s, 0, end)
}

fun trim(s : String) -> String = trimEnd(trimStart(s))

-- -- instances ----------------------------------------------------------------
--
-- Equality is byte equality, which for well-formed UTF-8 is exactly
-- code-point equality. It performs *no* Unicode normalization:
--
--     "\u{00E9}" == "\u{0065}\u{0301}"   -- False. Both render as e-acute.
--
-- Normalization is a library operation and will never be something `==` does
-- implicitly. Ordering is byte-lexicographic, which for well-formed UTF-8
-- coincides with code-point-lexicographic -- and which is not a collation:
-- it sorts `Z` before `a` and has no opinion about locale (PRIMITIVES.md 4.4).

instance Eq String { fun eq(x, y) = Prim.stringEq(x, y) }
instance Ord String { fun lt(x, y) = Prim.stringLt(x, y) }
instance Add String { fun add(x, y) = Prim.stringConcat(x, y) }
instance Show String { fun show(x) = x }
instance Semigroup String { fun combine(x, y) = add(x, y) }
instance Monoid String { fun empty() = "" }

-- No `Length String`. `len` means an O(1) count of elements, and a string has
-- no single answer to give: `byteLength` is the cheap one, `codePointCount`
-- the one people usually mean, and graphemes the one they usually want.

instance Hash String {
    fun hashInto(s, d) {
        -- Over the bytes, not the code points. The old version built an
        -- `Array Char` on every hash.
        let n = byteLength(s)
        for var i = 0; i < n; i = i + 1 {
            update(d, Prim.byteToInt(Prim.stringByteAt(s, i)))
        }
    }
}
