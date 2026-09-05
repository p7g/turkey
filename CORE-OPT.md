# Core-level optimization: CSE and constant folding

Status: **surveyed, measured, and declined.** Neither pass is built. The
analysis is kept because it is what would otherwise be redone, and because the
measurement moved a decision in the backend.

The design document for optimizations on Core. `opt` today does inlining, beta,
case-of-case, case-of-known-constructor, join specialization, dead-let,
let-floating and join lowering. This considered adding two.

## Why they would belong in Core, if they belonged anywhere

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

## Measured, before writing either

The survey said both passes were sound and where they belonged. It did not say
whether they would ever fire, and a constant folder in Core carries a cost the
survey surfaced: `boot/Turkey/Prims.tl` holds primitive *names* and says so
deliberately -- "what one *means* is the Python's business until the C runtime
arrives". Folding would be the first thing to need semantics there, so it is
worth knowing what it buys before paying.

Counted over `tests/programs` and `boot/Main.tl` -- the whole corpus, including
the compiler compiling itself -- on the program `opt` produces:

| | Core | backend IR |
| --- | ---: | ---: |
| operations with operands | 3,213 | 301,622 |
| with at least one literal operand | **0** | 8,724 |
| with *every* operand literal | **0** | 7 |
| repeated identical pure expression | 32 | 1,536 |

**Constant folding in Core would fire zero times.** Not rarely: never. Of 3,213
primitive applications in the optimized Core of every program in the corpus,
not one has even a single literal argument. In hindsight the reason is
ordinary -- a programmer writes the constant already folded, and what inlining
substitutes is arguments, which are variables.

**CSE in Core would fire 32 times**, across 384,095 nodes, and 31 of those are
tuple projections. That is 0.008%, for a pass needing an effects analysis Core
does not have.

Neither is worth two implementations and a golden regeneration. Neither is
built.

## What the measurement says about the backend

The interesting half is the other column, because it is the Core-to-backend
line drawn by counting rather than by citing GHC.

**The constants are created by lowering.** Zero operations in Core take a
literal operand and 8,724 in the backend IR do. Address arithmetic, tag tests
and bounds checks are made during lowering out of numbers that were not in the
program, which is exactly why a folder pays below Core and not in it.

**But plain folding would fire 7 times even there.** Almost every constant
appears in a `scalar_eq` -- a tag test -- with one constant operand and one
variable, which is not foldable on its own. It is foldable once something has
*propagated* the tag a preceding construction wrote.

That is the difference between constant folding and what QBE actually lists,
which is *sparse conditional constant propagation*. The value is in the
propagation; the folding is the part that is worthless alone. The backend
should implement SCCP and should not implement a folder, and this is now a
measurement rather than a borrowed opinion.

**And block-local CSE would fire 1,536 times**, which is 0.5% of instructions
and the first real number on the question. Worth revisiting when the backend
exists, against a live-range cost the backend can actually see; not worth
guessing at now.

## And constant propagation? Core already does it

The obvious follow-up, since the measurement above says the value is in the
propagation rather than in the folding. The answer is that Core has the
propagation already, under other names:

* `trivial_let` substitutes a `let`-bound `CLit`, `CCon`, `CVar` or `CUnit`
  into the body. That is constant and copy propagation, and it runs inside the
  same fixed point as everything else, so a constant it exposes is immediately
  offered to the rules below.
* `known_constructor` collapses a `match` whose scrutinee is a known
  constructor. That is the *conditional* half -- the part that makes sparse
  conditional constant propagation stronger than plain propagation -- for the
  only kind of branch Core has.
* `specialize_join` copies a join per constructor signature of its jumps, which
  is propagation across the one place Core joins control flow.

What SCCP would add over that is the phi case for *literals*: a join parameter
that every jump supplies the same constant for, which none of the three
handles. Counted over the corpus:

| | |
| --- | ---: |
| joins | 8,324 |
| join parameters | 5,283 |
| parameters every jump gives the same constant | **2** |
| ...of those, used as a primitive's argument | **0** |

Two, and neither of them enables anything downstream. There is no pass here.

The other direction a constant can travel is into a call, which is a different
pass again -- specializing a body on a constant argument, GHC's SpecConstr
territory, and something `mono` does not do because it specializes by type and
never by value. 934 of 6,362 calls to a top-level binding pass at least one
constant argument, but only **19 argument positions** are always the same
constant across every call site, which is what a specializer would need. Small,
and it trades code size for it. Recorded rather than pursued.

The conclusion is the same as the section above from the other side: Core is
not missing constant propagation. The backend is missing the *things to
propagate to*, and lowering is what creates them.

## What this costs and saves

Not writing them saves two passes in two implementations, a golden
regeneration, and the semantics module `Prims.tl` has so far not needed. It
keeps `boot` ignorant of what a primitive *means* until the native backend
makes that unavoidable, which is the point at which it has to know anyway.

The passes stay described here rather than deleted, because the analysis is
what would otherwise be redone: if a future program is constant-heavy in a way
this corpus is not, the traps above are the ones a folder still has to avoid.

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
