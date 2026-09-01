# Primitive type semantics

Status: implemented, except where a section says otherwise. `design.md` §8.1
carries the summary and SPEC-DELTAS.md 57 records what changed; this document
stays as the reasoning behind each decision.

Not yet done, and called out again at the end of each section that owns it:
packed `Array Byte` layout, everything in `Data.Unicode` (normalization,
collation, case mapping, graphemes), and the `Show`/`Debug` split. The opaque
`String.Index` has since shipped -- see 4.3.

`design.md` §8.1 defines the primitives in six table rows -- "machine
integer", "floating-point", "UTF-8", "single Unicode codepoint". None of
those is a semantics. What the implementation actually does is whatever
Python does, which means the language currently promises:

| Written | Actually | Consequence |
|---|---|---|
| `Int` "machine integer" | Python `int`, unbounded | `types.py` says `INTEGRAL_WIDTHS = {"Int": None}`; a 200-bit `Int` is legal today |
| `Float` "floating-point" | Python `float` (f64) *except* `/` panics on zero | not IEEE |
| `String` "UTF-8" | Python `str`, a code-point sequence | `String.length` counts code points, not bytes; UTF-8 is not the model |
| `Char` "single Unicode codepoint" | Python 1-char `str`, surrogates allowed | `Prim.charFromInt(0xD800)` succeeds |
| `Show Float` | `repr(x)` | `inf`, `nan`, `1e+16` -- Python's spelling is the language's |

This document nails each of those down independently of the host, and adds
`Byte`. It is written so the Python evaluator and the future llvmlite backend
(`LLVM-BACKEND.md`) can be checked against the same statements.

---

## 1. Int

**`Int` is a two's-complement signed 64-bit integer**, range
`-2^63 .. 2^63 - 1`. Not a machine word, not a bignum: exactly 64 bits on
every target.

### 1.1 Overflow traps

`add`, `sub`, `mul` and `neg` **panic** when the mathematical result is
outside the range. So does `div` for the one overflowing case,
`minInt / -1`, and `neg(minInt)`.

Trapping rather than wrapping, for three reasons:

- It is the same choice the language already made everywhere else. `div` by
  zero panics, array indexing panics, `Prim.error` panics. Silent wraparound
  is the only place a Turkey program would keep running with a wrong answer.
- The cost is a well-predicted branch. LLVM lowers
  `llvm.sadd.with.overflow.i64` to `add; jo`, and the taken edge is a cold
  block that calls the panic runtime.
- Wrapping is recoverable from trapping (`Int.addWrapping`), but trapping is
  not recoverable from wrapping -- once the answer is wrong, the information
  is gone.

Panic messages follow the existing frame-carrying format
(SPEC-DELTAS.md 56): `integer overflow in +`.

### 1.2 Division and remainder

Already decided (SPEC-DELTAS.md 18) and unchanged, but state the identity:

- `div` truncates toward zero. `(-7) / 2 == -3`.
- `rem` takes the sign of the *dividend*. `(-7) % 2 == -1`.
- For all `a`, `b != 0` with no overflow: `a == (a / b) * b + a % b`.
- `b == 0` panics for both.

`%` is **remainder, not modulus**. Because the difference bites exactly where
people reach for it -- `i % n` as a bucket index, which is what
`Data/Map.tl:180` does -- `Data.Int` gains `mod(a, b)`, floored, whose result
always has the sign of the divisor and is therefore always a valid index for
positive `b`. `Data.Map` should use it.

### 1.3 Literals

An integer literal outside `-2^63 .. 2^63 - 1` is a **compile error**, not a
wrap. This is a real change: `INTEGRAL_WIDTHS["Int"]` becomes `64`, and the
literal-defaulting path in `types.py` (`int_literal_set`, and the mantissa
check that already rejects `Float` literals past 2^53) starts rejecting them.

### 1.4 Escape hatches, in `Data.Int`

