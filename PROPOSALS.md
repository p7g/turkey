# Proposals

Eight changes to the language from `notes.txt`, and one that is not -- item 8
comes from the runtime sequence and is argued here because
`RUNTIME-IN-TURKEY.md` asks for it to be justified as a language feature rather
than as a means to that sequence. Each is measured against what the compiler
does today and against how other languages answered the same question. Nothing
here is decided; the point is to have the argument written down before any of
it is built. Where a note makes a claim about the current implementation, that
claim was checked by running the compiler, and what it printed is quoted.

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

## 8. `foreign`: a declaration form for the symbols libSystem forces on us

Not from `notes.txt`. This one comes from TIX-62, and
`RUNTIME-IN-TURKEY.md`'s recommendation is the reason it is argued here rather
than simply built: *"An FFI is worth having regardless of the runtime. Turkey
today cannot call C at all, which is a limitation of the language and not of
the runtime."*

### What is already decided, and it is only this

The *emission* is done. `Callee` has a `Runtime(String)` shape, `Select.gob`
already dispatches it through `runtimeSymbol`, and `call` already lays
arguments out by register bank. `Runtime.gob` already owns a table from
primitive name to C symbol and result class. What is missing is a surface
syntax, a type mapping, and an answer for the Python side.

TIX-61 also left two things for this: `Prim.ptrAlloc` and `Prim.ptrFree` are
`malloc` and `free` behind runtime calls, standing in until there is an FFI.

### The framing that makes this small

The ticket sizes itself against thirty-five libc functions and asks whether an
FFI that expresses exactly them is too narrow to justify. That is the wrong
axis, because the goal is not to call C well -- it is to depend on libc as
little as possible and write the rest in Turkey.

Under that framing the thirty-five sort into four piles and only one of them
is an FFI problem:

| group | today | under this proposal |
|---|---|---|
| `floor` `ceil` `round` `trunc` `isnan` | libm calls | **instruction selection.** arm64 has `frintm`/`frintp`/`frintn`/`frintz`; LLVM has the intrinsics. A `Select.gob` change, and its own ticket |
| `snprintf` `fprintf` `fputs` `fwrite` `fread` `fopen` `fclose` `strlen` `strtod` | libc stdio and string | **Turkey, over `read` and `write`** |
| `getenv` | libc | **Turkey.** `environ` is a pointer array handed to the process at startup |
| `malloc` `free` `realloc` | libc | **Turkey, over `mmap`** -- eventually |
| `memcpy` `memmove` `memset` `memcmp` | libc | **see 8.7** |
| `read` `write` `exit` `signal` and the five `pthread_*` | libc | **the FFI.** About ten entry points |

So the answer to the ticket's question is: the *mechanism* is general -- any C
ABI signature over scalars and `Ptr` -- and what is narrow is the list of
symbols anyone has a reason to declare. That is the right way round.

### 8.1 The survey

Six axes, and on five of them the peers agree closely enough that the
disagreement is the interesting part.

#### Strings are decided by whether the collector moves

Every peer with a **moving** collector copies at the boundary, and says why.
Go's `C.CString` mallocs a copy the caller frees, and cgo's rules go further:
C code "may not keep a copy of the Go pointer after the call returns", checked
at run time and fatal, because "after the cgo call returns, the Go garbage
collector is free to move memory around as necessary, but it cannot update any
pointers in C/C++ when it does this."

JNI prices the same trade out loud and sells both halves. `GetStringUTFChars`
copies; `GetStringCritical` pins and is faster only "on some platforms", since
"while an object is pinned it cannot be moved, potentially impeding many
Garbage Collector techniques which require object movement." Android's move to
a moving collector "greatly reduces the number of cases where direct pointers
can be provided ... even for `GetStringCritical`", and JEP 423 exists to give
G1 region pinning back.

The two systems with **non-moving** heaps hand the pointer over instead.
OCaml pads its strings with a NUL *precisely* so `String_val` is a valid
`char*` for free -- with the documented caveat that OCaml strings may contain
NUL bytes, so C functions must "cope with arbitrary bytes within the buffer
contents and are not expecting C strings." Modula-3 makes the same claim for
untraced references generally: "In most other respects, traced and untraced
references behave identically."

Rust, with no collector at all, shows where the cost goes when movement is not
the problem: `CString::new` *fails* on an interior NUL and reports its position.

**Turkey's collector does not move.** So OCaml's route is open and nobody
else's obstacle applies -- the only thing in the way is that `TurkeyString` is
`{int64_t length; unsigned char bytes[]}` with no terminator, and the fix is
one byte at every construction site.

