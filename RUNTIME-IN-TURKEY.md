# Replacing the C runtime

Status: surveyed, and the first piece is built. TIX-61 landed raw pointers --
`Prim.Ptr`, load and store at each representation, and pointer arithmetic --
which is the ordering constraint this document identifies below and a
prerequisite of both halves. The rest is still undecided; M28 and M29 do not
depend on any of it.

Two things TIX-61 leaves for the tickets after it. `Prim.ptrAlloc` and
`Prim.ptrFree` are `malloc` and `free` behind two runtime calls, standing in
until there is an FFI: TIX-62 declares both directly and deletes them. And the
module gate is a gesture -- `lib/Unsafe/Ptr.gob` puts the name in the import
list of anything touching raw memory -- not the checker TIX-63 will build.

The FFI is now argued, per the recommendation at the foot of this document, as
`PROPOSALS.md` item 8, and built: TIX-62 landed `foreign` as SPEC-DELTAS 71,
declared the symbols in `lib/Unsafe/Libc.gob`, and deleted `Prim.ptrAlloc`,
`Prim.ptrFree` and the two C wrappers underneath them. So the FFI itself is
done, and `System.Env.get` is the first safe wrapper over it.

**Staging step 1 is done (TIX-65), with one piece reclassified.** The streams
(`print`, `write`, `stderr`), both file doors and the construction of the
argument strings are Turkey in `lib/System/IO.gob` and `lib/System/Env.gob`,
over `open`, `creat`, `read`, `write` and `close`. Seven primitives are gone
from both compilers, and `runtime/turkey_runtime.c` went from 1575 lines to
1451. What did not move is the *state* the host hands across: the copied
arguments, which `turkey_main` and the JIT's `ctypes` call write before any
Turkey runs, and the exit flag they read after the last of it returns. That is
the entry section's business rather than the outside world's, so it sits under
its own banner in the C, is reached from Turkey through `lib/Unsafe/Runtime.gob`,
and moves with step 3. Measured cost: none natively -- `boot` compiling itself,
44MB of assembly out through `print`, took 159s before and after. The Python
oracle pays instead, because its `print` is now interpreted Turkey storing a
byte at a time into the simulated heap: the conformance corpus went from 111s
to 131s of CPU. Three findings came
out of the port: a C `int` result is only half defined in an `Int`
(FINDINGS 102), `canRead` was a race and is gone (103), and arguments were the
one door into `String` that skipped the UTF-8 check (104).

**Staging step 2 is done (TIX-66), smaller than planned and cheaper than
feared.** The section was mostly strings, and rather than give Turkey the
intrinsics to reach inside a `TurkeyString`, `String` stopped being primitive:
it is `type String = String(Prim.Array Byte)` in `lib/Data/String/Type.gob`,
erased by both backends, and every string operation is Turkey in `Data.String`,
`Data.Int` and `Data.Char` -- run by the Python oracle rather than written a
second time in it (FINDINGS 106). The reasoning and the survey are "Step 2"
below. Sixteen string primitives are gone from both compilers with their C
bodies, six libm wrappers became `foreign` declarations, a string literal pattern
compares inline, and `array_parts` went with its callers, so C no longer reads
`Data.Array`'s record. Two things stayed and were reclassified: the allocators,
which fill headers over `heap_allocate` and so are the collector's interface
(step 4, TIX-68), and float formatting and parsing (TIX-75).
`runtime/turkey_runtime.c` went from 1451 lines to 1223. Measured cost, not
optimized: `boot` compiling itself went from 165s to 204s, the byte loops now
being Turkey where they were C; the Python oracle, running every corpus
program with `--backend python`, went from 62s to 82s of CPU over the same 91
programs, because a string operation there is interpreted Turkey over a list of
ints where it was a Python `str` method.

