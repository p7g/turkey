# Proposals

Eight changes to the language, from `notes.txt`, each measured against what the
compiler does today and against how other languages answered the same question.
Nothing here is decided; the point is to have the argument written down before
any of it is built. Where a note makes a claim about the current implementation,
that claim was checked by running the compiler, and what it printed is quoted.

Ordered by what should be built first, not by the order they were written in.

---

## Where things already stand

Four of the eight are smaller than they look, because part of the work is done.

**Newline separators** already exist in record *declarations*. `design.md` 3.2
has `record-sep ::= "," NEWLINE? | NEWLINE`, and the prose under 3.3 says so:
"In a multiline record payload, a significant newline is also a field
separator, so commas are optional." Construction and patterns did not get the
same treatment.

**A warning system** already exists. `driver.Checked` carries a `warnings` list,
`report_warnings` prints it to stderr, and every CLI subcommand calls it. The
only thing missing is the check that would produce the new warning.

**Exhaustiveness checking** already exists, and is good -- `exhaustive.py` is
Maranget's usefulness algorithm in its witness-producing form, so it names an
unhandled value rather than merely asserting one exists. What is at issue is
the policy (warning, not error) and the coverage (`match` only, not `let`).

**Rigid signatures** already exist for complete annotations. Delta 38 did that
work, and the last paragraph of that delta is the remaining hole, described
there as deliberately left open.

So the eight notes are really: two policy decisions, three pieces of
consistency debt, one deferred half of a finished delta, one representation
change, and a rename.

---

## 1. A non-exhaustive `match` is an error, and `let` takes irrefutable
patterns only

These are one change and should ship together. Making a non-exhaustive `match`
an error is only tolerable if there is a one-armed form to replace
`match x { Some(v) -> ..., None -> () }`, and `if let` is only *necessary* --
rather than a convenience -- if refutable `let` stops being allowed.

### What happens today

```
type Opt = Yes(Int) | No

fun f(o : Opt) -> Int {
    match o {
        Yes(n) -> n
    }
}
```

```
b.tl:5:5: warning: this match is not exhaustive; 'No' is not handled
...
panic: no match arm applies to No
```

The diagnostic is excellent and the outcome is a runtime panic anyway. A
warning that is always followed by a crash is an error wearing a disguise.

Worse, the check does not reach `let`. This compiles clean, with no warning of
any kind, and lowers to a panicking match:

```
let Yes(n) = o
```

That is OCaml's hole, and Turkey has no reason to inherit it.

### How others answered

Rust, Swift, Elm, and Kotlin (in expression position) all make a non-exhaustive
match a hard error. OCaml's warning 8 is on by default and essentially every
serious project escalates it with `-warn-error +8`, which is a hard error
reached by a longer road. GHC's `-Wincomplete-patterns` is off unless asked
for, and that is generally regarded as a mistake rather than a position
anybody would defend today.

The argument for error is stronger in Turkey than in most of them, because
`match` is an expression. A non-exhaustive expression has no value. The
language has nothing to put there.

On the `let` side: Rust restricts `let` to irrefutable patterns and routes
everything else through `if let`; Swift does the same with `if case let` and
`guard let`; OCaml permits it and files it under warning 8. Rust's rule is the
one to copy, and it is what makes `if let` load-bearing rather than sugar.

### The cost, and why it is acceptable

A match on integer or string literals can never be exhaustive without a
catch-all, so those matches all grow a `_ -> panic(...)` arm. This is exactly
Rust's situation and nobody there considers it a problem. It is one line, and
it is honest: the arm says out loud that the programmer believes the case is
unreachable, which is information the reader did not have before.

### Two additions

**`while let` earns the feature on its own.** The `drain` in `README.md` is
currently:

```
loop {
    match Array.pop(s.data) {
        Some(x) -> Array.push(out, x)
        None -> break out
    }
}
```

Five lines for the canonical drain loop. With `while let` it is one:

```
while let Some(x) = Array.pop(s.data) { Array.push(out, x) }
```

Rust introduced `if let` and `while let` in the same RFC for this reason, and
splitting them would leave the more valuable half unbuilt.

**`if var` is right, and is not exotic.** Rust has no `if var` because
mutability there lives on the binder (`mut`) rather than on the keyword.
Turkey put it on the keyword. Having `let`/`var` everywhere else and only `let`
here would be an asymmetry with no justification behind it, so support both.

### One thing to get right at the grammar level

