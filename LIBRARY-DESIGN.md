# General-purpose library and language ergonomics

Status: **agreed direction; implementation pending.** Consolidated on 2026-09-15.
This document records the decisions from the general-purpose library discussion,
including module organization, imports, exports, and user-defined operators.
Where an API or syntax is illustrative, it is labelled as such. Open details
are collected in section 10 rather than presented as implemented guarantees.

## 1. Goals and relationship to existing documents

Turkey is a general-purpose programming language. Its library should prioritize
modularity, correctness, and convenience. Advent of Code is one validation
workload, alongside Turkey's compiler, developer tools, and ordinary applications;
it does not define the library's scope or delivery order.

The standard library is batteries included. Tooling for Turkey should itself be
implemented in Turkey, so filesystem, process, structured-data, error, and
resource facilities precede the dependency tools that need them. A package
registry is not a prerequisite for this work. Graph algorithms may be useful,
but their priority must be justified against general-purpose workloads.

Relationships:

- [STDLIB.md](STDLIB.md) preserves the AoC measurements, experiments, and iterator
  discussion. This document supersedes its `Data`/`Protocol`/`Algorithm` public
  layout and its AoC-led delivery priorities. The resource discussion here also
  constrains future generator work.
- [ERRORS.md](ERRORS.md) owns the error and existential-type design. This document
  adds promotion, context, and cleanup requirements; it does not replace that
  design with exceptions or implicit conversions.
- [design.md](design.md) describes the language. The module and operator decisions
  here require coordinated specification and implementation changes before they
  describe actual compiler behavior.

### Design principles

1. Represent domains directly with composable values. Textual syntax can be a
   convenient front end to those values.
2. Make the usual correct operation easy to discover and use. Correct Unicode
   and time APIs need not sacrifice ergonomics.
3. Use Turkey's features where they help. Choose the appropriate algebra rather
   than requiring every builder to be a `Monad`.
4. Keep explicit codecs, comparators, and similar strategy values available.
   Classes may select defaults; they must not be the only customization route.
5. Hide representation and compiler coupling behind public interfaces.
6. Validate APIs through complete programs, including errors and early exits.

## 2. Public modules: flat roots, organized by domain

There is no visible `Std.` prefix. A module owns a coherent subject, including
its types, protocols, and operations. Generic algorithms remain independent of
representations without requiring a universal `Algorithm` namespace.

Examples of intended public names:

```text
Bool, Int, Float, Option, Either, String, Bytes
Array, Map, Set, Deque, Heap, Iteration
Compare, Hash, Numeric, Function
Error, Resource, IO, File, Path, Process, Env
Parse, Regex, JSON
Text.Normalize, Text.Case, Text.Segment
Time.Clock, Time.Calendar, Time.Zone
Random, Net.HTTP, Net.TLS, Testing
```

This is a navigation sketch, not a promise to ship every module immediately or
a requirement for one public module per type. Detailed boundaries such as
`Function`'s contents remain subject to API experiments.

| Domain | Related facilities presented together |
|---|---|
| `Iteration` | Iterable protocol, iterator type, adapters, reductions, collection |
| `Compare` | Equality and ordering protocols; explicit comparators |
| `Hash` | Hashing protocol, hasher interfaces, implementations |
| `IO` | Reading/writing protocols, buffering, copying |
| `JSON` | JSON values, codecs, default-codec class, parsing and encoding |
| `Resource` | Managed acquisition, scopes, cleanup composition |

Protocols and algorithms should also be available as documentation indexes.
Users should be able to find all protocols, all instances for a type, and all
operations accepting a protocol without making those indexes the namespace tree.

Implementation modules can differ from public facades. Re-exports must preserve
canonical identities, instance ownership, and an acyclic import graph. Existing
plans for compiler-integrated declarations under `Turkey.Internal.*` remain
compatible with flat public names; they are not a public root requirement.

### Acronyms

