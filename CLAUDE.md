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

## Keep the language reference true

`docs/ref/` is the language as its users see it. After any change a program
could notice -- syntax, a type rule, a diagnostic the reference quotes, a
panic, what the Prelude provides -- update the chapters it touches in the same
commit, and fix any sentence a bug fix has made wrong. `tests/test_reference.py`
compiles and runs every example, so a stale example goes red; stale prose does
not, so grep the chapters for the feature you changed.

How the reference is written:

* **What the compiler does today, and nothing else.** No compiler internals
  (Core, dictionaries, passes, layouts), no planned or reserved features, no
  citations of SPEC-DELTAS. Library only where the language leans on it
  (`builtins.md`). Check a claim by running it, not by reading `design.md`.
* **Each section:** a small **Syntax** block, the rules in plain prose, then
  examples. Sugar is explained by an approximately equivalent desugaring into
  plain Turkey, with `$`-prefixed hidden names.
* **Two audiences.** Use the real terminology (principal type, value
  restriction, existential type) with a short "For readers new to this" note,
  and add "Coming from Rust" / "Coming from Haskell" notes where those readers
  would guess wrong.
* **Idiomatic examples.** Small realistic programs, one idea each: `let` or a
  parameter pattern rather than a one-armed `match`, `for ... in` rather than
  an index loop, `[]` rather than `Array.new`, annotations only where they
  explain an interface. Every `kotlin` fence carries a directive (`run`,
  `check`, `error: TEXT`, `panic: TEXT`, `module: F.gob`); see
  `docs/ref/README.md`.

## Documents

* `docs/ref/` -- the language reference: syntax and semantics for people
  writing Turkey, every example compiled and run by `tests/test_reference.py`.
* `design.md` -- the original language design, with its rationale.
* `PRIMITIVES.md` -- primitive types and their semantics.
* `SPEC-DELTAS.md` -- numbered decisions that changed the spec.
* `plan.txt` -- the roadmap and its milestones.
* `FINDINGS.md` -- what writing the compiler in the language turns up. Keeping
  it as work proceeds is the point; the interesting part of a papercut is the
  moment it bites and what was being written at the time.
* `LLVM-BACKEND.md`, `NATIVE-BACKEND.md` -- backend designs.
* `BOOTSTRAP.md` -- building `boot` from the committed bootstrap, and when to bump it.
* `LINKER.md` -- what producing an executable without `cc` would cost.
* `RUNTIME-IN-TURKEY.md` -- what replacing the C runtime would cost.
* `CORE-OPT.md` -- optimizations on Core, and where the line to the backend is.
* `STDLIB.md` -- what the standard library needs, and how it is named.
* `ERRORS.md` -- error handling, existential constructors, and why not GADTs yet.
* `LIBRARY-DESIGN.md` -- the agreed library direction: modules, imports, exports, operators.
* `PROPOSALS.md` -- language changes argued before they are built; nothing there is decided.