#### Ownership is documented everywhere and expressed nowhere

Go's two cgo rules are run-time checks that crash the program. Haskell's
`ForeignPtr` plus `touchForeignPtr` is a finalizer and a manual liveness
marker. Modula-3 just splits the heap and concedes that matching references to
C "is complicated ... the traced heap is automatically managed in ways that
are not compatible with sharing of memory between safe and unsafe languages."

Nobody puts it in the type system, and that is the finding.

#### The variadic escape hatch that every peer uses is closed here

Go: "Calling variadic C functions is not supported. The arguments must be
written out in the calling C code" -- the remedy is a static C wrapper.
Haskell: varargs "are unsupported by the `ccall` calling convention. Foreign
imports needing to call such functions should rather use the `capi`
convention", which generates a wrapper from the header.

**Both answers are a C compiler**, which is the thing this sequence exists to
remove.

And declining is not neutral, because a fixed-arity declaration does not fail
to work -- it miscompiles. Darwin's arm64 convention passes **every** variadic
argument on the stack where AAPCS64 passes them in registers, a deliberate
divergence that "greatly simplifies the underlying implementation of `va_list`
and related macros." So a fixed-arity `snprintf` puts an argument in a register
the callee reads off the stack.

#### The declaration form: Rust 2024 re-derived Modula-3's argument

Modula-3's `EXTERNAL` pragma "can only be used within unsafe interfaces",
because "since the type of the function or data structure may in fact be
specified by the C implementation, Modula-3 cannot enforce type safety of safe
modules that use EXTERNAL."

Rust 2024 made every `extern` block an `unsafe extern` block, and RFC 3484's
reasoning is the same argument reached independently thirty-five years later:
*"When we declare the signature of items within extern blocks, we are asserting
to the compiler that these declarations are correct. The compiler cannot itself
verify these assertions ... It's unreasonable to expect the caller (in the case
of function items) to have to prove that the signature is valid. Instead, it's
the responsibility of the person writing the extern block."*

The piece worth taking is what Rust added alongside it: items inside an
`unsafe extern` block "may be marked as safe to use." The declarer carries the
risk once; the caller does not carry it again.

#### errno is a library, with one catch that bites

Go exposes it as an optional second return value from any C call. Haskell makes
it a library -- `throwErrno`, `throwErrnoIfMinus1Retry` -- and the RTS keeps a
`saved_errno` per Haskell thread and restores it on reschedule.

The catch: on Darwin `errno` is a macro for `*__error()`, which is a **call**.
glibc's is `__errno_location()`. So reading errno is an ordinary foreign
declaration, and "declare `errno` as an extern global" silently does not work.

#### The constraint no peer has

None of these systems has a second implementation acting as an oracle.
`turkey/values.py`'s `RawHeap` hands out *simulated* addresses from 4096, and
`PRIMITIVES.md` 9.4 states its job: to agree with `malloc` on everything
defined and to disagree loudly everywhere the section says "undefined". A real
call cannot be handed a simulated address. 8.9 is what follows from that.

### 8.2 The declaration form

A top-level declaration naming a C symbol and a signature, legal only in a
*standard library* module under `Unsafe.` -- which is exactly where `Prim.` may
be spelled, and the same rule rather than a second one.

```
foreign "read" fun read(fd : Int, buf : Ptr, count : Int) -> Int
```

Modula-3's rule and Rust's, with the gate Turkey already has rather than a new
one: `Prim.` is spellable only from `lib/`, and `lib/Unsafe/Ptr.gob` already
puts the subject in the import list of everything that touches raw memory,
which is Oberon's `SYSTEM` gesture. A `foreign` declaration is the same kind of
assertion and belongs behind the same name.

Rust's `safe fn` is the shape of the exported surface: the declaration is
unsafe and lives once, and what `System.Env` exports is ordinary Turkey. That
boundary is load-bearing beyond tidiness -- see 8.8.

Two things the gate deliberately is not, because each is a way of having no
gate at all. It is not a check on the module's declared *name*: only the loader
knows where a file came from, and a program that called its own module
`Unsafe.Libc` and put it beside itself would let itself in. And it is not "any
directory called `lib`" -- the first search root is the entry file's own
directory, which is the same hole wearing a different hat.

What that costs is worth saying plainly: **a user cannot declare a symbol yet**,
only call the wrappers over the ones the standard library declares. That is the
same position `Prim.` is in and it is not this proposal's to reverse -- what
would reverse it is a story for where a third-party library lives, which this
compiler does not have.

### 8.3 The type mapping