Make the condition a *list* from the start:

```
if-cond ::= expr | "let" pat "=" expr | "var" pat "=" expr
if-expr ::= "if" if-cond ("," if-cond)* block ...
```

Implement only the singleton if that is all there is appetite for, but leave
the comma in the grammar. Rust and Swift both had to retrofit chained
conditions, and Rust's took the better part of a decade to land. The binding
from a `let` condition scopes to the then-block only.

`let ... else` -- Rust's early-return form -- is a separate question and can
wait.

---

## 2. A record pattern names every field, or says it is not going to

### What happens today

A record pattern may name a subset, silently:

```
match p {
    P { x = a } -> print(a)     -- accepted; `y` is not mentioned
}
```

Adding a field to `P` changes nothing at this site. That is the finding about
the child list: a new field was added and the functions that should have
handled it went on compiling.

### How others answered

**Rust** is the direct precedent and matches the motivation exactly. `Point { x }`
for a two-field record is error E0027, "pattern does not mention field `y`",
and `..` opts out. **OCaml** has the same idea at lower intensity: warning 9,
`missing fields in record pattern`, off by default, silenced explicitly by
writing `{ x; _ }`. **Haskell** does not have it at all, and its `C{..}` means
the *opposite* thing -- bind every remaining field into scope.

### An argument the note does not make

`design.md` 3.6 currently says the record and positional forms "differ in one
respect only": the record form may name a subset and the positional form may
not, "because a position is not self-describing the way a name is". Requiring
the omission to be written deletes that last difference. The two forms become
genuinely interchangeable, and the paragraph explaining why they are not can go.

### The token should be `_`, not `..`

`..` is already spoken for in Turkey, with the opposite sense.
`import MyMod (MyType(..))` means *all of them*. Using `..` in a pattern to mean
*the rest, which I am ignoring* puts two inverted meanings on one token in one
language, and the reader has to know which side of a pattern they are on to
disambiguate.

`_` has no such problem:

```
P { x = a, _ }
```

It is one character, and `_` already means "wildcard, I do not care" in every
other pattern position, so it reads correctly with no new rule to learn. This
is OCaml's spelling and OCaml chose it for the same reason.

If Rust muscle memory turns out to matter more in practice than internal
consistency, accept both spellings and normalize in the parser -- but let the
documentation write `_`, and let `..` be the undocumented alias rather than the
other way round.

### The cost, stated plainly

Adding a field becomes a compile error at every match site that does not opt
out. That is the entire point for the child-list case, and it is a real tax on
wide records. Rust pays it, and it is survivable there largely because the
opt-out is cheap. Do not add a `#[non_exhaustive]` counterpart for the reverse
direction; that is a library-versioning problem and Turkey does not have one yet.

---

## 3. Newline separators in record construction and patterns

Pure consistency debt. Declarations accept newline separators and the other two
positions do not, for no reason anyone would defend:

```
let p = P {
    x = 1
    y = 2
}
```

```
a.tl:6:9: parse error: expected '}', found IDENT 'y'
```

The fix is to reuse `record-sep` in the `field-init` and `field-pat` lists.

### The hazard, and why it is not one

Field punning means a bare `IDENT` is a valid `field-init`, which raises the
question of whether a continuation line beginning with an identifier could be
silently misread as a new field. It cannot: no Turkey expression continues with
a bare `IDENT`, because infix position requires an operator. The two-sided
newline rule in 2.4 already drops newlines before `.`, `)`, and binary
operators, so ordinary continuations are unaffected.

The one live case is unary minus:

```
P { x = a
    -b }
```

Here the newline is significant by rule 2.4 -- `IDENT` can end a production,
unary `-` can start one -- and `-b` is not a valid `field-init`, so this is a
parse error rather than a silent misparse. That is the correct outcome. The
work is making the error say something useful about what it thinks the newline
did.

### Scope

Brace-delimited field lists only. Newline-as-separator in call argument lists
is where Go's rules get genuinely awkward -- the mandatory trailing comma
before a closing paren on its own line -- and there is little to gain.

---

## 4. Warn when a non-unit expression's value is discarded

### What happens today

```
fun main() -> Unit {
    let p = P { x = 1, y = 2 }
    1 + 2                       -- no diagnostic
    ...
}
```

### Two schools