Capitalize acronyms in module names: `JSON`, `IO`, `HTTP`, `TLS`, `UTF8`, `URI`.
Ordinary words remain title case: `Regex`, `Resource`, `Time`. Migration of
existing library and bootstrap names is separate implementation work.

### Collision policy

A user source module must not silently replace a standard-library module with
the same canonical module name. An exact collision is a diagnostic. Applications
can use names such as `MyTool.File` and import aliases when they need a local
concept that shares a library module's short name.

The resolver must detect collisions rather than merely choosing whichever file
wins a search-path ordering. Future package identity should be distinct from
public module spelling. Runtime type identity, including `TypeRep`, must include
package identity once multiple packages can contain identically named modules.

## 3. Imports are qualified by default

| Declaration | Names available |
|---|---|
| `import Net.HTTP` | `Net.HTTP.request`, `Net.HTTP.Response`, etc. |
| `import Net.HTTP as HTTP` | `HTTP.request`, `HTTP.Response`, etc. |
| `import Net.HTTP (request, Response)` | The selected bare names, plus `Net.HTTP` qualification |
| `import Net.HTTP (..)` | All exported bare names, plus `Net.HTTP` qualification |
| `import Net.HTTP ()` | No exported names in scope; instance-only dependency |
| `import Net.HTTP as HTTP (Response)` | Bare `Response`, plus `HTTP` qualification |

An alias is the qualifier for that import; it does not additionally create the
original qualifier. Separate imports may provide additional access paths.
Importing `Net.HTTP` does not implicitly create the short alias `HTTP`.
Qualified access refers only to exported contents, never private declarations.

Instances retain their existing global-coherence behavior. Selective name imports
are not a mechanism for choosing competing instances. `import Prelude ()` retains
its existing special role of opting out of the implicit Prelude dependency.
The ordinary implicit Prelude remains a deliberately small source of bare names.

### Operators in imports

Importing a module qualified does not bring its symbolic operators into infix
scope. Operators are selected explicitly or imported through `(..)`.
Illustrative spelling, subject to the operator grammar:

```text
import Regex
import Regex ((<|>))

let answer = Regex.literal("yes") <|> Regex.literal("no")
```

If distinct imported operator declarations have the same symbol, using that
symbol is an ambiguity error. The compiler must not choose an operator, its
precedence, or its associativity from operand types. Re-exporting the same
canonical declaration does not create a distinct operator.

## 4. Paths determine identity; exports determine the interface

Remove the requirement to repeat a module name in source. Relative to the source
root, `Net/HTTP.gob` is `Net.HTTP`.

Replace the named module header with an export declaration:

```text
export (request, Response)
```

Use `export (..)` to explicitly export everything. Existing distinctions between
exporting a type abstractly and exporting its constructors should be preserved
when adapting the export grammar.

Reusable modules require an explicit export declaration. An entry script may
omit one. Precise treatment of an entry script that is also imported must be
specified during implementation; entry status must not create competing module
identities or inconsistent imported interfaces.

A repeated name would offer a misplaced-file diagnostic and let identity survive
moving a file, but those benefits do not outweigh duplicate information in a
path-based system. Moving a file changes its module identity and requires the
corresponding import updates.

### Source roots

A program has one source root. By default it is the entry file's directory.
All local imports resolve relative to that same root, including imports from
nested modules:

```text
project/
    Main.gob
    Net/
        HTTP.gob
        Headers.gob
```

Running `Main.gob` makes `project/` the default root. Inside `Net/HTTP.gob`,
`import Net.Headers` still resolves to `project/Net/Headers.gob`.

Provide an optional command-line source-root override for nested entry points.
The flag's spelling is not decided. Running `project/Tools/Generate.gob` cannot
by itself reveal whether `project/` or `project/Tools/` is the intended root.
Do not search arbitrary parent directories until an import happens to resolve.

No manifest or project file is required for ordinary use. Module components
must match file paths with exact casing, including on case-insensitive hosts.

## 5. User-defined operators

Adopt a constrained user-defined infix mechanism instead of continually adding
compiler-known symbols for library abstractions.