`Unit`, `Bool`, `Byte`, `Char`, `Int`, `Float`, `Ptr`. Nothing else crosses in
either direction: no `String`, no `Array`, no records, no closures, no type
variables.

Each of those already erases to exactly one `RepClass`, which is the whole
reason the list is what it is -- `PRIMITIVES.md` 9.3 had to make the same
choice for raw load and store and made it the same way, for the same reason.
A type outside the mapping is a compile error at the declaration, not at the
call.

Callbacks are the one absence that is a deferral rather than a decision, and
they belong to TIX-65 and TIX-67 because they need the non-allocating
convention that TIX-63 has not built.

### 8.4 Strings copy, and only paths need one

OCaml's route is open to Turkey and this proposal declines it anyway.

Under 8.3's scope the only NUL-terminated arguments that appear are paths --
`open`, and later `stat` and `execve`. Everything else in the residue takes
`(ptr, len)`, because that is what a POSIX call takes. Padding every
`TurkeyString` in the program to serve a handful of call sites is a cost paid
everywhere for a benefit collected in three places.

So `Unsafe.Ptr` grows a copying `toCString` and a `fromCString`, which is
Haskell's `withCString` and Go's `C.CString` at a tenth of their traffic.

**What would reopen this.** A section of the runtime that hands strings to C
in a loop. If the measurement ever shows the copy on a hot path, OCaml's
padding is the answer and it is a one-byte change in
`runtime/turkey_runtime.c` that no other implementation can see -- the padding
is invisible to `length`. Recording it here so the option is not rediscovered.

### 8.5 No variadics

Declined, and the two functions that wanted them leave the list under the
framing above. Go and Haskell both decline and both route to a C wrapper, and
that remedy is unavailable here for the reason the whole sequence exists.

**The consequence to state plainly**, because it is a real bill and not a
footnote: `%.17g` float formatting and `strtod` become Turkey work on the
critical path. That is Ryu or Grisu, it is its own ticket, and it is owed the
moment `turkey_float_to_string` is rewritten whether or not variadics are ever
added.

### 8.6 At most eight general and eight floating arguments, and none on the stack

`Select.gob` says it already: the stack arguments it emits "are this compiler's
own convention, and a C function would not read them the same way", and a
runtime call that overflows calls `stop`. AAPCS64's stack rules are
unimplemented.

Every function in the residue takes six arguments or fewer -- `mmap` is the
widest and it fits. So the restriction costs nothing today, and the reason to
write it down as a *rule* rather than leave it as a `stop` is that a
declaration is user-written and a runtime entry point was not. A ninth argument
must be rejected at the declaration with an error that says why, rather than
selected into a convention the callee does not share.

### 8.7 `memcpy`, `memmove`, `memset`, `memcmp`, and why the residue is LLVM's

These cannot be declined by declining to declare them. LLVM is free to
recognise a copy loop and replace it with `llvm.memcpy`, which lowers to a
call, and the situation is worse than a flag can fix: *"Clang (as well as gcc)
requires that freestanding environment provides memcpy, memmove, memset and
memcmp. None of `-fno-builtin-memcpy`, `-ffreestanding` nor `-nostdlib`
provide a satisfactory answer to the problem."* A C implementation of `memcpy`
is itself a candidate for being rewritten into a call to `memcpy`. Rust's
`no_std` ships `compiler_builtins` for exactly this.

So they stay linked, named here as residue rather than discovered at link time.

**But the residue belongs to LLVM and not to Turkey**, which is worth checking
rather than assuming, and it checks out: the arm64 backend emits no reference
to any of the four. It is a `Ldr`/`Str` machine. The only reach for one in the
whole toolchain is `Llvm.gob`'s `llvm.memset` zeroing a root frame, plus
whatever LLVM synthesises unbidden; `runtime/turkey_runtime.c` uses them twenty
times and is the thing being rewritten.

Both causes are scheduled to go. This entry expires with the LLVM backend.

### 8.8 What this must not foreclose

Zig and Go converged on the same split: raw syscalls on Linux, where "Linux
syscalls are a stable ABI across kernel versions", and the platform libc on
Darwin. Zig "always links dynamically against libSystem ... because this is the
stable syscall interface." Go reached it the expensive way -- it did raw Darwin
syscalls until "binaries were occasionally broken by kernel updates", because
"Apple doesn't commit to a particular syscall ABI", and 1.12 moved to libSystem
"ensuring forward-compatibility with future versions of macOS and iOS" at a
performance loss taken deliberately. Two second-order costs came with it that
are worth knowing before copying it: the switch "triggered additional App Store
checks for private API usage", and `Getdirentries` now fails with `ENOSYS` on
iOS.

