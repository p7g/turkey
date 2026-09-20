# Working practices

## Where what you write goes

Six places, and nothing else. A document nobody tests goes stale without anyone
noticing, and a stale document is worse than none, because the next reader
takes it for fact.

* **What the language is:** `docs/ref/`. Every example is compiled and run by
  `tests/test_reference.py`, which is why it stays.
* **Why this code is the way it is:** a comment in the module it is about,
  edited in the same commit as the code. See "Comments" below.
* **Why a change was made:** the commit message. It is dated, so it cannot go
  stale.
* **Work not done yet:** a tix ticket.
* **Something rejected, and why:** a *closed* tix ticket, searchable the next
  time someone proposes it.
* **How to build and run the thing:** `README.md`.

**Do not add a Markdown file to the repository root.** `README.md` and this
file are the two that belong there. If the thing you want to write is a design
you are about to build, it is a ticket; if it is a design you have built, it is
a comment; if it is what happened, it is the commit message.

The other root documents -- `design.md`, `SPEC-DELTAS.md`, `PRIMITIVES.md`,
`plan.txt`, `FINDINGS.md`, the backend and library designs -- are being retired
into those six places (TIX-97). Until they are gone: do not add to them, and do
not trust a claim in one without checking it against the code. Several have
already been found describing a compiler that no longer exists.

## Survey the prior art before implementing anything hard

Before writing a pass, an IR, an algorithm or a design that will be expensive
to change, find out what other implementations did and what they measured. Do
it *before* the code exists, not after it is 10k lines old.

This is cheap and it keeps paying. The native backend design was surveyed
before a line was written and the survey reversed two decisions:

* it proposed a high IR between Core and machine code, until GHC's split --
  CSE and float-out are Core-to-Core, Cmm gets sinking -- showed that Core
  already *is* the high IR;
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

**Where it goes: in the ticket that makes the decision**, as part of its body,
with links, before the code exists. The ticket is then the record -- open while
the work is pending, closed and searchable once it is done or declined. What
the code needs to keep afterwards is the *invariant* the survey settled on, as
a comment where it binds, not the argument that produced it. A survey that
lives only in a conversation has not been done.

**What does not need one.** Routine work: a bug fix, a port of an algorithm
already agreed, a refactor with a test suite behind it. The trigger is *novel
design that is expensive to reverse*.

## One implementation, and what checks it

`boot/` is the compiler and it is written in Turkey. The Python implementation
it was diffed against is gone, so there is no second answer to compare against,
and a change is checked by what it *does*:

* **Recorded behavior.** `tests/programs/` holds a program, its exact output
  and its exit status. `tests/lang.py` compiles and runs through `boot`, and
  every behavioral test goes through it. Regenerate a golden with
  `python3 -m tests.regenerate_expected` and **read the diff**: nothing else
  says whether a change is a fix or a regression.
* **The reference.** `tests/test_reference.py` compiles and runs every example
  in `docs/ref/`, so the chapters are executable.
* **The fixed point.** `boot` compiles itself, and `pytest -m bootstrap` checks
  that the compiler it builds emits the same bytes it was built from. A
  miscompile has to reproduce itself exactly to survive.

Budget for the goldens: a change that moves output moves the recorded files
too, and regenerating them without reading them is how a regression gets
committed.

`boot` itself is built from the arm64 assembly committed in `bootstrap/`, by
`tools/build.sh`, with a C compiler and no Python. `tools/bump-bootstrap.sh`
replaces that committed compiler, which is a deliberate act and its own commit:
both scripts say when and why at the top.

## Keep the language reference true

`docs/ref/` is the language as its users see it. After any change a program
could notice -- syntax, a type rule, a diagnostic the reference quotes, a
panic, what the Prelude provides -- update the chapters it touches in the same
commit, and fix any sentence a bug fix has made wrong. `tests/test_reference.py`
compiles and runs every example, so a stale example goes red; stale prose does
not, so grep the chapters for the feature you changed.

How the reference is written:

* **What the compiler does today, and nothing else.** No compiler internals
  (Core, dictionaries, passes, layouts), and no planned or reserved features.
  Library only where the language leans on it (`builtins.md`). Check a claim by
  running it.
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

## Comments

A comment describes the code **as it is now**: what it does, and why, where the
why is not obvious. The reader has this file and the code in front of them, and
nothing else.

* **No references to documents or tickets.** No `FINDINGS 43`, no
  `SPEC-DELTAS 65`, no `design.md`, no `TIX-92`, no milestone numbers. If the
  reason matters, write the reason. A rule that comes from the spec is stated
  as the rule, not cited.
* **No history.** Not "used to", "previously", "now", "since the rewrite", "the
  old version". A change belongs in its commit message, where it is dated.
* **No comparisons with other implementations**, the retired Python compiler
  above all. A choice explained by what something else did explains nothing
  once that something is gone; write what makes the choice right here.
* **Nothing that narrates the next line.** `-- increment i` earns nothing.
* **An invariant, a trap, or a reason is exactly what a comment is for.** That
  the collector does not move objects and the allocator interface relies on it;
  that this scan must run before that one and why; that a plausible-looking
  simplification is wrong for a stated reason. Keep those, and make them
  precise enough to check.

The test: would this comment still be true and useful to someone reading only
this file a year from now? If it is about the past, it belongs in a commit
message. If it is about work not done, it belongs in a ticket.

## Tickets

Work is tracked in tix. Bugs and disagreements found in passing become
tickets rather than notes in a document or a message -- dedupe against the open
list first. A ticket carries what is needed to act on it without the
conversation that produced it: a repro, the files involved, the decision it
needs, and what "done" means.

## Documents

* `README.md` -- what Turkey is, and how to build and run it.
* `docs/ref/` -- the language reference: syntax and semantics for people
  writing Turkey, every example compiled and run by `tests/test_reference.py`.
* `boot/CLAUDE.md` -- house style for the compiler's own source.

Everything else at the root is on its way out; see "Where what you write goes".