### Operators alias named functions

Illustrative declaration syntax:

```text
infix left Combine (<>) = combine
infix left Choice (<|>) = choice
infix left Bind (>>=) = bind
```

An operator refers to a named function or class method. That declaration carries
the implementation and type signature. Operator use elaborates to an ordinary
uncurried two-argument call. Named calls remain available for documentation,
qualified use, and contexts where symbolic notation is less clear.

Operator notation introduces no new overloading mechanism. Existing class-based
resolution still applies when the target is a class method. An ordinary target
function can have heterogeneous operands; homogeneous arithmetic comes from
existing arithmetic class signatures, not from infix syntax.

### Initial scope

- Binary infix operators only.
- Module-level declarations only.
- A fixed set of language-defined, named precedence groups.
- Explicit associativity, with the consistency rules specified before implementation.
- Ordinary canonical identities, imports, exports, and ambiguity diagnostics.
- Existing structural punctuation and built-in operators remain reserved.

Users may choose a precedence group but may not define new groups or arbitrary
precedence relationships initially. Exact group names, their ordering, the
operator character set, and the declaration syntax remain open.

Defer custom prefix/postfix operators, sections, symbolic constructors, local
fixity declarations, and changes to existing built-in meanings.

### Evaluation

User-defined operators are strict ordinary calls, with the language's existing
evaluation order. They do not introduce short-circuit evaluation:

```text
Regex.literal("yes") <|> Regex.literal("no")
```

can build two descriptions and combine them. By contrast,
`tryPrimary() <|> tryFallback()` evaluates both calls before invoking the operator.
Delayed work must be represented by a description or explicit thunk. Existing
short-circuit Boolean syntax remains compiler-defined.

Operators complement `?`, particularly for composition that is applicative or
bidirectional rather than monadic. The library should design useful uncurried
composition operations instead of copying a curried operator vocabulary wholesale.

### Compiler work

The current checker already treats method-backed operators as calls. The main
work is in both front ends:

1. Tokenize operator symbols without disrupting comments, arrows, structural
   punctuation, postfix `?`, or newline handling.
2. Resolve precedence and associativity after the relevant declarations/imports
   are available. The current loader initially parses before loading imports;
   imported fixity cannot simply be added to its static table.
3. Carry operator identity and fixity through module exports and resolution.
4. Elaborate to ordinary calls, retaining source information for diagnostics.
5. Check matching behavior in the Python and bootstrap implementations.

Parsing unresolved operator chains followed by fixity resolution is a candidate
implementation. No new backend instruction or general type-inference mechanism
is intended.

## 6. Errors: promotion and context

Retain the [ERRORS.md](ERRORS.md) direction: typed error values, existential
`SomeError`, existing monadic `?`, and panics for bugs with recovery at isolation
boundaries. No implicit `From` mechanism or error-specific rewrite of `?` is added.
Re-examined on 2026-09-17 against the associated-family and multi-parameter
routes, and unchanged: a conversion class is not part of this work. ERRORS.md
decision 5 and plan.txt both stand.

Promotion is an ordinary library operation, built from two pieces that each
belong somewhere other than "errors on `Either`".

**Mapping the left is `Bifunctor`, not an error operation.** An earlier draft of
this section gave the operation as `mapError : fun(Either e a, fun(e) -> f) ->
Either f a`. That name presumes the left is an error, which is exactly the
assumption `Either` declines to make -- `Either` is not `Result`. The general
operation is Haskell's `Data.Bifunctor.first`. (`Control.Arrow.left` is the same
function reached through `ArrowChoice` at `(->)`; it is an accident of the arrow
hierarchy rather than the thing meant, and is not the precedent to follow.)

```text
class Bifunctor f {
    fun bimap(f a b, fun(a) -> c, fun(b) -> d) -> f c d
}

instance Bifunctor Either { ... }

first[Bifunctor f]  : fun(f a b, fun(a) -> c) -> f c b
second[Bifunctor f] : fun(f a b, fun(b) -> d) -> f a d
```