Darwin is the host, so the syscall instruction is out of scope and Linux gets
the same treatment through glibc: one mechanism, two symbol tables.

Three decisions above are what keep the other door open, and they are decided
partly *for* that reason rather than incidentally:

* **8.2's safe wrappers are the only public surface.** `System.IO.read` must
  not reveal whether it went through `read@libSystem` or `svc #0`. That
  boundary is the swap point, and it is the whole mechanism by which the
  substrate can change without the library changing.
* **8.5 and the errno decision keep errno out of the call form.** A raw Linux
  syscall returns `-errno` in the result register and has no global at all. Had
  errno been built into the call the way cgo builds it into a second return
  value, a syscall would have needed a different call form rather than a
  different library.
* **File descriptors and `(ptr, len)`, never `FILE*`.** Declining stdio means
  the safe layer already speaks the shapes a syscall takes.

A syscall primitive is then *additive*: `Prim.syscall6` is shaped like the
`Prim.loadI64` family TIX-61 added, not like a `foreign` declaration, and
nothing here has to be revised to admit it.

Unbuilt and blocked by nothing here: no libc on Linux means no `crt1.o`, so
`_start`, the initial stack and `environ`/`auxv` become ours. That is
`LINKER.md`'s problem.

### 8.9 The Python side delegates to POSIX, and does not use `ctypes`

`ctypes` is the obvious answer and it is wrong, for a reason particular to this
project rather than for effort.

`tests/test_native.py`'s premise is that there is no byte-identical oracle
below Core and that **differential execution** replaces it. Back raw memory
with `ctypes` and real `malloc`, and the two arms of that differential stop
being independent implementations -- they become the host's libc, called twice.
A differential whose arms are the same code catches nothing, which is
FINDINGS 43's failure mode generalized.

`PRIMITIVES.md` 9.4 has already argued the specific case. Fresh memory is
poisoned rather than zeroed because "a zero fill would make 'reads back as
zero' an accidental guarantee, and since this side is the oracle, the
differential would then *enforce* the accident." Real `malloc` is worse than a
zero fill, not better: it is plausible garbage that matches often enough to
make the test flaky instead of wrong.

Three further costs follow. Addresses stop being deterministic, and
`RawHeap.reset` exists precisely so that "a program's addresses are a function
of its own allocation sequence and not of what ran before it in this process",
with byte-exact goldens downstream of that. Undefined behaviour stops raising
`TurkeyPanic` and starts segfaulting a pytest worker, in a suite that runs in
parallel. And `tests/test_primitives.py`'s raw-memory tests only mean anything
against a simulation.

**The alternative is not hand-written C semantics.** The residue is about ten
POSIX calls and Python already has every one of them -- `os.read`, `os.write`,
`os.open`, `os.close`, `os.environ`, `os._exit`, the `mmap` module. Each
foreign symbol is a delegation plus a buffer copied in or out of `RawHeap`.
The oracle stays independent, and a symbol with no delegation is a clean
"cannot run this program on this host" with the differential still covering
every stage up to execution.

What this leaves, stated rather than hidden: **the Python implementation
supports the symbols it delegates, not the general feature.** That is an
asymmetry of the same kind as the Python side not having an arm64 backend, and
a general FFI has no user outside the runtime today. If one ever arrives,
`ctypes` behind a flag that the suite never sets is the shape of the answer.

Two properties of this choice are worth noticing. The delegation is to POSIX
*semantics* rather than to libc, so it is reusable verbatim if Linux ever goes
direct to syscalls. And an `mmap`-based allocator written in Turkey would run
against exactly the simulation `RawHeap` already is, which is the one place
where the oracle gets easier rather than harder.

### 8.10 Ownership stays documented

Nobody expresses it in a type system, and Turkey should not be the first to
try on the strength of ten symbols.

Half of it is already enforced, for free and statically: `LowIr.checkReps`
rejects storing a traced pointer through a raw pointer, which is cgo's first
rule caught at compile time instead of at run time. `PRIMITIVES.md` 9.3 gives
the reason -- the block has no header and is not scanned, so "nothing at run
time could tell the two stores apart".

The other half -- that C must not retain a pointer past the call -- needs
lifetimes and gets a sentence in `PRIMITIVES.md` 9.1 alongside the rest of what
is undefined there. The pinning problem that Go, JNI and the JVM all spend
real machinery on does not arise, because the collector does not move objects.

### 8.11 The work

Both implementations, per `CLAUDE.md`, so each item is two edits and a golden
regeneration.