```
fun addWrapping(Int, Int) -> Int        -- and sub/mul/neg
fun addChecked(Int, Int) -> Option Int  -- and sub/mul/neg
fun mod(Int, Int) -> Int                -- floored
fun shl(Int, Int) -> Int                -- panics if shift >= 64 or < 0
fun shr(Int, Int) -> Int                -- arithmetic, same panic
fun and(Int, Int) -> Int                -- and or/xor/not
fun minValue() -> Int
fun maxValue() -> Int
```

These are not optional. **`Algorithm.Hash` and `Data.Map` currently rely on
unbounded `Int`**: `DJB2.update` computes `d.h * 33 + x` and masks to 32 bits
*after* the multiply. That survives the change only because it masks every
step; `Hash Int` feeding a full-width `Int` into `update` does not. Every
hash mixer must move to `addWrapping`/`mulWrapping`, which is also what makes
it a *good* mixer rather than a 32-bit one.

Shifts panic rather than mask or saturate, because masking (`x << 64 == x`)
is a C wart nobody predicts and LLVM's `shl` is poison there -- a panic is
the only answer that is both defined and unsurprising.

Bitwise operations stay functions. There is no operator budget for
`& | ^ ~ << >>`, and `Int.and` reads fine.

---

## 2. Byte

**`Byte` is an unsigned 8-bit integer**, range `0 .. 255`. New primitive
`TCon`, alongside `Int`/`Float`/`String`/`Char`.

`Byte` exists for one reason: to be the element of `Array Byte`, which is how
bytes enter and leave `String` and how a future I/O layer will speak. It is a
storage and interchange type, so it is deliberately given **no arithmetic
instances at all** -- no `Add`, `Sub`, `Mul`, `Div`, `Neg`. Arithmetic on
bytes goes through `Int`:

```
Byte.toInt(b) : Int              -- total
Byte.fromInt(n) : Option Byte    -- None outside 0..255
Byte.truncate(n) : Byte          -- low 8 bits, total, explicitly lossy
```

This dodges the entire "does `u8 + u8` wrap, trap, or promote" question. If
byte arithmetic later proves worth having, adding instances is
backward-compatible; removing them would not be.

`Byte` gets `Eq`, `Ord` (numeric), `Show` (decimal), `Hash`, and bitwise
functions in `Data.Byte`. There is no `Byte` literal syntax; `Byte.fromInt`
is the only way to write one, and constant-folding makes that free.

`Bytes` is a type alias for `Array Byte`, not a new container.

Representation: `Array Byte` should be **packed**, one byte per element. The
backend's layout selection (`LLVM-BACKEND.md`) must special-case it, or
`Byte` buys nothing over `Int`.

---

## 3. Float

**`Float` is IEEE 754 binary64**, with the default rounding mode
`roundTiesToEven`, fixed. No dynamic rounding mode. No access to the FP
exception flags. Keep the name `Float` for binary64; a future 32-bit type is
`Float32`, which is less churn than renaming `Float` to `Double`
(`types.py:512` contemplates the rename -- this closes that question).

### 3.1 Arithmetic is IEEE, which means `/` stops panicking

`add`, `sub`, `mul`, `div`, `neg` are the IEEE operations, correctly rounded.
In particular **`Prim.floatDiv` must lose its zero check**:

```
1.0 / 0.0   ==  Infinity
-1.0 / 0.0  == -Infinity
0.0 / 0.0   ==  NaN
```

The current panic (`builtins.py:_float_div`) is the single largest departure
from IEEE in the implementation. A language that says "IEEE 754" cannot
panic where the standard says "return an infinity and raise a flag nobody is
reading."

There is **no `Rem Float`**, so `%` remains integer-only. C's `fmod` and
IEEE's `remainder` disagree (different rounding of the implied quotient), and
neither deserves the operator. `Data.Float` provides both under names that
say which is which: `fmod` and `remainder`.

**No fast-math, ever.** The llvmlite backend must not set `nnan`, `ninf`,
`fast`, `reassoc`, or `contract` on any float instruction, and must not form
FMAs. Float operations are the one place where the Python evaluator's role as
a differential oracle requires bit-exact agreement, and every one of those
flags breaks it.

### 3.2 NaN, and the one lawless instance in the language