This is expressible today, with no language change. Kind `* -> * -> *` class
variables are inferred from the method signature exactly as `Monad`'s `* -> *`
is, written down nowhere; the class and the `Either` instance above compile and
run (checked 2026-09-17). `second` must agree with `Functor.map (Either l)`, a
law nothing checks.

`Either.mapLeft` is then the thin, discoverable wrapper on the module that owns
the type:

```text
mapLeft : fun(Either l r, fun(l) -> m) -> Either m r
```

**A pair should be a `Bifunctor`, and today it cannot be.** Not because of the
class system -- saturated tuple instance heads such as `instance Flip (a, b)`
already work -- but because `TTuple` is its own type former of kind `*` rather
than a constructor that can be partially applied, and type aliases are saturated
(delta 28) so `type Pair a b = (a, b)` does not provide a head either. That is a
type-representation change, tracked separately as TIX-43. `Bifunctor` does not
wait on it; `Either` is a legal instance now, and user-defined types such as
`Validation e a` are the other instances the class serves.

**Promotion is `mapLeft` through the packing function**, and so belongs in
`Error` rather than on `Either`:

```text
promote[Error e] : fun(Either e a) -> Either SomeError a
promote(x) = Either.mapLeft(x, fail)
```

```text
let contents = promote(File.readText(path))?
```

Whether it earns a name at all is open: `Either.mapLeft(x, fail)` is already
short and composes, and a named wrapper is worth adding only once call sites
show it repeated. If it does earn one, `promote` is the spelling to use --
`lift` will be read as the monad-transformer operation, and `liftEither` already
means `Either e a -> m a` in `Control.Monad.Except`, a different operation.
`Error` owns `fail` and knows nothing about `Either`; `Either` owns `mapLeft`
and knows nothing about errors. The rule that packing goes through the one
standard packing function, so a trace is captured once and an already-packed
`SomeError` is not wrapped again, then has a single home in `fail`.

APIs may retain concrete errors where exhaustive handling is useful. Integration
boundaries can use `SomeError`. The existing plan to convert standard-library
error paths should determine which errors merit a concrete public API.

Context wrappers should retain the original cause rather than only its rendered
message. Define outer-error casting and cause-chain search separately. Exact
context APIs, cause representation, and handling of already-packaged errors remain
open; avoid accidental repeated existential wrapping.

## 7. Resource scopes and generators

**Direction:** deterministic scoped cleanup, with a convenient `with` construct
and library-defined resource descriptions. The exact syntax, result typing, and
interaction with monadic elaboration require a dedicated specification.

### Language and library responsibilities

The language/runtime supplies reliable finalization across normal completion,
`return`, `break`, `continue`, and propagating panics. The design must leave room
for cancellation. Successful acquisition establishes cleanup exactly once;
nested resources release in reverse acquisition order. Failed acquisition must
not leave earlier acquisitions unreleased.

The library describes acquisition and release. A `Resource a` description may
support mapping and monadic composition, with `Resource.use` as the functional
entry point. Illustrative surface syntax:

```text
with File.reader(path) as input {
    ...
}
```

This is not a claim that acquisition cannot fail. Specify how fallible acquisition
and cleanup affect the block's result before implementing the syntax.

Generic cleanup must not silently suppress a body error. Error recovery and
transaction commit/rollback remain explicit operations.

Proposed failure policy:

| Body | Cleanup | Outcome |
|---|---|---|
| Success | Success | Body result |
| Failure | Success | Original failure |
| Success | Failure | Cleanup failure |
| Failure | Failure | Original failure with cleanup failures attached |

A panic remains a panic, with cleanup failures retained as diagnostics. Buffered
output should offer an explicit fallible finish/flush operation. Cleanup must
continue attempting remaining releases when one release fails.

Lexical syntax alone cannot prevent escaping handles or captured references in
Turkey. The initial direction is runtime-checked invalidation after closure;
static lifetime safety would require additional language machinery. Forced process
termination is outside ordinary scoped-unwinding guarantees.

