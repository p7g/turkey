# Error handling

Status: **direction decided; layout contract selected and prototyped (Python,
Core down); recovery contract open; nothing built.** The plan is at the end. The
survey is kept because it is what would otherwise be redone, and because it
reversed the question this started from.

The design document for how Turkey signals and recovers from failure, and for
the existential types that design needs.

## The question, and why it changed

It started as: `Either` for errors is a kind of function colouring, which Go
avoids for concurrency, so should Turkey either (a) keep `Either` but make the
error type an existential so nothing needs converting, or (b) replace `panic`
with exceptions as the one mechanism?

Two corrections came out of the survey.

**`Either` is not a colour in the sense `async` is.** A colour is viral *and
cannot be discharged*: a sync function cannot await. Any caller can `match` an
`Either` away. What `Either` really costs is higher-order code: a fallible
closure passed to `map` needs `traverse`, and then `foldM`, `forM`, and a second
copy of every combinator. `?` is do-notation over `Monad` (design.md 6.9), so
Turkey gets Haskell's `map`/`mapM` split exactly. That duplication is the real
problem; the syntax is not.

**Go is a counterexample to "avoid colours, so avoid error values".** Go hid
async behind goroutines and kept errors as visible values. Its `error` is option
(a) without sum types. After check/handle (2018), `try` (2019), `?` (2024) and
"literally hundreds" of community proposals, the Go team stopped pursuing error
syntax in 2025. Go's concurrency argument is about hiding async, not hiding
errors.

And the two options are not alternatives. They answer two independent
questions:

* **How the payload is typed** -- concrete sum, existential, or inferred union.
  This decides conversion friction.
* **How it propagates** -- as a value, or non-locally. This decides whether a
  fallible closure can pass through `map`.

Option (b) needs (a)'s existential anyway: an unchecked `raise` has to carry
*something*, and `catch` of a specific type needs a downcast.

## What errors are, measured

These bound how much the mechanism matters at all.

* **Duffy, Midori:** "90-something% of the typical uses of exceptions in .NET
  and Java became preconditions" -- bugs, not recoverable errors.
* **Sutter, P0709:** preconditions and allocation failure "outnumber all other
  failure conditions by ~10:1"; in Go and Rust "slightly over 90% of all
  functions do not report errors". 52% of C++ developers have exceptions banned
  in part or all of their code.
* **Yuan et al., OSDI '14:** 92% of the 48 catastrophic failures studied resulted
  from incorrect handling of non-fatal errors explicitly signalled in software, across Cassandra, HBase, HDFS, MapReduce and Redis.
  About a third were trivial: empty handler, log-only, `TODO`.
* **Nakshatri et al., MSR '16:** 20% of Java catch blocks are empty; the most
  common handler logs without recovering.
* **Weimer & Necula, OOPSLA '04:** over 800 cleanup-path mistakes in about 4M
  lines of Java.
* **Go Developer Survey 2024 H1:** 13% name error-handling verbosity a top
  challenge, second only to learning the language (15%).

These observations support separating bugs from recoverable failures and testing
error handlers carefully. They do not establish a comparative failure rate for
visible versus invisible propagation: the studies examine particular systems,
not controlled comparisons of language mechanisms. Marked propagation is a
design preference informed by this evidence, not a measured reliability result.

## Survey

| Peer | Payload | Propagation | Fallible closure in `map` | Cost / lesson |
|---|---|---|---|---|
| Go | `error` interface (existential) | value, unmarked | n/a | Declined all syntax changes. An unrecovered goroutine panic kills the process. |
| Rust | typed `Result` + `From` | `?`, marked | `try_map`-style duplicates; keyword generics scoped to `const`/`async` | Ecosystem split: `anyhow` (existential) for apps, `thiserror` (typed) for libraries. |
| Haskell | `Either`/`ExceptT`; IO exceptions via `SomeException` + `Typeable` (Marlow 2006) | monadic / unmarked | `mapM`, `traverse` | |
| Swift | `any Error` (existential); typed throws added in Swift 6 (SE-0413) | `try`, marked | `rethrows` | Error in a callee-saved register (r12/x21): "one instruction to zero out the error register ... and one instruction ... to jump-if-not-zero into the catch block after each call". |
| Midori | checked exceptions, plus abandonment for bugs | `try`, marked | n/a | Compiled both ways; tables came out "roughly 7% smaller and 4% faster" than return codes. |
| Java | checked + unchecked | unmarked | broken: lambdas cannot throw checked exceptions | |
| Zig | inferred error sets | `try`, marked | n/a | No payload. |
| Kotlin 2.4 (experimental) | error unions `T \| E` | `?.` | n/a | Error types may not have supertypes or generic parameters, "to keep unions tractable". |
| OCaml | unchecked exceptions; polymorphic variants for typed results | unmarked | works | OCaml 5 effect handlers cost a mean 1% on programs that do not use them. |
| Koka | effect rows | inferred | `map` is effect-polymorphic in its type | Needs row polymorphism. |
| Scala 3 `CanThrow`, Effekt | capabilities | implicit | unchanged `map` works: the closure captures the capability | A capability escapes through a returned closure; closing that needed capture checking. |
| Trio | any | exceptions | | A child task's exception cancels its siblings and is re-raised in the parent (structured concurrency). |