`Eq Float` and `Ord Float` are the IEEE comparison predicates:

```
NaN == NaN   is False
NaN != NaN   is True
NaN <  x     is False   for every x, including NaN
NaN <= x     is False
NaN >  x     is False
NaN >= x     is False
-0.0 == 0.0  is True
```

Three consequences, and they must be written into `design.md` rather than
discovered:

**(a) `Ord Float` breaks the class defaults, and does so today.**
`Std/Classes.tl` derives `gte(x, y) = !lt(x, y)`, so `gte(NaN, 1.0)` is
currently `True` while `lte(NaN, 1.0)` is `False`. That is not IEEE and not
anything else. `instance Ord Float` must **override all four methods**
(`lt`, `lte`, `gt`, `gte`) with the primitive comparisons instead of
inheriting three of them.

**(b) `Ord Float` is the one instance in the language that does not satisfy
its class's laws.** `Eq` is not reflexive on `Float` and `Ord` is not total.
Turkey has one `Ord` and no `PartialOrd` split (single-parameter classes,
§8.2), so there is nowhere to put the distinction in the type system. The
honest move is to write the exception down and give sorting a way out:

```
type Ordering = LT | EQ | GT                     -- new, in the prelude
Float.totalCompare(x, y) : Ordering              -- IEEE 754 totalOrder
```

`totalOrder` puts `-NaN < -Infinity < ... < -0.0 < +0.0 < ... < +Infinity <
+NaN`. Any sort or ordered structure that must not misbehave on NaN uses it.
A generic `sort` over `Ord Float` is **safe but unspecified** on arrays
containing NaN: it terminates, it panics nowhere, it may produce an unsorted
result. Say exactly that.

`Ordering` is worth adding regardless -- three-way comparison is missing from
the class hierarchy and every sort wants it.

**(c) There is no `Hash Float`.** `Hash a : Eq a` and `Eq Float` is not
reflexive, so a `Float` key can be inserted into a `Map` and never found
again. Refusing the instance turns that into a type error. Anyone who really
wants float keys uses `Float.bits(x) : Int` (the raw bit pattern) and thereby
opts visibly into `-0.0 != 0.0` and NaN-payload sensitivity.

NaN payloads and the NaN sign bit are **not preserved** by any operation, and
programs may not observe them except through `Float.bits`. This leaves the
backend free to canonicalize.

### 3.3 `Show Float` is defined here, not inherited from `repr`

- The shortest decimal string that round-trips to the same `Float`
  (Ryu/Grisu; Python's `repr` already does this, so the evaluator matches by
  accident -- make it match on purpose).
- Always a `.0` or an exponent, so finite output re-lexes as a `Float`
  literal and not an `Int` one: `1.0`, not `1`.
- `-0.0` prints `-0.0`.
- Specials print `Infinity`, `-Infinity`, `NaN`.

The specials do **not** round-trip through the lexer -- there is no
`Infinity` literal. That is an accepted wart, mitigated by
`Float.parse(String) -> Option Float`, which accepts them.

### 3.4 Conversions are explicit and total-or-optional

```
Float.fromInt(n) : Float           -- total; exact only to 2^53, then
                                   --   rounds ties-to-even. Not lossless.
Float.truncate(x) : Option Int     -- None for NaN, +/-Infinity, and
                                   --   anything outside Int's range
Float.floor/ceil/round/trunc(x) : Float   -- stay in Float
```

`Float.truncate` returning `Option` is not fussiness: LLVM's `fptosi` is
**poison** out of range, so the alternative is real undefined behaviour in
the native backend.

There is no implicit coercion between `Int` and `Float`, in either direction.
That is already true and should be stated in §8.1 as a rule rather than left
as an absence.

---

## 4. String

**A `String` is an immutable sequence of bytes that is guaranteed to be
well-formed UTF-8.**

That is the whole model, and every operation is judged by whether it
preserves the invariant. It is not "a sequence of characters" and not "a
sequence of code points" -- the code points are a *view*, and so are the
bytes, and later so are the graphemes.

### 4.1 No indexing, and no `length` either