### Iteration borrows; scopes own resources

`Iterator a` remains a shared, resumable cursor. Plain `for` does not close it on
loop exit. Otherwise the existing multiple-consumer/resumption requirement breaks.

A managed iterator can be obtained through a resource description:

```text
with File.lines(path) as lines {
    for line in lines {
        if enough(line) { break }
    }
    -- The same cursor may resume here.
}
-- The owning scope releases the resource even if iteration was incomplete.
```

Adapters normally borrow their sources. They must not guess that exhaustion or
consumer abandonment grants permission to close a shared input.

Generator-owned resources must attach to the scope managing that execution.
Normal completion can release them early; scope exit releases remaining resources.
Do not depend on garbage-collection timing, exception injection at `yield`, or
resuming arbitrary suspended body code merely to run cleanup.

A scoped operation around monadic `yield` must span execution of the suspended
computation, not merely construction of its description. A resource-aware builder
operation or explicit compiler elaboration is needed. Resolve this before
finalizing the generator API.

## 8. Domain descriptions and builders

### Regular expressions

Provide composable regex descriptions, with string syntax as a convenience front
end. A monadic automaton-construction builder is a plausible implementation.
Keep the representation private so construction may use fragments, an intermediate
representation, or a later compilation phase.

Distinguish construction-time dependence from dependence on matched input.
Arbitrary match-dependent bind can describe nonregular languages and cannot in
general be compiled to a finite automaton. Regex composition should preserve its
regular-language contract; general input-dependent parsing belongs in `Parse`.
Typed captures, sequencing, choice, and repetition can be exposed independently
of arbitrary monadic bind.

### JSON codecs and schemas

Explicit `Codec a` values are the primary customization mechanism. A class selects
a default codec; users can supply alternate codecs without violating instance
ownership or creating wrapper types solely to choose another encoding.

A decoder can be monadic. A bidirectional codec also needs the reverse mapping:
a function constructing a record does not explain how to extract its fields.
Build codecs through products, construction/decomposition functions, tagged
alternatives, validation, optional fields, and explicit defaults. Document
round-trip behavior and normalization.

A builder can offer convenient syntax without promising `Monad Codec`. Arbitrary
validation predicates are not necessarily expressible as JSON Schema; distinguish
codec validation from the guarantees of an exported schema. Reflection is not
required for direct decoding into user-defined types.

### Applicative composition

The current `Applicative` supplies only `pure`. Add an independent combining
operation, such as `map2` or a product operation, to support static descriptions
and independent validation without requiring `Monad`. Exact signatures and any
builder syntax are to be designed around Turkey's uncurried functions.

### Time and Unicode

Use Temporal as the design basis for calendar/time APIs. Distinguish exact
instants, local dates/times, zoned values, calendar-relative arithmetic, and
elapsed time. Expose policies for ambiguous or nonexistent local times; add a
monotonic clock for measurement independently of calendar functionality.

Preserve valid UTF-8 strings. Provide explicit byte, scalar-value, and grapheme
views; normalization, case mapping, and case folding belong in coherent text
APIs. Ordinary equality must not silently normalize. Grapheme count is not a
promise of terminal display width. Specify Unicode versions and tailoring where
applicable, with ergonomic default operations and explicit specialized choices.

## 9. Priorities and validation

Prioritize foundations useful for Turkey-written tools:

1. Error implementation, promotion, contextual causes.
2. Resource scopes, panic cleanup, and the generator lifetime model.
3. Bytes, I/O, paths, filesystem, environment, and subprocesses.
4. Applicative composition and explicit JSON codecs.
5. Testing support and complete Turkey-written tools exercising these facilities.
6. Regex, Unicode, and Temporal domain libraries.
7. Dependency tooling implemented in Turkey on those foundations.

Module/import changes and constrained operators are enabling language work to be
scheduled alongside these stages, not prerequisites for every library function.
This is a priority direction, not a dated release commitment.