It is smaller than the thirty-five below suggest. Under "depend on libc as
little as possible" only about ten of them are an FFI problem at all, five are
instruction selection (`frintm`/`frintp`/`frintn`/`frintz`), and the rest are
Turkey written over `read`, `write` and `mmap`. Two consequences worth carrying
forward: float formatting and `strtod` are now owed in Turkey, because
declining variadics means `snprintf` never arrives; and the `memcpy` family
stays linked, because LLVM synthesises calls to it -- a residue that belongs to
the LLVM backend and to the C runtime, both of which are scheduled to go, since
the arm64 backend emits no reference to any of the four.

`LINKER.md` ends by noting that no way of producing an executable removes the C
dependency, because `runtime/turkey_runtime.c` is C and `boot` cannot compile C.
This asks the follow-on question: could pointer primitives and a small FFI let
the runtime be written in Turkey instead?

**Yes, and it is three separable pieces of very different difficulty, and the
expensive one is not the one it looks like.** It is also worth being precise
about what it buys, because it is not "no libc".

## What the runtime is

Measured, by section:

| Section | Lines | What it needs beyond ordinary Turkey |
|---|---:|---|
| Values: strings, objects, arrays, boxes, closures | 546 | Nothing, once `String` is a library type -- **done**, bar the allocators and float text |
| GC: heap list, mark, sweep, root frames | 310 | Raw memory, **and must not allocate** |
| The outside world: args, files, print, exit | 205 | Nothing but FFI -- **done**, bar the host's handoff state |
| Entry and the big-stack thread | 106 | FFI, and a Turkey function as a C callback |
| Crash diagnostics | 54 | Signal handlers, so also a C callback |
| **Total** | **1221** | |

And the whole libc surface it uses is about thirty-five functions:
`malloc` `free` `realloc` `memcpy` `memmove` `memset` `snprintf` `fprintf`
`fputs` `fwrite` `fread` `fopen` `fclose` `read` `write` `strlen` `strtod`
`getenv` `exit` `signal` `floor` `ceil` `round` `trunc` `fmod` `remainder`
`isnan` and the five `pthread_*`.

Thirty-five is a small FFI. That is the encouraging half.

## What it does *not* buy

**libc stays.** An FFI to libc is still a dependency on libc, and on macOS that
was never optional anyway -- `LINKER.md` establishes that there is no static
`libSystem`, and that Go, which wanted to avoid it more than anyone, was made to
route its Darwin syscalls through it. So this does not remove a library. It
removes *our own C*, which is a different and smaller claim:

* no C **compiler** in the build;
* one language for the whole system;
* `boot` compiling every line it depends on, which is what self-hosting means
  in the strong sense.

Worth stating plainly before costing it, because "get rid of the C part" can
sound like it means shedding a dependency and it means shedding a *toolchain*.

## What the language would need

**1. Raw memory.** A pointer that is not a managed reference, and load/store at
a given representation with pointer arithmetic. This is a new kind of value that
the collector must *not* trace -- which `Rep` already expresses: `traced` is
exactly the bit, and `Ptr` untraced already exists in the IR and is currently
unused. That is a piece of luck worth noticing; the low IR was built with the
distinction in it.

**2. An FFI.** A declaration form for a foreign function, a mapping from Turkey
types to C ones, and a direct call at the C ABI. The backend already emits
`Call(Runtime(name), args)` to exactly these symbols, so the *emission* is
done -- what is missing is the surface syntax and the type mapping.

**3. A low-level subset -- and this is the expensive one.** The collector runs
when nothing may allocate, and has no roots of its own because it *is* the root
scanner. So some functions must compile with no root frames, no allocation, and
no implicit anything. Declaring that is easy. **Enforcing it is a whole-program
analysis**, and that is the real cost.

**4. Callbacks.** A Turkey function usable as a C function pointer, for the
signal handler and the thread entry. Needs the non-GC convention from (3).

## Defining the subset, and what enforcing it costs

Two questions hide inside (3), they have different answers, and the second is
where the cost estimate above came from. **What does the subset forbid**, and
**how does the compiler know**.