No `Index String` instance, no `s[i]`. Agreed and already the plan.

Less obviously: **`instance Length String` must go, and `String.length` with
it.** Today `Data.String.length` is `Prim.stringLength` is Python `len` is
the code-point count -- an O(n) answer to a question with three defensible
answers (bytes, code points, graphemes), under a name that promises one. It
is the clearest instance of the language accidentally inheriting Python's
string model.

Replacements:

```
String.byteLength(s) : Int      -- O(1); the only cheap count
String.isEmpty(s) : Bool        -- what `len(s) == 0` was for
```

Counting code points is `count(String.codePoints(s))`: still available,
O(n) at the call site where it belongs, and impossible to confuse with the
byte count. `len` keeps meaning "an O(1) count of elements," which `String`
does not have.

### 4.2 Three views, all lazy, room for a fourth

```
String.bytes(s)      -- Iterator with Item = Byte
String.codePoints(s) -- Iterator with Item = Char
-- reserved: String.graphemes(s), String.words(s), String.lines(s)
```

These are **iterators, not arrays.** `Data.String.chars` currently returns
`Array Char` and `builtins._chars` materializes the whole thing -- an
allocation per iteration site and, again, Python's model showing through.
The `Iterator` class with its `Item`/`Cursor` families (§8.2) already exists
and is exactly the right shape; these become opaque view types with
`Iterator` instances.

Making them views now is what leaves room for graphemes: `String.graphemes`
slots in beside the others without changing the shape of anything, and a
grapheme view *cannot* be an `Array` of a primitive anyway, since a grapheme
cluster is a substring.

`Data.String.fromChars` -- a `+`-in-a-loop, O(n^2) -- becomes a builder. It
shipped as `fromCodePoints`, over a `String.Builder` that collects the pieces
in an `Array String` and joins them once through a new
`Prim.stringConcatAll`; `join` and `repeat` are written on the same builder.

### 4.3 Cutting strings without indices

Something has to be able to take a string apart. Two shapes are available:

1. An opaque `String.Index`, obtainable only from a search or an iteration,
   with `String.slice(s, from, to)`. Well-formedness holds by construction
   because no arithmetic on an index is exposed.
2. A search-and-split API with no index type at all.

**Ship (2) first**: `split`, `splitOnce`, `startsWith`, `endsWith`,
`contains`, `stripPrefix`, `stripSuffix`, `replace`, `repeat`, `trim`,
`trimStart`, `trimEnd`. It covers the overwhelming majority of real string
handling and costs no new type. Add the opaque `String.Index` when something
genuinely needs it.

**Something did, and (1) has now shipped too.** A lexer is the thing that
needs it: the two shapes otherwise available to one are to materialize an
`Array Char`, at four bytes per code point and an allocation per file, or to
drive the `CodePoints` view, which cannot look ahead and cannot say where a
token started. So `Data.String` gained

```
type Index                                        -- opaque
String.start(s) / String.end(s) -> Index
String.step(s, i)   -> Option (Char, Index)       -- decode and advance
String.decode(s, i) -> Option Char                -- decode without moving
String.atEnd(s, i)  -> Bool
String.slice(s, from, to) -> String
String.find(s, needle, from) -> Option Index
```

with `Eq` and `Ord` instances ordering by position. The invariant holds by
construction exactly as this section says it does: an `Index` comes only from
`start`, `end`, `step` or `find`, and there is no arithmetic on one, so `slice`
has nothing to validate and cannot cut a character in half. `find` is public in
this form and not in the earlier one -- what made it unshippable was that its
only useful return was an *offset*, and an `Index` is not one.

An `Index` belongs to the string it came from, and using one with another
string is not caught. Catching it would cost a tag per index; the same
discipline already governs `Iterator`'s `Cursor`.

`startsWith` and `endsWith` are the search, not a slice-and-compare. Cutting
a prefix of the needle's byte length would split a multi-byte character in
half -- `startsWith("é", "a")` asks for one byte of a two-byte character --
and `Prim.stringSlice` panics on a non-boundary offset rather than inventing
a replacement character. The search cannot make that mistake, because a
well-formed needle only ever matches at a boundary.

