# Eager monadic regex prototype

This is a runnable Turkey experiment for TIX-26, not the public Regex API.
It needs no language changes, `build`, or `compile` operation.

```kotlin
let assignment = do {
    let key = capture(word)?
    literal("=")?
    let value = capture(word)?
    pure(map2(key, value, fun(k, v) =
        Assignment { key = k, value = v }
    ))
}

fullMatch(assignment, "hello=world")
-- Some(Assignment { key = "hello", value = "world" })
```

Returning `pure((key, value))` instead produces `Some(("hello", "world"))`.
The actual example and checks are in `main.gob`.

## What the experiment establishes

`Builder a` contains an assembled NFA instruction array and a construction-time
value. Its `bind` immediately calls the continuation with that value, then copies
and concatenates the fragments, relocating jump targets. Falling off the array
means acceptance, so concatenation connects one fragment to the next without a
final compilation pass. Construction does not need the subject string.

`Capture a` is an extraction recipe. `map2` combines recipes without changing the
NFA. The existing `Applicative` has only `pure`, so this experiment puts `map2` in
a small `Combining : Applicative` class. Moving that method into Applicative is
separate standard-library work; the experiment does not modify the Prelude.

`Resolve` has an associated `Output` family and instances for captures, pairs,
and Unit. Pairs resolve recursively. Thus `fullMatch` returns
`Option (Output r)` from `Builder r`, using existing Turkey type-class machinery.
The extraction callback runs only after a successful full match.

## Algorithm basis and scope

The VM follows [Cox's Thompson/Pike VM presentation](https://swtch.com/~rsc/regexp/regexp2.html):
Consume, Jump, Split, and Save instructions; ordered epsilon closure; parallel
active threads; and first-arrival deduplication at each instruction and input
position. Save instructions retain capture boundaries per path. The matching
loop has no recursive backtracking. Epsilon closure is recursive and marks
visited instructions before following edges, including empty cycles.

This experiment exercises the ambiguous `a?` repeated 24 times followed by `a`
repeated 24 times on 24 input characters. It is a correctness smoke check, not a
performance measurement or a claim about production throughput. Copying capture
histories has additional cost beyond boolean NFA simulation. Engine selection
and a comparative performance survey remain work for the eventual design.

## Deliberate limitations

- Full matching only. No pattern-string parser or search API.
- Capture IDs come from a process-wide mutable counter; matching allocates slots
  for all IDs allocated so far. This is convenient for the experiment, not a
  production allocation or concurrency design.
- Capture ownership and branch participation are unchecked. Missing slots are
  initialized to the input start; foreign handles can give meaningless results.
- Choice and repetition accept only `Builder Unit`. Capture around these works;
  optional typed results and arrays of per-iteration captures are not implemented.
- Reusing an already captured fragment reuses its capture IDs. Independent
  occurrences should use fresh `capture` calls. Uncaptured `word` fragments are
  safely reusable, and constructing later patterns preserves earlier handles.
- Fragment copying can make repeated concatenation quadratic during construction.
  Matching does not modify the assembled program or rerun builder continuations.
- Capture recipes are closures; no guarantee of purity is enforced. The tests
  deliberately use counters to observe construction and extraction timing.

## Verification

From the repository root:

```sh
.venv/bin/python -m turkey run --backend python prototypes/regex/main.gob
.venv/bin/python -m turkey run --backend llvm prototypes/regex/main.gob
```

Both backends pass the 14 checks: eager construction, typed Assignment results,
program reuse, failure cases, callback timing, tuple and nested tuple resolution,
UTF-8 boundaries, nullable repetition, alternative fallback, greedy captures,
capture stability, and ambiguous optional prefixes.

Two temporary negative variants were also type-checked and rejected: assigning
a construction-time `Capture String` to `String`, and assigning the matched
`Assignment` to `String`.
