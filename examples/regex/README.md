# Monadic Regex library

The implementation is in [`lib/Regex.gob`](../../lib/Regex.gob), and
[`main.gob`](main.gob) is an example that imports it. A pattern is described
with combinators, compiled once with `compile`, and matched against whole
strings with `fullMatch`.

```kotlin
import Regex as R

type Assignment = Assignment { key : String, value : String }

let word = R.some(R.satisfy(fun(c) = c >= 'a' && c <= 'z'))
let assignment = R.compile(do {
    let key = R.capture(word)?
    R.literal("=")?
    let value = R.capture(word)?
    pure(R.map2(key, value, fun(k, v) =
        Assignment { key = k, value = v }
    ))
})

R.fullMatch(assignment, "hello=world")
-- Some(Assignment { key = "hello", value = "world" })
```

The calls above illustrate expressions to use in a function. Returning
`pure((key, value))` instead gives `Some(("hello", "world"))`. Pairs (including
nested pairs), triples, capture recipes, and Unit have `Resolve` instances.

## Typed composition

A `Builder r` describes a pattern whose result recipe is an `r`. A
`Capture a` is a recipe for obtaining an `a` after a successful match.
`Resolve r` supplies the associated `Output r` type and resolves the recipe.
`compile` turns a `Builder r` into a `Pattern r`, which is what `fullMatch`
runs.

| Operation | Successful full-match result |
| --- | --- |
| `literal("hello")` | `String` |
| `satisfy(predicate)` | `Char` |
| `capture(pattern)` | The entire matched `String`, discarding the inner recipe |
| `choice(left, right)` | `Either (Output a) (Output b)` |
| `orElse(left, right)` | `Output a`, with both builders having recipe type `a` |
| `optional(pattern)` | `Option (Output a)` |
| `many(pattern)` / `some(pattern)` | `Vec (Output a)` |

`fullMatch` wraps those results in an outer `Option`: failure is `None`, while
successful matching of an absent optional result is `Some(None)`.

```kotlin
R.fullMatch(R.compile(R.choice(R.literal("yes"), R.satisfy(fun(c) = c == 'n'))), "n")
-- Some(Right('n'))

R.fullMatch(R.compile(R.many(R.literal("a"))), "aaa")
-- Some(["a", "a", "a"])
```

Repetition supports structured recipes, including tuple results and the
`Assignment` recipe above. Wrap an assignment with a delimiter in an ordinary
`do` block, then apply `some` to obtain a `Vec Assignment`. Nested repetitions
produce nested arrays. Each iteration resolves against its own capture subtree;
optional values and branch choices cannot leak from an earlier iteration.

A builder is a description: it runs when it is compiled, once for every place
it is used in the pattern. So a fragment can appear twice in one pattern, and
each use resolves to its own match:

```kotlin
let token = R.capture(word)
R.fullMatch(R.compile(do {
    let a = token?
    R.literal("=")?
    let b = token?
    pure((a, b))
}), "ab=cd")
-- Some(("ab", "cd"))
```

`map` on a Builder runs when the pattern is compiled; `map` and `map2` on
Capture create result recipes. The standard `Applicative` supplies only
`pure`, so `Regex.Combining : Applicative` supplies `map2`. Result callbacks run
after recognition and cannot change which input matches; unused and
losing-branch recipes are not evaluated. Callbacks are not memoized: returning
a recipe twice may evaluate it twice. Character predicates run during
recognition and should be stable and side-effect free. Code in a `do` block
that builds a pattern runs once per use of that block in a compiled pattern,
and once more for each `compile`.

## Matching and empty patterns

Matching is anchored to the entire input and uses Unicode scalar predicates and
UTF-8-safe `String.Index` boundaries. Choice is left-biased among successful
paths, but a left branch that cannot finish the whole pattern does not commit.
Optional participation and repetition are greedy, giving way when required by
the remainder of the pattern.

Each instruction is visited at most once per input position, so nullable loops
terminate. `many(literal(""))` yields `[]`; `some(literal(""))` yields `[""]`,
retaining its required first iteration. Empty loop traversals that revisit a
state are discarded. This rule also applies to nested nullable patterns; there
is no enumeration of infinitely many empty iterations.

## Representation and algorithm

`compile` runs the builder against an assembly that holds the instructions at
their final positions and a tag counter belonging to that one pattern:
Consume, Jump, Split, and the Open and Close boundaries of capture scopes.
Falling off the end of the instructions means acceptance. Jumps and splits are
then pointed past any chain of jumps they lead to. A `literal` has no scope,
since its text is known, and nothing inside a `capture` has one either, since
the capture discards the inner recipe.

`fullMatch` runs one of two engines, and they give the same results:

- When `(instructions + 1) * (bytes of text + 1)` is within a budget, 256Ki
  pairs by default, a depth-first backtracker with a visited bit for each pair
  of instruction and position. It tries the first branch of every split
  before the second, so the first path it finds is the one the Pike VM would
  choose, and since a pair is explored at most once, the work is bounded by
  the number of pairs. Capture boundaries go to one event log, cut back on
  backtracking. This follows RE2's BitState and Go's backtracker.
- Otherwise the [Pike VM](https://swtch.com/~rsc/regexp/regexp2.html): ordered
  epsilon closure, parallel threads, and first-arrival deduplication at each
  instruction and input position. Threads carry persistent event histories
  that share prefixes, and only the winner's is turned into a log.

`fullMatchWithin(pattern, text, budget)` chooses with another budget; a
budget of 0 always runs the Pike VM, whose space is linear in the pattern
alone.

The winning log becomes a capture tree. Choice wraps its selected branch in a
scope; repetition wraps each iteration. Recipes resolve against those scopes,
giving typed sums and arrays without unboxing or unchecked casts.

## Current limits

- Full matching only: no pattern-string frontend, search, or replacement API.
- Character classes are predicates, so the compiler cannot tell whether two
  alternatives can start with the same character. There is no one-pass
  engine, and no DFA.
- Capture ownership is not statically enforced. A handle used outside the
  scope it was built in fails with a diagnostic when its recipe is evaluated.

## Verification

From the repository root:

```sh
build/stages/stage2 build -o main.s examples/regex/main.gob && cc -o main main.s && ./main
python3 -m pytest tests/test_regex.py -q
sh scripts/test.sh tests/programs/regex_library
```

The conformance program in `tests/programs/regex_library.gob` covers when
builders run and recipes are extracted, typed records, tuples, sums and arrays,
nesting, optional participation, priority and fallback, UTF-8, nullable
cycles, reuse of compiled patterns and of fragments, and long capture
histories. It runs every case twice, once with room to backtrack and once on
the Pike VM, and the two halves of its output are the same.