* **Surface.** A `foreign-decl` production in `design.md` 3.1 and 3.3; the
  parser, AST, declaration collection and name resolution on both sides, with
  the `lib/Unsafe/` gate where `Prim.`'s already is; and inference giving the
  declaration a monotype and rejecting anything outside 8.3.
* **Lowering.** `Runtime.gob`'s `Entry` and `entryPoint` are a fixed table
  keyed by primitive name; generalise them so a declaration produces the same
  data. `Select.gob` already dispatches `Runtime(name)` and already lays
  arguments out by bank -- what is new is 8.6's arity check and a result bank
  that comes from the declaration rather than from `resultBank`. `Llvm.gob`'s
  `declareRuntime` and `llvmgen.py` gain the declared signatures.
* **Delegations.** `builtins.py` gains the symbol table of 8.9.
* **Library.** The declarations in `lib/Unsafe/`, with safe wrappers in
  `System.*`. `Prim.ptrAlloc` and `Prim.ptrFree` are deleted from
  `PrimTypes.gob`, `Prims.gob`, `Runtime.gob`, `builtins.py` and `llvmgen.py`,
  and `Unsafe.Ptr.alloc`/`free` point at declared `malloc`/`free` -- which is
  what `RUNTIME-IN-TURKEY.md` says this ticket is for.
* **Docs.** `PRIMITIVES.md` 9.1 gains 8.10's sentence; `SPEC-DELTAS.md` gains
  the numbered decision.

Verification is `test_boot`'s byte diff for everything above Core, and
`test_native`'s differential execution for the ABI, which is the only thing
that actually checks a calling convention. A conformance program that reads an
environment variable and one that allocates, stores at each representation,
reloads and frees are the two that matter, and the second wants a
`TURKEY_GC_STRESS=1` run because the failure mode is the collector following
something it must not. A declaration with nine general arguments must be
rejected with 8.6's error rather than miscompiled.

### 8.12 Sources

- cgo, its pointer-passing rules and the variadic limitation:
  <https://pkg.go.dev/cmd/cgo>, and the proposal that fixed the rules:
  <https://go.googlesource.com/proposal/+/master/design/12416-cgo-pointers.md>
- Go 1.12 moving Darwin to libSystem: <https://go.dev/doc/go1.12>, and the
  issue that argued it: <https://github.com/golang/go/issues/17490>
- GHC's FFI, `capi` for varargs, and safe versus unsafe calls:
  <https://ghc.gitlab.haskell.org/ghc/doc/users_guide/exts/ffi.html>
- Haskell's errno as a library, and the RTS's per-thread `saved_errno`:
  <https://hackage.haskell.org/package/base/docs/Foreign-C-Error.html>
- OCaml's NUL-padded strings and the caveat that comes with them:
  <https://ocaml.org/manual/5.4/intfc.html>,
  <https://dev.realworldocaml.org/runtime-memory-layout.html>
- Modula-3's `EXTERNAL` in unsafe interfaces, and traced versus untraced
  references: <https://en.wikipedia.org/wiki/Modula-3>,
  <https://www.opencm3.net/doc/reference/intro.html>
- Rust's RFC 3484, `unsafe extern` and `safe fn` within it:
  <https://rust-lang.github.io/rfcs/3484-unsafe-extern-blocks.html>
- Rust's `CString` and its interior-NUL error:
  <https://doc.rust-lang.org/std/ffi/struct.CString.html>
- JNI copying versus pinning, and what a moving collector costs it:
  <https://www.ibm.com/docs/en/sdk-java-technology/8?topic=jni-copying-pinning>,
  <https://openjdk.org/jeps/423>
- LLVM's synthesis of `memcpy` and why no flag prevents it:
  <https://lists.llvm.org/pipermail/llvm-dev/2019-April/131973.html>,
  <https://github.com/llvm/llvm-project/issues/56467>
- Zig's `syscall0`..`syscall7` on Linux and libSystem on Darwin:
  <https://github.com/ziglang/zig/blob/master/lib/std/os/linux.zig>,
  <https://deepwiki.com/ziglang/zig/4.5.2-platform-specific-implementations>
- Darwin's arm64 divergence on variadic arguments:
  <https://dyncall.org/docs/manual/manualse11.html>

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

Item 8 is not in this sequence and does not interact with it. It is ordered by
the ticket graph of the runtime work instead: TIX-61 is its prerequisite and is
done, and TIX-65 and TIX-67 wait on it. The only coupling to the list above is
that it adds a declaration form, so the grammar work in (2) and (3) should not
be in flight at the same time.