The Koka and OCaml rows rest on the papers' abstracts, not a full reading.

## What is particular to Turkey

**The native backend already implements Swift's propagation, for panics.**
`turkey_panic` sets a flag, and every call is followed by a test of it
(NATIVE-BACKEND.md, "Panic propagation -- late, definitively ... Nothing can
optimize it away"). The propagation checks are already paid for today; this does not
establish the total cost of catchable errors, payloads, or trace capture. What is missing is a handler that tests-and-clears instead of
returning, and a heap payload in place of `panic_buffer`, rooted while it
propagates. In Core, `effectsOf` already says a panic traps.

**Capabilities do not fit.** Scala's scheme needs local evidence -- a `given`
introduced by a `try` -- and Turkey resolves instances globally. Turkey closures
also escape freely, which is exactly the hole capture checking was built to
close.

**Inferred unions do not fit.** Zig- and Kotlin-style unions need subtyping,
which HM(X) does not have, and Kotlin had to restrict its error types to keep
inference tractable.

## Options considered

* **A. `Either e a` with `?`, plus an existential `SomeError`.** Cheap. Errors
  stay ordinary data in Core, where `test_boot` reaches. Removes conversion
  friction without the MPTC `From` would need (plan.txt already declined that).
  Leaves the `mapM` duplication.
* **B. Unchecked exceptions carrying `SomeError`.** Fixes fallible closures and
  the backend is mostly ready. But no call site says it can fail, contrary to the preference for
  marked propagation. The failure studies do not establish that this choice
  itself causes more outages; Go's syntax decision is not a comparison of B
  against A either.
* **C. Swift's model.** A throws bit on function types, an untyped `SomeError`
  payload, `try` at call sites. Where Swift writes `rethrows`, Turkey would infer
  a throws-bit variable on arrows, so `map` needs no annotation -- Koka
  restricted to one effect. The colour remains wherever closures are stored in
  data or class methods. Midori and Swift are the only peers with both marked
  propagation and fallible closures through `map`.

## Decisions

1. **Existential constructors, as a general feature.** A special-cased
   `SomeError` needs the same inference and Core plumbing, so it would not be
   cheaper.
2. **`Either SomeError a` with the existing `?` is the recoverable-error
   channel for combining failures from different sources.** A source packs its
   concrete payload once; intermediate callers propagate it with `?` without
   wrapper sums or repeated conversions. Concrete error sums remain useful
   where callers need exhaustive recovery. Entering the shared channel from
   such an API still requires explicit packing; `?` does not insert conversion.
3. **`panic` stays the channel for bugs.** It becomes recoverable only at a
   boundary -- Go's `recover`, Rust's `catch_unwind`, an Erlang supervisor --
   reusing the flag. Catching alone does not provide task isolation or restore
   partially mutated state. Recovery is deferred until a concrete boundary
   defines surviving state, cleanup, nested recovery, and fatal failures.
   Structured concurrency will additionally need cancellation and parent
   propagation semantics.
4. **Fallible-closure pain is recorded, not predicted.** Every place in `boot/`
   where a fallible closure forces a `traverse`-shaped duplicate goes in
   FINDINGS. If that becomes a pattern, the move is **C, not B**. The backend
   half of C is small; its cost is arrow-bit inference in both implementations.
5. **Not doing:** unchecked exceptions (B), inferred error unions, capabilities,
   effect rows, a `From`-style conversion class, and GADTs (below).

## Existential constructors

### Syntax

A constructor takes a context in the same position a `fun` does. Every type
variable in its fields must be a type parameter or be bound in that bracket, so
a typo is still an unbound-variable error rather than a silent existential. A
bare variable in the bracket binds it unconstrained.

```
type SomeError = SomeError[Error e](e)

type Counter = Counter[s] { state : s, step : fun(s) -> s, read : fun(s) -> Int }
```

`[` cannot start a constructor argument today, so this is unambiguous.

Packing is explicit, and the instance is resolved at construction. This minimal
example omits the trace field added by the standard error wrapper below:

```
Left(SomeError(ParseError { line = 3, text = "expected '}'" }))
```

Opening is a `match` arm or a function-parameter pattern:

```
match err {
    SomeError(e) -> print(message(e))
}

fun leak(SomeError(e)) = e   -- error: the type of 'e' would escape the pattern that opened it
```

### Restrictions, v1

1. **No `~` in a constructor context.** Class givens over fresh skolems keep
   principal types (Läufer & Odersky 1994); equality givens are what make GADTs
   lose them.
2. **Existential patterns only in `match` arms and function parameters, not
   `let`.** GHC has the same rule.
3. **No `.field` on an existential-typed field.** It is opened with a pattern,
   or the skolem has no scope to live in.

### Peers

| Peer | Form | Lesson |
|---|---|---|
| Läufer & Odersky 1994 | existentials on data constructors | Compatible with HM, principal types kept; the only new check is escape. |
| GHC | `data T = forall a. Show a => MkT a` | Not in `let`/`where` bindings. `SomeException` recovers the type through a `Typeable` superclass. |
| Luke Palmer, "existential typeclass" antipattern | a record of closures instead | Same result with no type-system work, unless you need a downcast. |
| OCaml | GADT syntax `Any : 'a t -> any`, or first-class modules | Naming the hidden type needs `(type a)`. |
| Swift | implicit `P`, then explicit `any P` (SE-0335) | Existentials were overused because their costs did not show. Packing should be visible. |
| Rust | `dyn Trait` with dyn-compatibility rules | Methods taking `Self` by value, or generic methods, are uncallable. Turkey gets the same from the escape check. |
| Scala 3 | dropped `forSome` | Bad interaction with subtyping. Keep existentials tied to constructors. |

The record-of-closures alternative is real: `type SomeError = SomeError
{ message : fun() -> String }` needs no existential type-system work and supports reporting through a common
interface. Existentials retain the original heterogeneous payload and its class
evidence without repeated conversions. Downcasting is a separate capability,
useful when recovery needs that original type, and can ship later.

### Implementation

What exists already: skolems with ranks and an escape check (SPEC-DELTAS 40),
`CAssume` with named dictionary givens, dictionaries as record values, and the
generic fallback plus layout-keyed sharing for calls that never become ground.
Every item below is in both `turkey/` and `boot/`.

* **Parser, AST, `astdump`:** an optional context after a constructor's name.
* **`decls`:** `ConInfo` gains `exists` and `context`; the scheme becomes
  `forall params exists. context => fun(args) -> T params`. Validate that
  existential variables do not reach the result type and that the context has no
  equalities.
* **`infer`, construction:** the constructor is instantiated like a constrained
  function, so its context becomes wanted predicates and elaboration produces
  `SomeError[t](%inst.Error.T)(x)`. This is the existing path, unchanged.
* **`infer`, patterns:** existential variables become skolems at a fresh rank,
  and the arm body is wrapped in a `CLet` with those skolems and a `CAssume` whose
  givens are the unpacked dictionaries. Delta 40's escape check then rejects an
  arm whose type mentions a skolem. This is the one spec change: `CAssume` says
  its declared-type source is "deliberately the whole of it", and patterns become
  a second source, justified by restriction 1.
* **`core`, `CAlt`:** gains a list of evidence names. `CAlt` deliberately does not
  restate binder types, but a dictionary's *name* cannot be derived; the checker
  still derives its type.
* **`coretc.pattern`:** existential fields get fresh rigid `TCon`s instead of
  `_head_mapping`, evidence names are typed `%Dict.C sk`, and the arm type is
  checked free of `sk`.
* **`mono`:** a type application at a type mentioning a skolem is not ground. It
  keeps its `CTyApp` and dictionary and calls the generic binding -- the exit the
  polymorphic-recursion cap already uses. The devirtualizer must not collapse a
  selection off a pattern-bound dictionary.
* **`layout`:** fields are stored at the packed type's own layout, and the
  packed value records one layout code per hidden variable. `share` copies
  each opened arm per packed layout key, shares bindings that pack at their
  own binders, and prunes to the keys reachable packings store. Boxing the bare
  field was the earlier proposal and does not work even for `SomeError`; see
  the layout milestone below.
* **`opt`:** case-of-known-constructor substitutes the type and the dictionary,
  so pack-then-match in one function specializes and devirtualizes fully.
* **Backends:** dictionaries add traced pointer fields, but the work for hidden
  nested layouts is not yet established. Do not assume no backend changes.

### Correctness milestone: nested layouts

Before committing to the full implementation, demonstrate construction and
consumption of `type Packed = Packed[a](Array a)`. An `Array Int` is a pointer,
but its element layout is not determined by the consumer's fresh skolem.
Boxing the outer field does not change the array's contents. The existing
`transparent_parameters` checks and layout-keyed sharing address precisely
producer/consumer agreement; a generic fallback alone is not a proof of it.

Investigate carrying layout evidence with the existential first, including how
that evidence selects operations or compiled layout variants. The alternative
is a uniform representation throughout exposed nested values, with conversions
at packing boundaries. Neither approach is selected yet. Account for closures
accepting or returning those values and for mutable containers: copying a
container must not silently break aliasing semantics.

Acceptance requires unpacking and processing arrays with scalar and pointer
payloads through generic code, nested containers, and closures over the hidden
type, as well as the scalar round trip. Verify optimized and generic paths and
GC tracing. This milestone determines the representation contract and the
remaining backend work.

### Why boxing the bare field is not enough, even for `SomeError`

The native backend has no uniform representation to fall back on. A producer
and a consumer each compute a layout from the static type in front of them and
must agree: `layout_of` answers from the type, arrays are flat at the element
layout fixed when they were created (`turkey_array_new` records it in the
header, but compiled reads never consult it), closure calls coerce arguments to
the static parameter layout, and a dictionary's methods are compiled at the
instance's layout. `layout.share` keeps generic bodies honest by making one
copy per layout *of a type argument at a call site*, and `check_layouts`
refuses a transparent lambda parameter nothing gave a layout.

An arm opening `Packed(xs)` defeats both. The hidden variable is never a type
argument, so sharing has nothing to key on; `xs` is a pattern binder, not a
lambda parameter, so the check never sees it; and a read falls back to `BOXED`
against an array of `i64`. Nor does the minimal `SomeError[Error e](e)` escape:
boxing the field stores `e` safely, but `message` from an `Error Int` instance
takes an `i64`, and the arm holding the box calls it.

The same producer/consumer disagreement already exists without existentials.
A generic `fun mk(x : a, n : Int) -> Box a` left past the specialization cap
writes a boxed pointer into a field that a ground `Box Int` reader loads as an
integer, and prints `32067093649` for `42`. It is pinned as a strict xfail in
`tests/test_layout.py` (NATIVE-BACKEND.md, "A hole to close first").

### Survey: representation-polymorphic code in peers

The question each peer answers is what code that does not statically know a
value's representation does with one.

| Peer | What they built | Measured / budget | Lesson for Turkey |
|---|---|---|---|
| .NET CLR (Kennedy & Syme 2001) | JIT-time sharing by representation: "all reference types are compatible", "primitive types are mutually incompatible, even if they have the same size", structs compatible when they "share the same pattern of traced pointers". Shared code receives precomputed dictionaries of type handles. | Stack benchmark, seconds, object-based / shared-polymorphic / hand-specialized: `int` 8.5 / 1.8 / 2.0, `double` 10.4 / 2.0 / 2.0, `Point` 10.5 / 4.3 / 4.3. Creating `List<T>` in shared code: specialized 4.2, runtime type lookup **288**, lazily filled dictionary 4.9. | Layout-keyed sharing is `layout.share` already, and it costs nothing against specialization. The expensive thing is *computing* representation information at the use; precomputing it where the type is known is the whole difference. |
| Go 1.18 (GC-shape stenciling) | One body per GC shape ("same underlying type or they are both pointer types"), plus a dictionary of type descriptors, sub-dictionaries and itabs for everything shape does not settle. | Compile time "roughly 15% slower" in 1.18, recovered by 1.20. No published runtime numbers in the design; PlanetScale's 2022 analysis reports method calls through a type parameter as a double indirection, slower than an interface call. | Same split as the dispatch contract below: shape picks the code, a dictionary carries the rest. Go's cost is method calls through the dictionary; Turkey's devirtualizer already removes those whenever the dictionary is ground. |
| Swift | Unspecialized generic code receives type metadata; a value witness table gives size, alignment, copy, destroy, and code does `alloca(T->vwt->size)`. Existential containers are a three-word inline buffer plus metadata and witness tables; specialization is an optimization, not the model. | Budget is separate compilation and a stable ABI across library versions; specialization "can only [happen] if the definition … is visible in the current Module". | Fully dynamic layout is what a language pays when it cannot see the whole program. Turkey is whole-program and already refuses unknown layouts, so it would buy Swift's cost without Swift's constraint. |
| GHC | Existential variables must be of lifted (boxed) kind; levity polymorphism forbids binders and arguments whose representation is unknown, because "the code generator needs to know the runtime representation of every bound variable". | Uniform boxed representation for anything polymorphic. | Sidesteps the question by never unboxing a polymorphic value. Turkey unboxes `Array Int` and closure arguments by layout, so the sidestep is not available without undoing FINDINGS 77/78. |
| OCaml | Uniform representation with one dynamic exception: `float array` is flat, and polymorphic array code tests the header tag (254) on every access. | LexiFi: a runtime cost and code size that "greatly increases"; no numbers. The exception forces every type to be *separable*, which is why OCaml rejects unboxed existentials; OxCaml (OCaml 2025) added a separability axis to the type system to recover them. | The closest analogue of reading `array->tag` at runtime. One dynamic layout case cost a type-system restriction on exactly the feature being built here. |
| Rust `dyn Trait` | A trait object is a data pointer plus a vtable of the concrete type's methods; generic methods and by-value `Self` methods are not callable through it. | Full monomorphization elsewhere. | The hidden type is reachable only through code compiled at the concrete type. Turkey's arms can index an `Array a` directly, which a vtable-only design would have to forbid. |

### Contracts considered

1. **Layout evidence plus dispatch to layout-keyed copies.** Packing stores a
   layout code for each hidden variable next to the dictionaries. Opening
   lifts the arm into a binding abstracted over the hidden variable and
   switches on the stored code, calling the copy `layout.share` would build for
   that layout. Inside each copy the variable has a concrete layout, so arrays,
   closures, dictionary methods and nested containers take existing paths, and
   nothing is copied, so aliasing holds. Whole-program compilation bounds the
   switch to the layouts actually packed. Cost: code size, one arm copy per
   reachable layout per hidden variable. This is .NET's and Go's split with the
   dictionary filled at the pack site, which is where .NET measured 4.9 against
   288.
2. **Dynamic layout in generic code.** Swift's model: operations read the
   layout at runtime. Touches every backend operation on both native backends,
   slows code that never uses an existential, and OCaml's single dynamic case
   shows the restriction it can force.
3. **Uniform representation at the packing boundary.** Convert `Array Int` to
   an array of boxes and wrap closures when packing. Breaks aliasing: a
   mutation through the opened array is invisible to the original. Rejected
   unless the prototype finds a reason to revisit.

### Result: contract 1, prototyped

**Selected: layout evidence plus dispatch to layout-keyed copies.** A
Python-only prototype passes every acceptance case above on the native
backend, the Python backend and the evaluator, at the default specialization
cap and at zero, with and without `TURKEY_GC_STRESS`
(`tests/test_existential_layout.py`). There is no syntax or inference yet:
each program is an ordinary source file for the helpers plus hand-built Core for
the pack and open sites, which is the elaboration step 2's front end has to
produce.

What the prototype is:

* **Representation.** A packed object is one `i64` layout code per hidden
  variable, then the carried dictionaries, then the declared fields. Codes
  are the collector's 3-bit layout codes. `ConInfo` gains `exists` and
  `context`, and its scheme is `forall params exists. fun(dicts..., fields...)
  -> T`; the evaluators see the dictionaries as leading arguments and no codes.
* **Opening.** `ast.PCon` carries the arm's `evidence` names, its `skolems`,
  and, after sharing, the `layouts` that copy was made for (the prototype's
  stand-in for `CAlt`'s evidence list). `layout.share` copies each opened arm
  once per layout key and rewrites the copy's body with the skolem's layout
  known, keyed `-uid` in `abstracted`, so calls at the skolem find `f@[i64]`
  like any other layout-keyed call. The backend takes a copy when the stored
  codes match its key.
* **Refusal instead of guessing.** `layout_of` answers "unknown" for a skolem
  with no layout, `held_at` then refuses, and `check_layouts` refuses an
  existential arm that reached the backend uncopied.

Test coverage: scalar payloads of every layout through a bare-variable generic
(`pick`) and repacked at the skolem; arrays of `Int`, `Bool`, `Float`, `String`,
`Byte` and `Char` through generic helpers; `Array (Option a)` and
`Array (Array a)`; a closure `fun(a) -> a` whose results are written back into
the flat array; a carried `Show` dictionary called on elements (the
`SomeError`-at-`Int` case); aliasing through two packings of one array; packing
in a generic body past the cap, both through `Array a` and through a bare `a`
with a dictionary; and escape of a skolem from its arm, refused in Core.

Each safety property was checked by breaking it. With dispatch removed the
compiler refuses the program. With every packing storing "boxed" the output is
wrong (`0.0`, `60`, a stray code point). With `layout._packs` removed the
bare-variable generic packing prints wrong values.

What it found:

1. **An unknown layout must not fall back to `ptr`.** The first version answered
   `ptr` for a skolem with no layout, like every other `TCon`. With dispatch
   disabled the `Int`, `Bool`, `Float` and `String` tests still passed:
   `i64`, `i1` and `ptr` are all 8-byte words, and the wrong name read the right
   bits. Only `Byte` (1 byte) and `Char` (4 bytes) payloads could have
   noticed. The fix is the refusal above, and the tests keep narrow payloads.
2. **A body that packs needs its layouts as much as one that destructures.**
   `packOne[a](d, x : a)` is not transparent, so `layout.share` left it generic
   past the cap. It held `x` boxed and stored "boxed", and the opened copy then
   called an `i64` method with a box. `layout._packs` adds such bindings to the
   shared set. It is the existential form of NATIVE-BACKEND.md's `mk : a -> Box
   a` hole; the general form is still the xfail in `tests/test_layout.py` and
   should be closed by the same rule extended to every construction.
3. **Copies multiply under inlining.** In the `packed_arrays` program, measured
   in backend IR instructions, the cost of one layout is about 1,400
   instructions. Copying for all 8 layouts gives 11,767. Copying only for keys
   some reachable packing stores gives 8,919 (6 layouts; `layout._packed_layouts`
   counts a packing inside an arm copy only if that copy's own key is packed).
   The same program written as a generic function, with no existential, is
   3,759 fully specialized or 3,257 at cap zero. Almost all of the difference is
   `main` (8,726 against 1,520): `opt` inlined the opener at six call sites, and
   each inlined `match` kept every copy, because case-of-known-constructor is
   disabled for existential patterns in the prototype. Step 2's `opt` item
   (substitute the packed type and dictionary on pack-then-match) is therefore
   a size requirement, not only a speed one. Without inlining, the dispatch
   itself costs one load and compare per hidden variable per arm.
4. **Evaluators need nothing but the evidence names.** Layouts are the native
   backend's; `pygen` and `eval` take the first matching copy.

Not established by the prototype: nested *existential* openings of two hidden
variables at once (the product of keys is implemented but untested), recursive
existential types, and anything in `boot/`.

Sources for this section:
[Kennedy & Syme, Design and Implementation of Generics for the .NET CLR](https://www.microsoft.com/en-us/research/publication/design-and-implementation-of-generics-for-the-net-common-language-runtime/),
[Go GC shape stenciling](https://go.googlesource.com/proposal/+/refs/heads/master/design/generics-implementation-gcshape.md),
[Go 1.18 dictionaries](https://go.googlesource.com/proposal/+/master/design/generics-implementation-dictionaries-go1.18.md),
[PlanetScale, Generics can make your Go code slower](https://planetscale.com/blog/generics-can-make-your-go-code-slower),
[Pestov & McCall, Implementing Swift Generics](https://llvm.org/devmtg/2017-10/slides/Pestov-McCall-ImplementingGenerics.pdf),
[Swift TypeLayout](https://github.com/swiftlang/swift/blob/main/docs/ABI/TypeLayout.rst),
[Swift OptimizationTips](https://github.com/swiftlang/swift/blob/main/docs/OptimizationTips.rst),
[Eisenberg & Peyton Jones, Levity Polymorphism](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/11/levity-pldi17.pdf),
[LexiFi, About unboxed float arrays](https://www.lexifi.com/blog/ocaml/about-unboxed-float-arrays/),
[Taming the Flat Float Array Optimization (OCaml 2025)](https://conf.researchr.org/details/icfp-splash-2025/ocaml-2025-papers/6/Taming-the-Flat-Float-Array-Optimization-Tracking-Separability-in-the-Type-System),
[Rust reference, trait objects](https://doc.rust-lang.org/reference/types/trait-object.html).

## Stack traces

Capture once when a concrete error enters the shared error channel. Provide a
general `captureStack() -> StackTrace` runtime primitive and call it from the
standard packing function; neither `Either` nor monadic `?` needs special rules.
The full wrapper is conceptually:

```
type SomeError = SomeError[Error e] { payload : e, trace : StackTrace }

fun error[Error e](value : e) -> SomeError {
    SomeError { payload = value, trace = captureStack() }
}
```

Sources use `Left(error(ParseError { ... }))`. Propagation preserves the same
immutable trace. Raw construction is explicit; automatic capture is a property
of the standard packing function, not every existential constructor. Adding
context should retain the original error and trace as a cause rather than
repacking it and losing its origin.

The trace means **the call stack where the error entered `SomeError`**. Packing
after the producing function returns cannot recover its departed frames. It
also does not record later propagation through stored values or other tasks;
those would need separate breadcrumbs. Keep tracing out of `?`, whose generic
`bind` semantics also serve monads unrelated to errors.

`runtime/turkey_runtime.c` already walks registered panic frames and snapshots
pointers to static source-location records. Generalize that operation to return
an independently owned snapshot, rooted appropriately for GC, instead of using
the global panic snapshot. Multiple live errors must retain independent traces.
Capture frame locations immediately; defer formatting until reporting.

Stack capture observes execution context. Give it an explicit effect contract
so optimization cannot move, merge, or cache captures as pure computations.
Ensure frame registration and source metadata survive at capture sites. Define
what optimized traces include under inlining and tail calls; complete logical
call histories are not promised by the current frame machinery. Test optimized
captures, repeated captures, and traces surviving collection. Existing panic
instrumentation is a starting point, not a claim that capture is free.

## Downcasting

`cast` is ordinary Turkey over two things the language cannot supply.

**Type representations come from the compiler.** A hand-written instance could
lie and make `cast` unsound, which is why GHC has rejected user `Typeable`
instances since 7.10. The representation is plain data and its `Eq` is plain
Turkey; delta 43's qualified constructor names make the string unique.

```
type TypeRep = TypeRep(String, Array TypeRep)   -- qualified constructor, arguments
type Proxy a = Proxy
class Typed a { fun typeRep(Proxy a) -> TypeRep }   -- instances derived by the solver only

class Error e : Typed e {
    fun message(e) -> String
}
```

The packed `Error` dictionary carries `Typed` as a superclass, so `cast` compares
the wanted representation with the packed one. Dictionary pointer identity is not
enough: generic code builds fresh dictionaries for instances with contexts.

**Use a checked primitive, not a public unchecked coercion.** The library API is
`cast[Typed a](err : SomeError) -> Option a`. Its implementation passes the
compiler-derived source and target type evidence to a trusted primitive which
compares type identity and converts representations only on equality. Specify
how the checker ties the source evidence to the payload and the target evidence
to the result type; arbitrary user-created `TypeRep` data is not authorization
to cast. Representation equality must cover all supported type forms and
respect type normalization.

The previously proposed `Prim.unboxAs : fun(a) -> b`, restricted only to uniform
inputs, is insufficient: a boxed `Int` cannot become a `String`, and unrelated
pointer-shaped types have compatible storage without being interchangeable.
Scalar debug tags cannot establish type identity. No unchecked coercion is
exposed to ordinary Turkey code.

After a successful comparison, conversion is identity for compatible pointer
representations and unboxing for concrete scalars; generic results must obey the
chosen layout contract. Nested existential layouts remain a prerequisite, not
something the type comparison fixes. This checked primitive avoids requiring
general equality givens or GADTs.

Downcasting can ship after packing and propagation. The `Typed` superclass above
is the eventual design; the initial `Error` class can contain only `message`.
Test successful scalar and parameterized casts, mismatches between distinct
pointer-shaped types, generic calls, and rejection of forged evidence/user
`Typed` instances. Verify signature-variable scope in the implementation;
SPEC-DELTAS 13 describes lexical annotation scope, but the proposed `cast` must
also work through the current signature-checking path.

## GADTs: not now

With existentials, the syntax step to GADTs is only allowing `~` in a
constructor context. Everything else is the expensive part:

* **Solver:** general equality givens. Delta 39's are only `F τ ~ τ` rewrite
  rules; GADTs need `sk ~ τ`, decomposition, and inconsistent givens meaning an
  unreachable arm.
* **Inference:** principal types are lost. OutsideIn(X) makes outer variables
  untouchable under an equality given, which in practice means signatures on
  GADT-matching functions. Ranks are the machinery, but `CAssume` is solved
  inline today and OutsideIn defers implications -- a restructuring of the
  solver.
* **Local let generalization:** OutsideIn comes with `MonoLocalBinds`, and Turkey
  generalizes local syntactic-value lets. Vytiniotis et al. measured turning it
  off: 20 of 533 base modules (3.7%) and 127 lines (0.13%) needed changes, and 95
  of 793 Hackage packages (12%) failed to compile.
* **Core:** layouts force explicit coercions. `IntLit(n) -> n` returns an `Int` at
  type `a`, which a shared generic body holds boxed, so Core needs System FC-style
  cast terms that every pass preserves. OCaml avoids this only because its
  representation is uniform. The payoff would be a typed `cast` with no unsafe
  primitive.
* **Exhaustiveness:** `eval : Expr Int` need not cover `BoolLit`; `exhaustive.py`
  would have to consult types (Lower Your Guards for the complete answer). Delta
  61 makes a non-exhaustive match an error, so imprecision rejects correct
  programs.
* **`mono`:** specialized copies have arms with inconsistent givens to drop.

Their real-world uses are typed requests (Haxl), typed keys into heterogeneous
maps, schemas with open-ended interpreters (OCaml's `Format`, `data-encoding`),
representation witnesses (Jane Street), and shape-indexed IRs (Hoopl). A typed
AST for Turkey itself is not one: Core's types are data, not Turkey types. A JSON
library wants classes, plus a `Codec` record if one description should drive both
directions.

What keeps the door open: the constructor-context bracket (so `~` slots in),
`CAlt`'s evidence list (it could hold coercion names), and the checked cast
remaining behind a narrow trusted boundary (so it could later use equality
evidence). The evidence that
would reopen this is a FINDINGS entry where a type-indexed structure is wanted.

## Plan

1. **Resolve existential layouts.** *Done as a prototype:* contract 1, see
   "Result: contract 1, prototyped". What moves to step 2 from it: `ConInfo`'s
   `exists`/`context`; evidence and layouts on the opened pattern (or `CAlt`);
   `coretc`'s pattern rule and escape check; `mono._ground` refusing skolems;
   `layout.open_arms`, `_packs` and `_packed_layouts`; `backend_lower.packed`,
   `lower_opened` and the skolem case of `layout_of`; evidence binding in
   `pygen` and `eval`. Every one needs its `boot/` mirror (`Core.gob`,
   `CoreTc`, `Mono.gob`, `Layout.gob`, `SsaLower.gob`, the evaluator), and
   `opt`'s pack-then-match rule is required before any real program uses
   existentials, per finding 3.
2. **Existential constructors.** A SPEC-DELTAS entry, then the implementation
   list above on both sides, with goldens regenerated for `CAlt`'s evidence.
   Tests: escape rejected (including through enclosing variables), `let` pattern
   rejected, `~` rejected, independent openings remain distinct, pack-then-match
   specializes, and the agreed nested-layout cases pass through generic code.
3. **`Error`, `SomeError`, and stack capture.** Add the standard packing function
   and independently owned traces. Use `Either SomeError a` for library paths
   combining heterogeneous failures; retain concrete sums where exhaustive
   recovery is useful. Verify propagation preserves the original trace and
   optimized capture respects its effect and lifetime contract.
4. **Checked downcasting.** Add solver-derived `Typed` instances and trustworthy
   type evidence; reject user instances. Implement checked `cast` with the
   positive and negative cases above, without exposing `unboxAs`.
5. **Recoverable panics, deferred.** First specify a concrete boundary's state,
   cleanup, nesting, and fatal-failure contract. Then implement a rooted heap
   payload and recovery using the flag. Clearing the flag alone is not task
   isolation. Open: whether `panic` should also accept a `SomeError`.
6. **Collect evidence throughout.** Fallible closures that force
   `traverse`-shaped duplicates go in FINDINGS. Revisit option C if they form a
   pattern.

## Sources

* [Duffy, The Error Model](https://joeduffyblog.com/2016/02/07/the-error-model/)
* [Sutter, P0709 Zero-overhead deterministic exceptions](https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2019/p0709r4.pdf)
* [Yuan et al., Simple Testing Can Prevent Most Critical Failures (OSDI '14)](https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf)
* [Nakshatri et al., Java exception handling patterns (summary)](https://neverworkintheory.org/2016/04/26/java-exception-handling.html)
* [Weimer & Necula, Finding and Preventing Run-Time Error Handling Mistakes](https://people.eecs.berkeley.edu/~necula/Papers/rte_oopsla04.pdf)
* [Go: \[On | No\] syntactic support for error handling](https://go.dev/blog/error-syntax)
* [Go Developer Survey 2024 H1](https://go.dev/blog/survey2024-h1-results)
* [Swift Error Handling Rationale](https://github.com/swiftlang/swift/blob/main/docs/ErrorHandlingRationale.md)
* [Swift's error handling implementation (Groff)](https://mjtsai.com/blog/2017/08/31/swift-error-handling-implementation/)
* [Marlow, An Extensible Dynamically-Typed Hierarchy of Exceptions](https://simonmar.github.io/bib/extexceptions06_abstract.html)
* [Kotlin KEEP-0441 Rich Errors](https://github.com/Kotlin/KEEP/blob/main/proposals/KEEP-0441-rich-errors-motivation.md)
* [Zig documentation](https://ziglang.org/documentation/master/)
* [Odersky et al., Safer Exceptions for Scala](https://plg.uwaterloo.ca/~e45lee/publication/safer-exceptions/safer.pdf)
* [Brachthäuser et al., Effects as Capabilities](https://pl.cs.uni-tuebingen.de/publications/brachthaeuser20effects/)
* [Sivaramakrishnan et al., Retrofitting Effect Handlers onto OCaml](https://arxiv.org/abs/2104.00250)
* [Xie & Leijen, Generalized Evidence Passing](https://xnning.github.io/papers/multip.pdf)
* [Rust keyword generics initiative](https://rust-lang.github.io/keyword-generics-initiative/explainer/effect-generic-bounds-and-functions.html)
* [Smith, Notes on structured concurrency](https://vorpus.org/blog/notes-on-structured-concurrency-or-go-statement-considered-harmful/)
* [Vytiniotis, Peyton Jones & Schrijvers, Let Should Not Be Generalised](https://www.microsoft.com/en-us/research/publication/let-should-not-be-generalised/)
* [OutsideIn(X)](https://www.microsoft.com/en-us/research/publication/outsideinx-modular-type-inference-with-local-assumptions/)
* [System F with Type Equality Coercions](https://www.microsoft.com/en-us/research/publication/system-f-with-type-equality-coercions/)
* [Lower Your Guards](https://www.microsoft.com/en-us/research/publication/lower-your-guards-a-compositional-pattern-match-coverage-checker/)
* [Garrigue & Rémy, Ambivalent Types for Principal Type Inference with GADTs](https://link.springer.com/chapter/10.1007/978-3-319-03542-0_19)
* [Marlow et al., There is no Fork: Haxl](https://simonmar.github.io/bib/haxl-icfp14_abstract.html)
* [Ramsey, Dias & Peyton Jones, Hoopl](https://www.cs.tufts.edu/~nr/pubs/hoopl10.pdf)
* [Minsky, Why GADTs matter for performance](https://blog.janestreet.com/why-gadts-matter-for-performance/)