### The leaf facts are already in the IR; the join is not

`LowIr.effectsOf` classifies every opcode, and `allocates` is the load-bearing
one, for a reason `Turkey.Ssa` states: "allocation is the only thing that can
trigger a collection, so an allocating instruction is a **safepoint**". It has
been there "from the first commit even though nothing reads it until allocation
exists". So `ObjectNew`, `ArrayNew`, `CellNew`, `ClosureNew` and `Box` already
say what they do, for free.

What is missing is one line, and it is deliberate:

```
Call(_, _) -> everything()
```

> A call may do anything the callee does, which is everything. Knowing better
> than this per callee is an interprocedural summary and is not something this
> phase has.

That summary *is* the enforcement pass, and three things make it cheaper here
than Go's flood:

* the whole program is already in one array -- `LowIr.checkCalls` takes
  `Array (Ssa.Func Low)`, which is exactly the shape a summary needs, so there
  is nothing to collect;
* it runs after `mono`, so a direct call names a symbol rather than a
  type-directed choice;
* the property is a bit on an opcode rather than something to be discovered.
  Go's flood is expensive because `//go:nowritebarrierrec` is a *source*
  annotation and write barriers are introduced long afterwards.

What it is not free of is `Callee`, which has three shapes -- `Direct(String)`,
`Runtime(String)` and `Indirect(Value)` -- and the third is a closure call with
no known target. A summary must either call every indirect call allocating,
which makes closures unusable inside the subset, or work out which closures
reach which call site, which is the expensive analysis Go's flood is an
instance of.

That one fact is most of the argument for the next section.

### By effect, or by type

Go defines its subset as an **effect on functions**. Modula-3, Oberon and
RPython all define theirs by **which types may be named**. The choice decides
whether enforcement is a whole-program flood or a local check.

| the subset is | enforcement | who |
|---|---|---|
| "this function does not allocate" | interprocedural, transitive, imprecise at indirect calls | Go |
| "this code holds no traced reference" | local and syntactic: a signature either mentions a managed type or it does not | Modula-3, Oberon, RPython |

Turkey can have the second, and the bit it needs is already spelled. `Rep` is a
register class *plus* `traced`; `untraced(Ptr)` is expressible today and
unused; and `Ssa.verify` already enforces the invariant that keeps the bit
honest -- only a pointer may be traced -- for a reason its comment gives: "a
code address is a pointer the collector must *not* follow, and instruction
selection is where the first one appears."

If the collector is written against untraced pointers and raw load/store and
names no managed type, then **it allocates nothing because there is nothing to
allocate**: no records, no constructors, no arrays, no closures, no strings.
The interprocedural summary becomes unnecessary, and so does the indirect-call
problem, because there are no closures to call.

**Why this fits here and does not fit Go.** Go's runtime manipulates Go's own
types constantly -- slices, maps, interfaces -- so a subset that forbade them
would forbid the runtime, and an effect on functions is the only line left to
draw. Our collector manipulates the heap as bytes: headers, mark bits, the free
list, the frame table, root slots. That is a property of this collector rather
than a general truth, and it is what buys the cheaper enforcement.

### Measured: the collector is all-raw

TIX-63 names the thing: code in modules the compiler holds to this rule is
**giblets** -- the innards under the skin -- and a module of it is a *giblet
module*.

The route stood on a claim -- that the collector really is all-raw -- and
TIX-63 took the measurement before building anything on it. **It holds.**
Nothing in the collector handles a managed value *as a value*; every managed
object it touches is read as words and bitmaps at offsets it knows.

* **One layout decoder.** `mark_children` (`runtime/turkey_runtime.c:352`) is
  the only code that knows what a heap object looks like: a cell's
  `pointer_value` flag, an array's element layout code in `tag`, a
  constructor's 3-bit-per-slot layout, 1 bit per slot for everything else.
  Words at fixed offsets, the same thing C does with a struct.
