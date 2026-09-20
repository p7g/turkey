# Eager monadic Regex library

The implementation is now in [`lib/Regex.gob`](../../lib/Regex.gob).
[`main.gob`](main.gob) is an importing example; it no longer contains a second
engine. No language changes or explicit `build` / `compile` calls are needed.

```kotlin
import Regex as R

type Assignment = Assignment { key : String, value : String }

let word = R.some(R.satisfy(fun(c) = c >= 'a' && c <= 'z'))
let assignment = do {
    let key = R.capture(word)?
    R.literal("=")?
    let value = R.capture(word)?
    pure(R.map2(key, value, fun(k, v) =
        Assignment { key = k, value = v }
    ))
}

R.fullMatch(assignment, "hello=world")
-- Some(Assignment { key = "hello", value = "world" })
```

The calls above illustrate expressions to use in a function. Returning
`pure((key, value))` instead gives `Some(("hello", "world"))`. Pairs (including
nested pairs), triples, capture recipes, and Unit have `Resolve` instances.

## Typed composition

A `Builder r` contains an assembled NFA and a construction-time result `r`.
A `Capture a` is a recipe for obtaining an `a` after a successful match.
`Resolve r` supplies the associated `Output r` type and resolves the recipe.

| Operation | Successful full-match result |
| --- | --- |
| `literal("hello")` | `String` |
| `satisfy(predicate)` | `Char` |
| `capture(pattern)` | The entire matched `String`, discarding the inner recipe |
| `choice(left, right)` | `Either (Output a) (Output b)` |
| `orElse(left, right)` | `Output a`, with both builders having recipe type `a` |
| `optional(pattern)` | `Option (Output a)` |
| `many(pattern)` / `some(pattern)` | `Array (Output a)` |

`fullMatch` wraps those results in an outer `Option`: failure is `None`, while
successful matching of an absent optional result is `Some(None)`.

```kotlin
R.fullMatch(R.choice(R.literal("yes"), R.satisfy(fun(c) = c == 'n')), "n")
-- Some(Right('n'))

R.fullMatch(R.many(R.literal("a")), "aaa")
-- Some(["a", "a", "a"])
```

Repetition supports structured recipes, including tuple results and the
`Assignment` recipe above. Wrap an assignment with a delimiter in an ordinary
`do` block, then apply `some` to obtain an `Array Assignment`. Nested repetitions
produce nested arrays. Each iteration resolves against its own capture subtree;
optional values and branch choices cannot leak from an earlier iteration.

`map` on a Builder runs at construction time; `map` and `map2` on Capture create
result recipes. The current standard `Applicative` supplies only `pure`, so
`Regex.Combining : Applicative` supplies `map2` locally pending TIX-24. No Prelude
change is included. Arbitrary result callbacks run after recognition and cannot
change which input matches; unused and losing-branch recipes are not evaluated.
Callbacks are not memoized: returning a recipe twice may evaluate it twice.
Character predicates run during recognition and should be stable and side-effect
free.

## Matching and empty patterns

Matching is anchored to the entire input and uses Unicode scalar predicates and
UTF-8-safe `String.Index` boundaries. Choice is left-biased among successful
paths, but a left branch that cannot finish the whole pattern does not commit.
Optional participation and repetition are greedy, giving way when required by
the remainder of the pattern.

Epsilon closure visits each instruction at most once per input position, so
nullable loops terminate. `many(literal(""))` yields `[]`;
`some(literal(""))` yields `[""]`, retaining its required first iteration.
Empty loop traversals that revisit a state are discarded. This rule also applies
to nested nullable patterns; there is no enumeration of infinitely many empty
iterations.

## Representation and algorithm

Bind executes its continuation immediately, copies and concatenates the two NFA
instruction arrays, and relocates jump targets. It never runs again at match
time. Falling off the instruction array means acceptance. Reusing a completed
builder across matches does not rebuild or mutate its NFA.

The recognizer follows [Cox's Thompson/Pike VM presentation](https://swtch.com/~rsc/regexp/regexp2.html):
Consume, Jump, Split, and capture-boundary instructions; ordered epsilon closure;
parallel active threads; and first-arrival deduplication at each instruction and
input position. It uses an explicit work stack, not recursive backtracking.

Open and Close instructions append to persistent event histories, so competing
threads share history prefixes without mutating one another. Only the selected
successful history is converted into a capture tree. Choice wraps its selected
branch in a scope; repetition wraps each iteration. Recipes resolve against
those scopes, giving typed sums and arrays without unboxing or unchecked casts.

The tests include 24 ambiguous optional `a` prefixes followed by 24 required
`a`s, and 2,000 repeated captured values. These are correctness/stress checks,
not a throughput benchmark. NFA state processing is bounded by the program size
per input position; histories, capture trees, scans of capture children, and user
result callbacks have additional time and memory costs. Production performance
claims and engine comparisons remain separate design work.

## Current limits

- Full matching only: no pattern-string frontend, search, or replacement API.
- Construction uses a process-wide mutable ID supply. Matching allocates history
  for the current run, not a global-ID-sized capture array. Concurrent construction
  is not addressed.
- Capture ownership is not statically enforced. An out-of-scope handle fails
  with a diagnostic when its recipe is evaluated.
- Sequential reuse of the same captured fragment reuses its IDs: direct capture
  lookup chooses the last occurrence in that scope. Repeated-result recipes gather
  all occurrences of their iteration ID in scope. Use fresh construction for
  distinct handles; choice and repetition introduce their own nested scopes.
- Repeated array copying can make construction quadratic. History and result
  allocation can also be substantial; this is an initial implementation.

## Verification

From the repository root:

```sh
.venv/bin/python -m turkey run --backend python examples/regex/main.gob
.venv/bin/python -m turkey run --backend llvm examples/regex/main.gob
.venv/bin/python -m pytest tests/test_regex.py -q
.venv/bin/python -m pytest tests/test_programs.py tests/test_llvmgen.py -k regex_library -q
```

The conformance program in `tests/programs/regex_library.gob` covers construction
and extraction timing, typed records/tuples/sums/arrays, nesting, optional
participation, priority and fallback, UTF-8, nullable cycles, program reuse, and
long capture histories. It joins the existing bootstrap compiler corpus too.
The Python tests also verify inferred result types and reject capture-as-string,
incorrect result annotations, and access to private constructors.

The initial library change passed all nine focused pytest cases (six type/API
checks plus conformance, native execution, and native code-generation checks),
covering 48 runtime assertions. The bootstrap compiler also produced identical
inferred types and Core for the conformance program; its generated LLVM compiled
and passed the same 48 assertions. The example ran on both backends.