What must **not** happen in the interim is exposing raw byte offsets as
`Int`, because that is the one decision that cannot be taken back: once a
program can compute an offset, `slice` has to either validate (a panic on a
mid-sequence boundary) or not (invalid UTF-8). The opaque index is precisely
the fix, so wait for it rather than shipping the `Int` version.

### 4.4 Equality is bytes; ordering is bytes; neither is linguistic

`==` on `String` is **byte equality**, which for well-formed UTF-8 is exactly
code-point equality. It performs **no Unicode normalization**:

```
"\u{00E9}" == "\u{0065}\u{0301}"    -- False. Both render as "e-acute".
```

This is the single biggest Unicode surprise in any language and belongs in
`design.md` §8.1 in as many words. Normalization is a library operation
(`Data.Unicode.normalize(s, NFC)`, later), never something `==` does
implicitly -- an implicitly-normalizing `==` is O(n) with allocation and
still wrong for the cases where you wanted the bytes.

`Ord String` is **byte-lexicographic**, which for well-formed UTF-8 coincides
with code-point-lexicographic -- one of UTF-8's design properties, and the
reason nothing needs to change here. It is *not* a collation: it sorts `Z`
before `a` and has no opinion about locale. Collation is
`Data.Unicode.Collate`, later, and never `Ord`.

`Hash String` hashes the **bytes**, and must stop going through
`chars` (which allocates an `Array Char` per hash today).

### 4.5 Case conversion is a String operation and is not shipping yet

There must be **no `Char.toUpper` / `Char.toLower`**, ever. Case mapping is
not a per-code-point function:

- `ß` uppercases to `SS`: one code point to two.
- Final sigma `ς`/`σ` depends on position in the word.
- Turkish dotless `ı` maps differently from every other locale's `i`.

So case lives on `String`, as `String.toUpperCase` / `toLowerCase`
implementing Unicode *default* (locale-independent) full case mapping, in a
later `Data.Unicode`. Shipping a per-`Char` version first would be a
compatibility trap.

### 4.6 Literals and escapes

Source files are UTF-8, so a string literal is well-formed by construction.
The escape rules need two changes to keep it that way:

- `\u{H...H}` with 1-6 hex digits, replacing `\uXXXX`. Four digits cannot
  express anything above the BMP, so `"\u{1F600}"` is currently unwritable.
- **Escapes naming a surrogate (`D800`-`DFFF`) or a value above `10FFFF` are
  a lex error.** This is what makes the invariant hold at the source level.

There is no `\xNN`. A byte escape can produce invalid UTF-8, and `\u{7F}`
covers everything an ASCII-minded `\x` was for.

### 4.7 The only door in and out

```
String.toBytes(s) : Array Byte           -- total, the UTF-8 encoding
String.fromBytes(bs) : Option String      -- validates; None if ill-formed
```

That checked constructor is the entire justification for `Byte` existing.
There is deliberately no lossy/replacement-character variant in the first
cut; add `String.fromBytesLossy` later if I/O demands it, and name it so.

---

## 5. Char

**A `Char` is a Unicode scalar value**: an integer in `0 .. 10FFFF`
**excluding the surrogate range `D800 .. DFFF`**.

Not "a code point", which is what `design.md` §8.1 says today. The
difference is exactly the surrogates, and it matters because
`String.fromChars` must not be able to build ill-formed UTF-8. If `Char` can
hold a surrogate, the `String` invariant is not enforceable.

This is a live bug: `builtins._char_from_int` checks `0 <= n <= 0x10FFFF` and
lets `chr(0xD800)` through.

```
Char.toInt(c) : Int             -- total, 0..10FFFF minus the hole
Char.fromInt(n) : Option Char   -- None for surrogates and out of range
Char.toString(c) : String
```

`Char.fromInt` returns `Option` rather than panicking; the panicking variant
in `Prim` stays internal to the library.

`Char` has no arithmetic instances -- no `Add Char`, no `Sub Char`. `'a' + 1`
is not meaningful across a variable-width encoding, and going through
`Char.toInt` costs a line and buys clarity.