* **The two suspects cleared.** `ArrayStorage` went with TIX-66, and the
  closure shape `[code, env]` is only ever *built* by the allocator and
  *walked* as an object with a bitmap -- no reader needs to know it is a
  closure. The panic path the collector reaches is `turkey_panic(const char*)`
  (653), which takes a C string; `turkey_panic_string` (661), the one entry that
  reads a Turkey `String`, is not reachable from collector code.

So the subset-by-types route is the right one. What the measurement found
instead is a list of *missing pieces*, none of which is an argument for an
effect:

| gap | where | what fills it |
|---|---|---|
| the frame address | `scan_native_frames` (480) uses `__builtin_frame_address` | a primitive |
| unsigned 32-bit loads, `ctz` | `HeapHeader`'s `kind`/`marked`; the free-bit search (151, 527) | primitives |
| bulk fill and copy | `memset`/`memcpy` across the sweep and allocators | loops in a giblet module, or `foreign` |
| region memory | `aligned_alloc` (136), `realloc` for the mark stack (341) | `foreign` declarations |
| sorting the frame table | `qsort` with a C comparator (439) | a hand-written sort, not a callback |
| formatted diagnostics | `snprintf` in `mark_grey` (325), `fprintf` stats (595-627) | `write` and hand formatting; SPEC-DELTAS 71 declines variadics |
| the collector's state | every heap, root and mark global is a C `static` | an untraced global -- arm64 already emits one as a plain word rather than a root slot (`Turkey.Emit.emitData`) -- holding a `Prim.Ptr` to `malloc`ed state, initialized before the first allocation |

The allocators, reclassified as the collector's by TIX-66, are the one place
where giblet code meets a managed reference, and they are where the type rule
had to be checked hardest. It holds there too, with one change to lowering;
"What stays in C, and whose it is" below has the design.

**What it costs is ergonomics inside the subset.** No `SomeError`, because
packing allocates; no `?`; no `Show`; sentinel returns and raw pointer
arithmetic. Against idiomatic Turkey that is a severe dialect. Against the 310
lines of C it replaces it is a wash -- and Modula-3's claim is that it need not
even be that: "In most other respects, traced and untraced references behave
identically."

### The spectrum

Five positions. The first is untenable and the last is unaffordable, for
different reasons.

| | what it is | what it costs | what it buys |
|---|---|---|---|
| **0** | nothing: discipline, review, `GC_STRESS` | zero | nothing -- and this is the one section where being wrong is silent |
| **1** | a post-lowering checker: mark the entry points, join `effectsOf` over the call graph, report | one pass over a function array that already exists | a real check, with errors naming a monomorphized function and often a call that *lowering* introduced |
| **2** | the same, with provenance back to source | plumbing spans through lowering | makes the failures that will actually bite readable -- dictionaries past `mono`'s cap, existential packing, a capturing closure. None of those are written by anyone |
| **3** | a declared subset on function types: a `nogc` function may call only `nogc` functions | a contract that closures, class methods and polymorphism must all answer to | errors at the call site, in source, early |
| **4** | the bit inferred on arrows, so `map` is `nogc` exactly when its argument is | an effect system, in two implementations, plus golden regeneration | the best ergonomics available |

Levels 3 and 4 buy ergonomics across a *body* of code. The body here is the
collector's 310 lines and the callback boundary's handful, written once by one
person. **Two consumers totalling a few hundred lines do not pay for an
inferred effect system**, which is why the effort curve is unusually flat at
the top.

What would move it is a third consumer that users write: latency-sensitive
code, a `nosplit` equivalent, or an FFI callback form. If one arrives, the
thing to notice is that arrow-bit inference is already described elsewhere for
a *throws* bit, in Swift's `rethrows` shape. An allocation bit is the same
machinery instantiated a second time -- which is an argument for designing the
bit once and generically if it is ever designed, and an argument against
building it for the collector alone.

## Prior art

