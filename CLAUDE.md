# Working practices

## Survey the prior art before implementing anything hard

Before writing a pass, an IR, an algorithm or a design that will be expensive
to change, find out what other implementations did and what they measured. Do
it *before* the code exists, not after it is 10k lines old.

This is cheap and it keeps paying. The native backend design was surveyed
before a line was written and the survey reversed two decisions:

* it proposed a high IR between Core and machine code, until GHC's split --
  CSE and float-out are Core-to-Core, Cmm gets sinking -- showed that Core
  already *is* the high IR, and that a pass written in Core is checked by
  goldens against a second implementation while one written below it is not;
* it proposed a separate machine IR on the argument that every backend
  converges on two, which is true only of backends an order of magnitude
  larger. QBE selects instructions by rewriting one IL in place, at 70% of
  industrial performance in 10% of the code.

And the survey produced an argument nobody would have reached by thinking about
Turkey alone: QBE and Go both put virtual and machine instructions in one enum,
and *neither language checks a switch for exhaustiveness*. The two languages in
the comparison that do check -- Haskell with Hoopl, Scheme with nanopass --
both parameterize or generate instead. That correlation is why this project
parameterizes, and it came from reading rather than from reasoning.

**What a survey has to contain.** Not a reading list. For each peer: what they
built, what they *measured*, and what their budget was. Counterexamples matter
more than confirmations -- find the projects that chose differently and say why
their situation differs, because if you cannot, they may simply be right.
Quote the numbers; "QBE aims for 70% of the performance in 10% of the code" is
a design constraint and "QBE is small" is not.

**Where it goes.** In the design document that owns the decision, as a section,
with links. If there is no such document the survey is the argument for writing
one. A survey that lives only in a conversation has not been done.

**What does not need one.** Routine work: a bug fix, a port of an algorithm
already agreed, a refactor with a test suite behind it. The trigger is *novel
design that is expensive to reverse*.

## Two implementations, and what that costs

`turkey/` is Python and `boot/` is Turkey, and `tests/test_boot.py` diffs every
stage between them byte-for-byte over the whole corpus. So a change to a shared
algorithm is a change to *both*, or the differential goes red.

That is a feature and it is the project's main correctness property, but budget
for it: a new Core pass is two implementations plus a golden regeneration, not
one pass. It is also why a pass belongs in Core when it can be -- Core is where
the oracle reaches.

The failure this permits is worth knowing: `test_boot` compares *output*, so a
stage that crashes is a stage the oracle says nothing about, and a fix to a
shared algorithm has no test that notices it was applied to only one side. See
FINDINGS 43.

## Documents

* `design.md` -- the language.
* `PRIMITIVES.md` -- primitive types and their semantics.
* `SPEC-DELTAS.md` -- numbered decisions that changed the spec.
* `plan.txt` -- the roadmap and its milestones.
* `FINDINGS.md` -- what writing the compiler in the language turns up. Keeping
  it as work proceeds is the point; the interesting part of a papercut is the
  moment it bites and what was being written at the time.
* `LLVM-BACKEND.md`, `NATIVE-BACKEND.md` -- backend designs.
* `CORE-OPT.md` -- optimizations on Core, and where the line to the backend is.