Representation is 32 bits. `Array Char` is therefore four bytes per element,
which is another reason `String.codePoints` should be a lazy view rather than
returning one.

**Naming caveat, worth stating in the spec:** a `Char` is not a
user-perceived character. That is a *grapheme cluster*, which may be several
scalar values (`e` + combining acute, a flag emoji, a family emoji). So
`String.codePoints` is not "the characters of the string," and the word
"character" should be avoided in the documentation of both. Reserving
"grapheme" for the real thing is what makes the later `String.graphemes`
honest.

---

## 6. Bool and Unit

Unchanged. `Bool` stays a prelude ADT (`type Bool = False | True`) rather
than a primitive, which is the right call and costs nothing -- the backend
lowers a two-nullary-constructor type to `i1`/`i8` by layout selection, not
by special-casing the name. `Unit` is zero-sized and erased.

---

## 7. Consequences elsewhere

### 7.1 `Show String` is the identity, and that is now visibly wrong

`instance Show String { fun show(x) = x }`. So `show(["a,b", "c"])` and
`show(["a", "b,c"])` produce output that cannot be told apart, and
`Show Array`'s output is not re-readable. This is the Display/Debug split
that Rust has and Turkey does not.

It is out of scope here, but nailing `String` down is what makes it visible:
either `Show String` quotes and escapes (and then `print(s)` prints quotes,
which is worse), or the class splits. Recording it so the next person does
not rediscover it.

### 7.2 The `Prim.` floor changes

The list below is what was planned; what shipped differs in two places.
`Prim.stringDecodeAt` returns just the `Char` and `Prim.stringNextIndex`
gives the following offset, rather than one primitive returning a pair — two
primitives were cheaper than plumbing a tuple `ConValue` through
`builtins.py`. And several `Option`-returning conversions are split into a
predicate plus a total primitive (`Prim.floatCanParse` + `Prim.floatParse`,
`Prim.charIsScalar` + `Prim.charFromInt`, `Prim.stringIsValidUtf8` +
`Prim.stringFromBytes`), so that the `Option` is built in the library where
`Some`/`None` are already in scope. `Prim.stringConcatAll` was added for the
builder.

Removed: `Prim.stringLength`, `Prim.stringChars`.
Changed: `Prim.charFromInt` (reject surrogates), `Prim.floatDiv` (no panic),
`Prim.intAdd`/`Sub`/`Mul`/`Neg` (range check), `Prim.floatToString`
(specified spelling, not `repr`).

New, roughly:

```
Prim.intAddWrapping/Sub/Mul/Neg      Prim.intShl/Shr/And/Or/Xor/Not
Prim.stringByteLength                 Prim.stringByteAt
Prim.stringDecodeAt                   -- (String, Int) -> (Char, Int)
Prim.stringFromBytes                  Prim.stringToBytes
Prim.byteFromInt / Prim.byteToInt
Prim.floatBits / Prim.floatFromBits   Prim.floatTotalCompare
Prim.floatTruncate                    Prim.floatFloor/Ceil/Round
Prim.floatParse
```

### 7.3 Differential testing

The Python evaluator is the oracle for the llvmlite backend, so every
statement above has to hold in both. The ones that will actually diverge if
unattended: `Int` overflow (Python won't trap unless the primitive checks),
`Show Float` (Python's exponent formatting and `inf`/`nan` spellings), float
`/` by zero (Python raises), and `fptosi` out of range (poison natively,
`OverflowError` in Python).

---

## 8. Deliberately not doing

- No implicit numeric conversion, in any direction.
- No sized integer family (`Int8`/`Int16`/`Int32`) yet -- but the names are
  reserved, `Int` is `Int64`, and `Byte` is `UInt8`. `types.py`'s
  `INTEGRAL_WIDTHS`/`DECIMAL_MANTISSAS` tables already anticipate the tower.
- No `Decimal`, no rationals.
- No normalization, collation, case mapping, or grapheme segmentation in the
  first cut -- but the API shapes above all leave room, and none of them will
  need a breaking change to land.