Validation programs should include configuration decoding with contextual errors,
a directory-processing tool, subprocess interaction, early termination of a
managed stream, and custom codecs for externally owned types. Later include HTTP
with timeouts and tests using fake clocks and deterministic randomness.

Check both compiler implementations, diagnostics, cleanup paths, residual iterator
state, memory retention, and runtime. Preserve runnable examples and exact
commands. Benchmark execution separately from compilation. No successful
prototype alone establishes general fusion, asymptotic, or lifetime guarantees.

## 10. Details still to specify

- Operator grammar, character set, precedence groups and ordering, associativity
  conflicts, newline interactions, alias-target restrictions, and import spelling.
- Export grammar migration, entries that are also imported, and the source-root
  flag. Reconcile existing `hiding` syntax with qualified-by-default imports.
- Collision detection scope, exact-case checking, canonical entry identity, and
  eventual package-qualified type identity.
- The small implicit Prelude and canonical documentation/re-export policy.
- Outer casting versus searching a cause chain. The context/cause API is
  settled (SPEC-DELTAS 69): one explicit chain, `context` wrapping rather
  than repacking, walked with `causeOf`. Whether `cast` inspects only the
  outermost payload or searches the chain, as Go's `errors.As` does, waits
  on checked downcasting -- ERRORS.md step 5.
- Resource acquisition/result types, cleanup failures on control-flow exits,
  cancellation, runtime invalidation, and suspended-builder finalization.
- Applicative/product operations and ergonomic bidirectional builder notation.
- Concrete domain boundaries and exact APIs, validated by complete programs.

## 11. Research and implementation precedents

These sources motivate particular choices; they do not prove that one complete
library organization is universally best.

- [Parnas, On the Criteria To Be Used in Decomposing Systems into Modules](https://www.cs.lafayette.edu/~gexia/cs301/resources/parnas.html):
  information hiding and dependency hierarchy are distinct concerns.
- [Stylos and Myers, The Implications of Method Placement on API Learnability](https://www.cs.cmu.edu/~NatProg/papers/FSE2008-p105-stylos.pdf):
  placement affects discoverability; the study concerns object-oriented APIs,
  so transfer to Turkey's modules is a design inference.
- [A field study of API learning obstacles](https://www.microsoft.com/en-us/research/publication/field-study-api-learning-obstacles/):
  intent, examples, and scenario-oriented documentation matter.
- [Go package names](https://go.dev/blog/package-names) and
  [Swift API Design Guidelines](https://www.swift.org/documentation/api-design-guidelines/):
  evaluate names and boundaries from actual use sites.
- [OCaml operators](https://ocaml.org/docs/operators),
  [Swift custom operators](https://docs.swift.org/swift-book/documentation/the-swift-programming-language/advancedoperators/), and
  [Haskell fixity resolution](https://www.haskell.org/onlinereport/haskell2010/haskellch10.html):
  alternative ways to bound operator syntax and precedence.
- [PEP 533](https://peps.python.org/pep-0533/): deferred proposal documenting
  iterator-cleanup problems; not an adopted Python guarantee.
- [Eio switches](https://ocaml-multicore.github.io/eio/eio/Eio/Switch/):
  resources attached to scopes and reverse-order release.
- [Regex-applicative](https://hackage.haskell.org/package/regex-applicative/docs/Text-Regex-Applicative.html) and
  [Cox, Regular Expression Matching Can Be Simple And Fast](https://swtch.com/~rsc/regexp/regexp1.html):
  typed regular composition and automata-based matching.
- [Rendel and Ostermann, Invertible Syntax Descriptions](https://www.informatik.uni-marburg.de/~rendel/unparse/rendel10invertible.pdf):
  bidirectional composition needs construction and decomposition information.
- [Temporal ambiguity handling](https://tc39.es/proposal-temporal/docs/timezone.html) and
  [Unicode UAX #29](https://unicode.org/reports/tr29/): correctness-oriented domain
  distinctions and explicit policies.