**Opt in per type.** Rust's `#[must_use]`, Swift's `@discardableResult`. Rust
deliberately left the blanket `unused_results` lint allow-by-default, and the
reasoning is worth taking seriously: `map.insert(k, v)` returning the old value
and `vec.pop()` are legitimately discarded all the time, and a warning that
fires constantly teaches people to stop reading warnings.

**On by default, with an explicit discard.** OCaml's warning 10,
`non-unit-statement`, on by default, opted out of with `ignore (...)`. F#'s
FS0020, "The result of this expression has type X and is implicitly ignored",
with the `|> ignore` idiom. Both are considered successes by the people who use
them daily.

### Turkey should take the second

Rust's objection is about signal-to-noise, and Turkey's noise floor is much
lower than Rust's. The language is strict, procedural, and mutates through
records and arrays, so the overwhelming majority of statements are already
`Unit`-typed. The warning will be quiet, and a quiet warning is a credible one.

No `ignore` function is needed. `let _ = e` already works, since `_` is a
pattern.

### The rule

Warn on any **non-final** statement in a block whose solved type is neither
`Unit` nor bottom.

- Non-final, because the last statement is the block's value (6.8).
- Not bottom, because `return`, `break`, and a call to a panicking function are
  already tracked (4.6, and the `noreturn` work in 68fd344) and discarding
  their non-value is meaningless.
- Solved, not inferred-so-far, so an as-yet-unsolved variable does not produce
  a bogus warning. The check belongs where `check_exhaustiveness` is, after
  inference, for the same reason it does.

---

## 5. A partial annotation is an assertion too

### The hole

Delta 38 made a *complete* annotation a claim about the function, and argued
the case there: "An annotation that inference may rewrite cannot document
anything, because it is not a claim about the function -- it is a hint the
solver is free to discard." Everything that reasoning says is still true one
parameter short of complete, where the old behaviour survives untouched. The
delta's closing paragraph says so:

> Delta 13's own example is *not* repaired: `fun f(x) -> a { 5 }` has an
> unannotated parameter, so it is not a signature and stays on the soft path.
> Closing that needs a body's inferred predicates re-abstracted over the
> skolems a partial annotation fixed, which is a different and larger change.

### How others answered

Haskell annotations are always rigid assertions; partial ones need
`PartialTypeSignatures` turned on, explicit `_` holes, and the compiler reports
what it inferred for each hole -- an annotation is never quietly rewritten.
Rust's signatures are mandatory and always assertions. Scala, Kotlin, and
TypeScript check an annotation as a bound and never rewrite it.

Only OCaml behaves the way Turkey's soft path does. `let f (x : 'a) = 5`
type-checks and `'a` is unified away, and getting rigidity there requires an
explicit `'a.` universal or an `.mli`. It is a well-known wart, not a model.

### Take the cheap half

The full fix needs the body's inferred predicates re-abstracted over the
skolems the partial annotation fixed. That can be skipped:

1. Skolemize the type variables a partial annotation writes, per `fun` scope --
   the same `Skolems` machinery `check_method` already uses. Unannotated
   parameters stay ordinary inference variables and the soft path continues to
   handle them.
2. `fun f(x) -> a { 5 }` then errors, and delta 38's `g` can no longer have its
   declared `[Iterator a]` silently overwritten by a recursive call.
3. When the body *does* infer a predicate mentioning a skolemized variable --
   precisely the case that needs re-abstraction -- reject it, with an error
   that says to write the complete signature.

The fallback in (3) points at a path that already works and is already the
documented way to get a checked type. Haskell's ergonomics have the same shape:
the answer to a great many inference complaints there is "add a type
signature".

### One correction to the framing

This is not a soundness hole. Type safety is fine today. What is broken is
documentation and blame -- the annotation does not mean what it says, and the
error, when one comes, is reported somewhere other than where the wrong claim
was written.

---

## 6. A record's layout should follow from its field types

Deferred, to be done in one piece. The design -- corrected against the code --
now lives in `NATIVE-BACKEND.md`, "Record layout: what the representation is,
and what a packed one needs". Two premises of the first draft did not survive:
`pointer_bitmap` is a three-bit code per slot rather than a leading-N bitmap, so
putting pointers first buys the collector nothing; and a type-variable field
stays one word for producer/consumer agreement, not because `mono.py` is
partial -- `layout.share` and `check_layouts` are what cover that.

---

## 7. The file extension stops being `.tl`

### `.tl` has to go

Two other languages claim it, and GitHub's Linguist knows about both. Running
the candidate list against `linguist/lib/linguist/languages.yml`:

| Extension | Claimed in Linguist by | Other live collisions |
| --- | --- | --- |
| `.tl` | **Teal, Twig** | Telegram Type Language schemas |
| `.gbl` | **Genshi** | **Gerber Bottom Layer** -- every PCB CAD tool emits these |
| `.tu` | **Turing** | -- |
| `.ki` | free | **Ki**, a live systems language (ki-lang.dev) |
| `.tk` | free | Tcl/Tk scripts; Tokelau ccTLD, heavily blocklisted |
| `.gob` | free | LucasArts Jedi Engine archives (1995); GObject Builder -- both dead |
| `.tky` | free | none found |
| `.tur`, `.turk` | free | -- |

Linguist is the concrete stake. An extension it already claims means every
Turkey file on GitHub is syntax-highlighted as some other language, counted as
that language in the repository's language bar, and matched by that language's
rules in code search. `.tl` files currently read as Teal or Twig.

### `.gbl` is worse than `.tl`, not better

This was wrong in the first draft of this document. `.gbl` is claimed by
Linguist for Genshi, so it fails on exactly the axis `.tl` fails on -- and
underneath that it is **Gerber Bottom Layer**, the bottom copper layer of a
printed circuit board, emitted as a matter of routine by Altium, KiCad, and
Eagle alongside `.gtl`, `.gbs`, and the rest of the Gerber set. That is a live
format with daily traffic, in a community that overlaps heavily with the people
a small systems language wants to reach. Someone with a PCB project and a
Turkey project in the same directory has a genuine problem.

### The two real candidates

**`.gob`.** Free in Linguist. The only formats standing in the way are dead:
LucasArts' Jedi Engine game archives from Dark Forces (1995) and GObject
Builder source files. Go's `encoding/gob` is a wire format, not a file
extension anyone writes.

It is also the better joke. `.gbl` was "gobble" with the vowels removed, which
has to be explained; `.gob` is a pronounceable English word that is already how
the noise is spelled. Three characters, no ambiguity in speech, and the
connection to the language's name survives being said out loud.

**`.tky`.** Free in Linguist, and nothing meaningful turned up against it --
the one aggregator entry is the generic "binary data, developer unknown" filler
those sites generate for every three-letter string. Unmistakably Turkey, and it
gives up the joke for total clarity.

### Recommendation

`.gob`. It is the only candidate that is both clean and *says something*, and
the collisions it has are with formats that stopped being written before most
of the toolchain this project depends on existed. `.tky` is the safe fallback
if a dead-format collision is unacceptable at any level.

Ruled out: `.gbl` (Genshi, plus Gerber), `.tu` (Turing), `.ki` (a live
language), `.tk` (Tcl/Tk convention, and the Tokelau domain's spam reputation
makes "tk file" an unpleasant thing to search for). `.turk` is free but carries
the ethnonym and the Mechanical Turk association for no gain over `.gob`.

### The work

One `with_suffix(".tl")` in `modules.py:132`, a rename of 42 test programs and
their `.expected` and `.types` companions, and a sweep of the prose in
`README.md`, `design.md`, `SPEC-DELTAS.md`, `PRIMITIVES.md`, and
`LLVM-BACKEND.md`. Mechanical, and the test suite proves it landed.

It is the cheapest item on this list and the only one that gets more expensive
with delay, since every new golden file and every new delta written against
`.tl` is one more thing to sweep. Do it first, on its own commit, before any of
the language changes start adding files.

---

## Order

1. **The extension rename** -- mechanical, gets more expensive the longer it
   waits, and does not interact with anything else. Its own commit, before the
   rest add files.
2. **Non-exhaustive `match` is an error, plus `if let` / `if var` /
   `while let`, plus irrefutable-only `let`** -- one change, the largest payoff,
   and `while let` improves the README's own worked example the day it lands.
3. **Newline separators in construction and patterns** -- small, and it removes
   an inconsistency that will otherwise be tripped over repeatedly while writing
   tests for (2).
4. **Record patterns name every field or write `_`** -- the second half of the
   exhaustiveness argument, applied to records.
5. **Warn on a discarded non-unit value** -- the check is small now that the
   warning infrastructure exists.
6. **Partial annotations are assertions** -- the cheap half, with the complete
   signature as the documented fallback.
7. **Records lay out by field type** -- the largest change, and the one whose
   argument is about what the language claims to know rather than about what it
   costs.