**Go** is the most honest estimate of (3), because it has exactly this problem:
a precise, root-based collector written in the collected language. The cost
shows up as a vocabulary of restrictions -- `//go:nosplit`,
`//go:nowritebarrier`, `//go:nowritebarrierrec`, `//go:yeswritebarrierrec`,
`//go:notinheap` -- **and a compiler pass that floods the call graph to check
them**: *"the compiler floods the call graph starting from each
`go:nowritebarrierrec` function and produces an error if it encounters a
function containing a write barrier, and this flood stops at
`go:yeswritebarrierrec` functions."*

That is the shape of the bill. Not the pointer primitives, which are a
week -- the *verification* that runtime code obeys restrictions the rest of
the language does not have, transitively, at compile time. Go's own
`runtime/HACKING.md` exists because those rules cannot be inferred from
reading the code.

**RPython** writes PyPy's GC *in RPython*: *"this is not reference counting; it
is a real GC written as more RPython code"*, made to work by a **GC transformer
that rewrites the flow graphs of everything else** while the collector itself
stays outside the transform. Structurally the same answer as (3): a subset that
the managed-code transformation does not apply to.

RPython also draws its line by type rather than by effect, which is the part
worth taking: `lltype` distinguishes a `GcStruct`, which carries "a
platform-specific GC header" and is collected, from a plain `Struct`, which has
no header and is "suitable for being embedded inside other structures", and raw
memory is `lltype.malloc(..., flavor='raw')` with a matching `lltype.free`.

With a caveat that matters here: **RPython translates to C.** So does Squeak's
VM, written in Slang, a Smalltalk subset, and then translated to C. Both prove
the *subset* idea and neither escapes the C toolchain -- they generate C rather
than depending on someone else's. If the goal is "no C compiler", these are not
precedents for it.

**Oberon** is the precedent that actually is one: Wirth's system, collector
included, in Oberon, compiled by its own compiler, no C anywhere. It is also
from an era with a much smaller platform contract to satisfy -- no dyld, no
code signature, no libc ABI.

And it is the precedent for *how the line is drawn*, which is by import rather
than by analysis. The low-level facilities live in a pseudo-module, `SYSTEM`,
whose name "would appear in the prominently visible import list of every module
making use of such low-level facilities", with the recommendation to "restrict
their use to specific modules (called low-level modules)", which are then
"easily recognized due to the identifier SYSTEM appearing in their import
list." Enforcement is social and the compiler's whole contribution is making
the fact visible. Turkey has modules and import lists, so this is available at
no cost at all -- and it is the floor under level 1 below, not a substitute
for it.

**Modula-3** is the closest precedent to the route recommended below, and it
draws *both* of the lines this document separates. Safety is per module: "In a
safe module, the compiler prevents any errors that could corrupt the runtime
system; in an unsafe module, it is the programmer's responsibility to avoid
them", and "unsafe operations are allowed only in modules explicitly labeled
unsafe." And the heap is split by *type*: "For programs that cannot afford
garbage collection, Modula-3 provides a set of reference types that are not
traced by the garbage collector. In most other respects, traced and untraced
references behave identically."

Traced and untraced references, which is exactly `Rep.traced`, in a language
from 1989. The last sentence is also the ergonomic claim the subset-by-types
route is betting on, from the one system that shipped it.

**Zig** has no GC, so it answers (1) and (2) and says nothing about (3), which
is the part that decides this.

## Staging, and where to stop

The four sections come apart, and conveniently in increasing order of
difficulty. Each is independently shippable and the sequence can be abandoned
at any point with the rest still in C.

1. **The outside world, 205 lines.** Pure FFI: no raw memory, no allocation, no
   GC interaction. Moving it proves the FFI on real code and removes a sixth of
   the C. *Done (TIX-65); the argument and exit state stayed, as step 3's.*
2. **Values, 546 lines.** Needs raw loads and stores but allocates normally, so
   it is ordinary managed Turkey with a pointer type. The largest section and
   the second easiest. *Done (TIX-66), and it needed no raw memory at all:
   `String` became a library type over a byte array. The allocators and the
   float text stayed, as step 4's and TIX-75's.*
