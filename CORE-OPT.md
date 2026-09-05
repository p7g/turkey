# Core-level optimization: CSE and constant folding

Status: proposal

The design document for optimizations on Core. `opt` today does inlining, beta,
case-of-case, case-of-known-constructor, join specialization, dead-let,
let-floating and join lowering. This adds two, and states where the line
between Core and the backend falls.

## Why these belong in Core

Two reasons, and the second is the one particular to this project.

GHC puts CSE, float-out, float-in, specialization and the simplifier in its
Core-to-Core pipeline, and leaves Cmm -- the machine level -- with sinking and
common block elimination. Cwerg states the same rule from the other side:
"sophisticated optimizations in the backend, like loop optimization... are best
left to the frontend."

And **a Core pass is checked by a second implementation.** `tests/test_boot.py`
diffs `.opt` byte-for-byte between `turkey/` and `boot/` over the whole corpus.
A pass written here is verified by machinery that already exists; the same pass
written in the backend is verified by running programs and hoping a difference
shows. That asymmetry is worth a lot and it only points one way.

## Survey

**GHC does not do full CSE, and its reason does not apply to us.** GHC does
"opportunistic CSE": it discards a duplicate only in the shape
`let x = e in ... let y = e in ...`, which the wiki itself calls "very weak".
The reason is laziness. Full CSE can turn

```
let { x = case e of ...; y = case e of ... } in ...
```

into a shared thunk `let v = e in ...`, converting two strict evaluations into
one lazy one and creating a space leak.

**Turkey is strict, call-by-value.** There are no thunks, so there is no
space-leak class of bug here, and the transformation GHC is afraid of is not
one we can perform. The conservative shape is still the right one, for the
different reason below, but it is a choice rather than a forced move.

**The objection that does apply is register pressure.** Reusing a computed
value means keeping it live, and "an increase in register pressure may trigger
generation of spill code which can more than offset the gains derived from
redundancy elimination" -- worst inside hot loops. Core has no registers, so
this is not decided here; it is *inflicted* here, on a backend whose spiller is
loop-aware by design. That argues for the narrow form: common what is already
in scope, never hoist a computation to a place it was not.

**Constant folding is uncontroversial and is mostly a feeder.** Its direct wins
are small. Its real value is that a folded literal makes
case-of-known-constructor and the inliner fire, and those are the passes that
pay. QBE gets the post-lowering half from sparse conditional constant
propagation, which subsumes folding and unreachable-block elimination in one
pass; that is a backend pass on different input and not a duplicate of this
one.

## Trap 1: arithmetic traps, and the folder must not spring them

`PRIMITIVES.md` 1.1: `add`, `sub`, `mul` and `neg` **panic** when the result
leaves the range, as does `div` at `minInt / -1` and at zero. Trapping was
chosen deliberately -- "silent wraparound is the only place a Turkey program
would keep running with a wrong answer".

So a constant folder has a correctness obligation and a self-preservation one:

* **It must not fold an expression that would panic.** `maxInt + 1` folded to a
  wrapped literal is the exact bug the trapping semantics exist to prevent, and
  it would be introduced by the compiler rather than written by the programmer.
  Leaving it unfolded is always safe: the panic then happens where it would
  have.
* **It must not fold to an eager panic either.** The expression may sit in a
  branch that never runs.
* **And the folder itself must not overflow while folding.** This is where the
  two implementations differ and where a shared design has to be careful:
  Python's integers are arbitrary precision, so `turkey/` computing `a + b`
  gets a value no Turkey program can hold, while `boot/` computing the same
  thing in a Turkey `Int` **panics the compiler**. Both must detect the
  overflow rather than perform it -- range-check the operands before the
  operation, or use the wrapping primitives and check the sign, which is what
  `Data.Int.addChecked` already does.

The safe rule is therefore: fold only when the result is provably in range, and
otherwise leave the term alone.

## Trap 2: Core has no purity notion, and CSE needs one

`opt._is_value` looks like the predicate to reach for and is not. It asks
whether evaluating a term "does no work and can be done twice or never" -- a
question about *cost*, answered by a literal, a variable, a lambda, a
constructor application. CSE asks a different question: whether two occurrences
of the same expression compute the same thing.

Core is strict and has mutation. `CAssign`, `CRef`/`CDeref`, a field read of a
mutable record, an array read, and any call all bear on this, and none of them
is covered by `_is_value`. So the pass needs a small effects analysis: an
expression is a candidate if it reads nothing mutable, or if nothing writes
between the two occurrences.

This is the same question the low IR needs `effects(op)` for. It is worth
noticing that CSE is what forces it to exist at *both* levels, and worth
resisting the urge to share one implementation across them -- the Core notion is
about terms and the backend's is about opcodes.

## Trap 3: a panicking expression can be commoned, but never hoisted

`xs[i]` is pure and may panic. Two occurrences may be commoned **if the first
dominates the second**: if the first panicked, the second never ran, so
replacing it changes nothing. The same expression must never be *hoisted* to a
point that does not already evaluate it -- that would make a program panic that
did not.

The narrow form is the one that is safe by construction, and it happens to be
the one the register-pressure argument also wants: replace a later occurrence
with a reference to an earlier binding already in scope, and move nothing.

## Design

**Constant folding.** Literal arithmetic, comparison and boolean operators,
folded only when the result is in range, computed through a checked path in
both implementations. Not `if` on a literal condition -- `Bool` is a
constructor and `known_constructor` already collapses that.

**CSE.** GHC's opportunistic shape, for our own reasons: within a binding,
maintain a map from an expression's key to the name already bound to it;
replace a later occurrence with that name when the binding dominates it and
nothing has written to anything the expression reads in between. Invalidate the
whole map at a write whose target the analysis cannot rule out.

Both run inside `opt`'s existing fixed point, so a fold feeds
case-of-known-constructor and CSE feeds dead-let, without a new pass ordering
to reason about.

## Cost

Two implementations plus a golden regeneration -- `turkey/opt.py` and
`boot/Turkey/Opt.tl` -- and `test_boot` is red until both agree. That is the
tax the differential charges and the reason it is worth paying.

## Sources

* GHC performance notes on opportunistic CSE and the space-leak reason,
  <https://wiki.haskell.org/Performance/GHC>
* GHC optimisation guide,
  <https://ghc.gitlab.haskell.org/ghc/doc/users_guide/using-optimisation.html>
* Register-pressure-sensitive redundancy elimination,
  <https://link.springer.com/chapter/10.1007/978-3-540-49051-7_8>
* Redundancy elimination notes, Cornell CS4120,
  <https://www.cs.cornell.edu/courses/cs4120/2023sp/notes.html?id=redund_elim>
* Cwerg backend README,
  <https://github.com/robertmuth/Cwerg/blob/master/BE/README.md>
* `PRIMITIVES.md` 1.1, overflow traps.