3. **Entry and crash diagnostics, 160 lines.** Needs callbacks.
4. **The collector, 310 lines.** Needs the low-level subset and its
   enforcement. Last, and the only one that requires (3).

Stopping after 1 and 2 leaves 470 lines of C -- the collector, the entry, the
signal handler -- and is 60% of the way with none of the hard machinery.

## Step 2: `String` stops being primitive

The values section is mostly strings: twenty-odd C functions behind as many
`Prim.string*` names, over a `TurkeyString` layout nothing else in the program
can see. Writing those functions in Turkey over raw pointers would need a way
from a `String` to its address, an uninitialized-string allocator and a store
into one -- intrinsics, every one, and each a thing the Python oracle would have
to model a second time, since its strings are host `str`s.

**Decided instead: `String` is a library type over a byte array, and keeps its
own invariants.**

```
type String = String(Prim.Array Byte)     -- lib/Data/String/Type.gob
```

Immutable and well-formed UTF-8 by construction, the way `Data.String`'s doors
already promise, with the constructor reachable only from `lib/` because
`Prim.Array` is. Every operation is then ordinary Turkey over an array, the C
and the primitives go, and the oracle runs the *same* Turkey instead of its own
`str` implementations -- so the differential covers the string code for the
first time rather than comparing two unrelated implementations of it. `Bool` is
the precedent for a type declared in `lib/` that both compilers still name.

Performance is not an input. A byte loop is what `memcmp` is too, inlining is
aggressive here, and vectorizing it is a later change inside one module.

### The one representation question, surveyed

Exact-sized array, or `(array, offset, length)` with shared backing? The second
buys O(1) slicing. The peers split, and the split has a history:

* **Rust.** `String` is "a pointer to some bytes, a length, and a capacity" --
  a `Vec<u8>` -- and "`String`s are always valid UTF-8", an invariant the type
  keeps and `from_utf8_unchecked` is `unsafe` for bypassing: violating it "may
  cause memory unsafety issues with future users of the `String`, as the rest
  of the standard library assumes that `String`s are valid UTF-8". Owned, not
  shared; sharing is `&str`, a borrow, which Turkey does not have.
* **Haskell `text`.** `Text` is an array, an offset and a length, so slicing is
  O(1). 2.0 moved the array from UTF-16 to UTF-8; Channable's data set went from
  3.08 GiB to 1.55 GiB. A library type over a byte array, exactly this shape,
  with sharing.
* **Java.** A library class over an array, and the counterexample: 7u6
  *removed* `offset` and `count` and made `substring` copy, because a small
  substring kept its whole parent alive -- sharing turned O(1) slicing into a
  leak. JEP 254 (compact strings) then measured `char[]` at 10–45% of live data
  across 960 heap dumps, and got 5–15% back by going to `byte[]`.
* **Go.** A built-in `(pointer, length)` header, substrings share, and Go 1.18
  added `strings.Clone` for the leak Java removed sharing over: "it guarantees
  to make a copy of s into a new allocation, which can be important when
  retaining only a small substring of a much larger string."
* **OCaml.** A primitive block; `Bytes.create` then `Bytes.unsafe_to_string` is
  the mutate-then-freeze route this design gets for free, because a `String`
  is built into an array the builder owns and nothing else sees.

Go and OCaml differ because their strings are primitive and their runtimes are
C and assembly -- the arrangement this step exists to leave.

**Exact-sized, in a newtype.** Both backends erase a single-field newtype
(`DeclTable.newtypes`), so a `String` *is* a kind-2 byte array at run time with
no wrapper object -- `(array, offset, length)` would be a three-field record, an
extra object on every string, and Java's leak. Slicing copies, as it does today.
The cost, recorded rather than optimized: the header goes from `TurkeyString`'s
8 bytes to an array's 24.

### Literal patterns

`match s { "let" -> … }` compares against a string the compiler knows. Today it
is a call to `turkey_string_eq`, and with the C gone there are three routes: a
Core rewrite to an equality test, a call to the Turkey `eq` by symbol, or
comparing inline. Core keeps patterns as nested AST (`CMatch`'s note declines
exactly this rewrite), and a call by symbol needs a binding kept alive past
`mono` and `opt` that no program references. **Inline:** a length test, then one
byte compare per byte of the literal -- what a C compiler makes of `memcmp`
against a short constant. No call, no safepoint, and the literals in patterns
are keywords.

### What stays in C, and whose it is

* **The allocators** -- `object_new`, `array_new`, `cell_new`, `box`/`unbox`,
  `closure_new` and the entry's literal interning. They fill headers over
  `heap_allocate`, which is the collector's safepoint and its region allocator,
  and a managed object cannot come from `malloc`: it needs a header and an
  allocation bit in a region the collector owns. They are the collector's
  allocation interface and move with step 4. The closure shape, `[code, env]`,
  stays pinned there.
* **Float formatting and parsing** -- `float_to_string`, `float_parse` and
  `float_can_parse`, TIX-75's, because `snprintf` and `strtod` never become
  foreign declarations. They read and build the new representation meanwhile.

`array_parts` and its hard-coded `ArrayStorage` slots go with their three
callers, so nothing in C reads `Data.Array`'s record any more.

### The allocators as giblet code

Decided under TIX-63 and built with step 4 (TIX-68). An allocator makes a
managed reference, and giblet code may not name a traced type -- so this is
where the type rule was most likely to break. It does not, and the reasons are
worth stating because two of them are invariants the collector must keep.

* **Operands arrive untraced, and that is safe.** `array_new`'s fill value and
  `cell_new`'s value may be managed pointers; a giblet allocator receives them
  as `Int` or `Prim.Ptr`. That is sound for two reasons, and both are
  load-bearing: the *caller* roots every operand across the call
  (`Turkey.Roots.across` counts the instruction's own uses), and the collector
  **does not move objects**, so a pointer held in an untraced register still
  names the same object afterwards. A moving collector would break this, and
  would have to revisit the allocator interface first.
* **Results leave untraced, and become traced at the opcode.** A giblet
  allocator returns `Prim.Ptr`. The generated code's `ObjectNew` or `ArrayNew`
  already has a `traced(Ptr)` result, so the conversion happens at the
  boundary between opcode and allocator -- nowhere in source, and `checkReps`
  still forbids making a traced pointer from an address everywhere else.
* **`closure_new` is split in lowering.** It is the one allocator that makes
  *two* objects, and it keeps the environment alive across the closure's
  allocation with a C root frame (`runtime/turkey_runtime.c:837-849`). Giblet
  code can hold that environment only as an untraced `Prim.Ptr`, which a
  collection in between would free. So `ClosureNew` becomes two single-object
  allocations in the *generated* code, where the environment is an ordinary
  rooted value. Every allocator then calls `heap_allocate` exactly once and
  holds nothing managed across it, and the type rule needs no exception.
* **Linkage is the callback mechanism.** Generated code reaches the allocators
  by symbol at the C ABI (`Turkey.Select.runtimeCall`). A Turkey direct call
  passes a leading environment, and a binding nothing references does not
  survive `mono` and `opt`. So a Turkey allocator needs a C-callable export
  that is kept alive -- which is exactly what TIX-67 builds for the signal
  handler and the thread entry. That makes TIX-67 a prerequisite of TIX-68 for
  a reason beyond ordering.

## The risk worth naming

The collector manages roots that this compiler emits, and writing it in the
language whose roots it manages closes a loop. This session took the native
backend from **0 of 28 to 28 of 28** under `TURKEY_GC_STRESS=1` by finding four
separate kinds of missing root, three of which no amount of reading suggested.
A collector written in Turkey would be subject to the same emission it depends
on, and `GC_STRESS` -- currently the thing that catches everything -- would be
testing the collector with the collector.

That is not an argument against doing it. It is an argument for doing it *after*
there is a second way to check the collector, and for keeping step 4 last.

## Recommendation

**Split the question, and do the cheap half on its own merits.**

**An FFI is worth having regardless of the runtime.** Turkey today cannot call C
at all, which is a limitation of the language and not of the runtime. Justify it
as a language feature, size it against the thirty-five functions above, and the
runtime rewrite becomes a *user* of it rather than the reason for it. That also
makes it testable independently -- a program that calls `getenv` is a test, and
does not need a collector rewritten first.

**The low-level subset should wait for a reason beyond this.** It is the piece
that pays for the collector and nothing else, its true cost is a whole-program
enforcement pass on Go's evidence, and the collector is the one section where
being wrong is silent. Neither M28 nor M29 needs it: stage1, stage2 and stage3
can all link a prebuilt `libturkey.dylib`, and self-hosting in the sense the
plan means -- `boot` compiling `boot` -- is unaffected by which language the
collector is in.

**Raw pointers are the ordering constraint.** They are a prerequisite for both
halves and are the smallest of the three pieces, and the `traced` bit that makes
them expressible is already in the IR. If any of this is done, it is first.

**The subset is defined by types, not by an effect on functions.** Decided, on
the reasoning under "By effect, or by type": the collector manipulates the heap
as bytes rather than as Turkey values, so "names no traced type" forbids
everything "does not allocate" was meant to forbid, and it is checked locally
instead of by an interprocedural summary that `Callee.Indirect` would make
imprecise anyway. Modula-3 is the precedent and `Rep.traced` is the bit. The
claim this rests on -- that the collector is genuinely all-raw -- is a
measurement to take before the route is committed to, and the suspects are
named above.

**Enforcement stops at a checker.** Levels 1 and 2 of the spectrum: an
allocation summary over the low IR, and then the provenance that makes its
errors readable. Levels 3 and 4 are declined for now, not deferred vaguely --
an inferred effect on arrows is an effect system in two implementations, and
the code it would serve is a few hundred lines with two consumers. The
condition that would reopen it is a third consumer that users write, and the
design to reach for then is the *throws* bit's, generalized, rather than a
second one built alongside.

## Sources

- Go runtime pragmas and the call-graph flood that enforces them:
  <https://github.com/golang/go/blob/master/src/runtime/HACKING.md>
- Go's hidden pragmas: <https://dave.cheney.net/2018/01/08/gos-hidden-pragmas>
- RPython, a GC written in RPython and inserted by a flow-graph transformer:
  <https://rpython.readthedocs.io/en/latest/garbage_collection.html>
- PyPy architecture, translation to C:
  <https://aosabook.org/en/v2/pypy.html>
- RPython, `lltype` and raw-flavour memory:
  <https://rpython.readthedocs.io/en/latest/rtyper.html>,
  <https://rpython.readthedocs.io/en/latest/rffi.html>
- Modula-3, safe and unsafe modules, traced and untraced references:
  <https://www.opencm3.net/doc/reference/intro.html>
- Rust's `String` and its UTF-8 invariant:
  <https://doc.rust-lang.org/std/string/struct.String.html>
- Haskell `text`, and its move to UTF-8: <https://hackage.haskell.org/package/text>,
  <https://www.channable.com/tech/so-long-surrogatesa>
- Java's `substring` stopping sharing in 7u6:
  <https://www.infoq.com/news/2013/12/oracle-tunes-java-string>; compact strings
  and their measurement: <https://openjdk.org/jeps/254>
- Go's `strings.Clone`: <https://pkg.go.dev/strings#Clone>
- Oberon, the SYSTEM pseudo-module: <http://www.ethoberon.ethz.ch/SYSTEM.html>,
  and Wirth on why it is a module:
  <https://people.inf.ethz.ch/wirth/Articles/Modula-Oberon-June.doc>
