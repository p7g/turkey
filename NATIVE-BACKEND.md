# The native backend, in Turkey

Status: accepted. Plan item 9, M27 and M28.

## Summary

`boot` owns the path from optimized Core to machine code: one low-level SSA IR,
a small set of optimizations over it, instruction selection, register
allocation, and object emission. Written in Turkey, in `boot/`.

The Python implementation's backend is left as it is. It is a JIT: it will
never select instructions or allocate registers, so hardening its IR to prepare
for those is work on the wrong artifact. It stays the differential oracle for
everything above Core, and takes fixes only.

Three decisions carry the design, and the first two are revisions of an earlier
draft that the prior art contradicted:

1. **Core is the high IR.** It is not "the front end's output" that a proper
   optimizer sits below -- it is already a CFG with block parameters, and the
   optimizations that belong at a high level are already in it. The new IR is a
   *low* IR, and there are two of them in the compiler, not three.
2. **One IR *framework*, two instantiations.** The CFG is parameterized over
   its instruction type, so dominance, liveness, the printer and the generic
   passes are written once and instruction selection produces a different
   instruction type rather than rewriting the same one in place. This is
   neither QBE's single IL nor LLVM's IR pair; it is the thing Turkey makes
   cheap and C does not.
3. **Four optimizations, chosen because someone measured.** Not a menu.

## Prior art, and what it settles

Read before designing, because two of these answer questions this document
otherwise would have guessed at.

**QBE** aims to "provide 70% of the performance of industrial optimizing
compilers in 10% of the code", and says the size limit is the point: it
"constrains QBE to focus on the essential". It is SSA, it uses one IL through
every stage, and instruction selection rewrites that IL in place using a
bottom-up tree-matching algorithm inherited from Ken Thompson's Plan 9 C
compiler. Its optimization set is *copy elimination, sparse conditional
constant propagation, dead instruction elimination, and registerization of
small stack slots*, plus a loop-based spilling heuristic. Its allocator is
linear scan with hinting, and it notes that SSA lets the spiller and the
allocator be separate passes, which is "simpler and faster than graph
coloring".

That is the target shape. It is a complete backend, it is fast, and it is
roughly the size budget this project can carry.

**Cwerg** budgets "10kLOC (target independent code)" and "5kLOC (per target)",
and de-emphasizes code quality -- aiming within 50% of state of the art -- for
a codebase one developer can hold. Its README states a rule this document
adopts outright: *"Sophisticated optimizations in the backend, like loop
optimization. These are best left to the frontend."*

**GHC** is the closest analogue for where an optimization belongs, because it
has the same shape of pipeline. CSE, float-out (which is loop-invariant code
motion), float-in, specialization and the simplifier are all **Core-to-Core**.
The machine level, Cmm, gets *sinking* and *common block elimination* and not
much else. There is also a second CSE at the STG level whose job is to common
up expressions "that differ in their types, but not their representation" --
which is precisely the distinction Turkey's layout sharing is built on, and the
one argument for any CSE below Core at all.

**Cranelift** contributes two lessons rather than a shape, since its budget is
an order of magnitude larger. Its handwritten lowering code ossified: the API
"was ossifying as more and more handwritten backend code came to depend on its
subtle details, making refactors very hard or impossible", which is why
instruction selection became a DSL. And fuzzing "proved incredibly effective"
in moving to a new register allocator "with no serious issues despite the high
complexity". The first says table-driven selection rather than handwritten. The
second says the allocator needs a fuzzer, not a test suite.

**Block parameters over phi nodes** is settled and stays. The choice is where
the binding on a control-flow edge lives -- source block or target block -- and
"more recent compilers instead use block arguments", because phis "are
pseudo-instructions existing as the leading instructions in a basic block, and
you may well need to special-case them in transformation or analysis passes".
`turkey/backend_ir.py` already has this right and it is the one thing carried
across unchanged.

## Core is the high IR

This is the question worth getting right before any code exists, and the answer
is that the overlap is real and the fix is to stop planning a middle layer.

Core already is a control-flow graph. `CJoin(name, params, body, rest)` is a
labelled block with parameters and `CJump(name, args)` is a jump carrying
arguments -- structurally the same thing the SSA IR's blocks are, arrived at
from the functional side rather than the imperative one. `joins.discover` is
the pass that finds them. What Core has that the low IR will not is types,
nesting, closures and constructors; what the low IR has that Core does not is
flat instruction sequences, explicit memory, and representations in place of
types.

So the pipeline is **Core, then one low IR**, and the optimizations divide
along a line that is easy to state:

* **In Core:** anything expressible about *terms* -- inlining, case-of-case,
  case-of-known-constructor, join specialization, dead code, let-floating.
  These are there today. Constant folding, CSE and code motion belong here too
  if they are wanted, and are not there yet.
* **In the low IR:** anything that only exists *after* lowering -- address
  arithmetic, bounds checks, tag tests, spills, the calling convention.

The practical argument for that line, beyond GHC's precedent: **a Core pass is
verified for free.** `.opt` goldens are diffed byte-for-byte against the Python
implementation across the whole corpus, so a CSE written in Core is checked by
machinery that already exists and by a second implementation. A CSE written in
the low IR is checked by running programs and hoping the difference shows.

The one exception is GHC's own: a CSE that commons expressions differing in
type but not in representation cannot be written in Core, because in Core they
have different types. If that turns out to pay, it is a low-IR pass and it is a
different pass from the Core one, not the same pass moved.

## Written in Turkey

Previous milestones ported Python and tried to look like it. This one should
not, and several of Turkey's constraints push toward what modern backends do
anyway.

**Dense integer ids and side tables, not linked objects.** Turkey has no object
identity (FINDINGS 10), so a value cannot be a node you point at -- it is an
index, and everything known about it lives in arrays indexed by it. That is
forced here, and it is also what Cranelift's entity references and Go's
`ssa.Value` ids are. Liveness becomes bitsets over a dense range, and the
register allocator's working sets become arrays rather than hash maps.

**One opcode ADT, matched exhaustively.** Turkey checks a `match` for
exhaustiveness, so adding an opcode is a compile error in the verifier, the
printer, every pass and the selector. This is the single biggest advantage over
porting `backend_ir.py`, where an opcode is a string and the passes that forgot
it are found at runtime or not at all. It has a price, and the price is the
design constraint: the opcode set must stay small, which is an argument for a
QBE-shaped low IR rather than an LLVM-shaped one.

**Records for pass state, mutated in place.** A pass is a record of arrays and
`var` fields, and its body is loops that assign. This is the procedural lean:
the data is ML-shaped, the code is not a fold. `Array` beats `Map Int` wherever
the key is dense, and after newtype erasure and layout sharing an `Array Int`
is machine integers rather than boxes.

**Panic for invariants, `Option` for absence.** A verifier failure is a
compiler bug and should stop the compiler; a lookup that may miss returns
`Option`. Turkey has no exceptions (FINDINGS 17), so there is no third choice
to be tempted by.

**One module per pass, and the IR in its own.** Turkey has no mutually
recursive modules (FINDINGS 31), so the layering is enforced rather than
merely intended: the IR module cannot know about its passes.

**Parametric containers, concrete payloads.** The CFG is generic in its
instruction type and the instruction types are plain ADTs, so the generic
machinery is written once while every `match` on an actual instruction is
direct. There are two instantiations, so `mono` specializes both and the
generality costs nothing at run time.

## Why two instruction types, and one CFG

Raised as an objection to the second draft, and it holds: declaring a datatype
is cheap in Turkey and expensive in C, so a design that copies QBE's economies
is copying a constraint this language does not have.

But the declaration is not what makes a second IR expensive, in any language.
The bill is the machinery around it -- dominance, liveness, loop nesting, CFG
traversal, the printer, the verifier's skeleton, dead-code elimination --
written a second time against a second set of accessors. Beside that, C's
struct boilerplate is a rounding error, which is why QBE and Cwerg avoid the
second IR rather than the second copy of `dominators()`.

Turkey can delete the bill instead of the IR. The CFG is parameterized over its
instruction type:

```
type Block i = Block { params : Array Value, insts : Array i, term : Term }
type Func  i = Func  { blocks : Array (Block i), entry : Int, ... }
```

Terminators stay in the shared part, because they are what the CFG is made of
and both levels branch and jump. Everything an analysis needs of an instruction
-- what it reads, what it defines, what it may do -- is a small class:

```
class Inst i { fun uses(i) -> Array Value
               fun defs(i) -> Array Value
               fun effects(i) -> Effects }
```

Dominance, liveness, loop nesting, the printer skeleton, dead-code elimination
and copy elimination are then written *once*, generic in `i`, and run before
and after instruction selection alike. There are exactly two instantiations, so
`mono` specializes both and none of this is paid for at run time.

**And there is an argument for two instruction types that is specific to this
language, which the second draft missed.** The reason to make an opcode an ADT
rather than a string is that Turkey checks `match` for exhaustiveness, so a new
opcode is a compile error everywhere it must be handled. Rewriting one IL in
place means that ADT has to contain the machine instructions too -- and then
sparse conditional constant propagation, which can never see an `arm64.ldr`,
must either carry an arm for it or a catch-all. The catch-all is exactly what
the ADT was chosen to prevent, and it would be added in every low-level pass on
the first day a target existed. QBE pays nothing for this because C has no
exhaustiveness to lose.

What the split buys, then:

* **A typed phase boundary.** After selection, a virtual opcode is not
  representable. In an in-place scheme the IR holds a mixture partway through
  and nothing checks it.
* **Exhaustiveness that stays meaningful** on both sides of that boundary.
* **Machine-shaped instructions.** Two-address forms, fixed physical registers
  for the calling convention, condition flags and register constraints are
  natural in a machine instruction and are optional-and-usually-meaningless
  fields in a virtual one.
* **Selection becomes testable on its own.** Its output is a value with a
  printer, so it can be golden-tested without a register allocator existing.

What it costs is one more ADT with its `Inst` instance and its printer. That is
the cost the objection correctly identified as small.

## Where this idea comes from, and where else it goes

The parameterized CFG is not a novelty, and the company it keeps is worth
knowing before leaning on it further.

**Hoopl** is the same idea, done first and done in Haskell. Ramsey, Dias and
Peyton Jones, 2010: a dataflow analysis and transformation library whose
`Graph` "is parameterized over both nodes `n` and over its shape at entry and
exit", with "unusually strong static guarantees". It went into GHC as part of a
rewrite of the back end and is what GHC's Cmm analyses are written against.
That is this design's direct precedent: analyses written once, over a node type
the client supplies.

**Nanopass** is the maximal version of the same door. Sarkar, Waddell and
Dybvig; `define-language` states an IR as a grammar and a later one as a
*delta* from it, and `define-pass` writes only the cases that change. Chez
Scheme's compiler is built on it. Where this proposal has two instruction types
and shares the CFG, nanopass has a dozen intermediate languages and shares
everything they have in common, generated.

**And the counterexamples all share a property.** QBE puts virtual and machine
instructions in one C enum and rewrites in place. Go does exactly the same at
much larger scale: one `Op` enum spanning the architecture-independent
operations and every architecture's, with lowering rewriting values in place
from generic ops into `OpAMD64ADDQ` and friends.

Neither language checks a `switch` for exhaustiveness. Neither pays anything
for the mixed enum, because nothing was ever going to tell them a pass had
forgotten a case. The two languages in this list that *do* check --- Haskell,
and Scheme with nanopass's macros --- are the two that parameterize or
generate.

That correlation is the argument. Following QBE here means importing a design
that is free in C and costs Turkey the main thing its type system offers.

## Directions this opens

Ordered by what they buy against what they cost. None is a phase-0 commitment;
they are recorded because the shape chosen now is what makes them available.

**~~Make "register allocated" a type.~~** Withdrawn, and it was listed here
first and highest. The idea was to parameterize over the value type as well as
the instruction -- `Func Virtual MachInst` before allocation, `Func Physical
MachInst` after -- so that a virtual register is unrepresentable in allocated
code. LLVM tracks the same facts as *runtime properties* on `MachineFunction`
(`isSSA`, `NoVRegs`, `TracksLiveness`), checked when someone remembers.

The better answer is that allocation results should not be in the IR at all.
Cranelift moved to exactly that: with regalloc2, `VCode::emit` "is almost
completely immutable, due to keeping regalloc2 results on-the-side and using
the pre-regalloc code plus regalloc results on the fly, rather than editing
in-place as before". If the code always holds virtual values and a table says
where each one lives, there is no phase in which a virtual register is illegal
and nothing to mistype. It also removes a rewrite pass and a third
instantiation.

Fixed registers for the calling convention are then *operand constraints* on
virtual values, which is regalloc2's own design, rather than physical
registers in the instruction stream. So `Value` and `Term` stay monomorphic.

**Generic passes, not just generic analyses.** Dead-code elimination and copy
elimination need only `uses` and `defs`, so they are written once and run
before *and* after selection. Post-selection dead code is then free, where QBE
must write it a second time or skip it. This is the payoff that arrives
earliest and costs nothing extra.

**A second target costs an instruction type.** With the CFG, the analyses, the
allocator and the verifier all generic, what a target adds is its instruction
ADT, its selection table and its encoder. That is what Cwerg's "5kLOC per
target" is buying, and it is bought here by construction rather than by
discipline.

**One fuzzer for everything.** Cranelift's evidence is that fuzzing is what
made a high-complexity allocator transition safe. A random-program generator
over `class Inst i` is generic too: one fuzzer, both instruction types, every
target, and it is the only practical check on the allocator.

**Associated families on the `Inst` class.** A target's register type and
condition-code type are functions of its instruction type, which is what an
associated family is for -- `class Inst i { type Reg i; ... }` -- and is how
this stays a single-parameter class. The project does not add functional
dependencies, and this is the case that would otherwise ask for them.

**Hoopl's other parameter, and why it is not needed.** Hoopl also parameterizes
a block by its *shape* at entry and exit, so that a block which must end in a
terminator cannot fall through; it needs GADTs to do it. Turkey has none, and
does not need them here: `Block.term` is a `Term` and not an `Option Term`, so
the invariant Hoopl encodes in a type index is already enforced by the record
having no way to omit it.

**Where not to take it: Core.** Core has blocks and jumps, so the temptation is
to express it in the same framework and share `joins.discover` and the
dominance analysis. It should be resisted. Core is expression-structured and
typed; the framework is flat and representation-typed, and forcing Core into it
means flattening Core, which is what Core exists not to be.

**The risk worth stating.** The generic machinery is polymorphic code behind a
class, which is precisely what `mono`'s cap declines to specialize past and
what M25's layout sharing exists to compile correctly anyway. The backend will
therefore be the program that stresses that feature hardest -- which is
fitting, and is also a reason to keep the class small and the instantiation
count low.

## The low IR

Values are dense indices carrying a representation. Blocks take parameters,
jumps carry arguments, and SSA is *function-wide*: a definition dominates its
uses. That last part is the one substantive departure from
`turkey/backend_ir.py`, where the rule is block-local and everything crossing
an edge goes through memory -- which makes promoting memory to registers a
precondition for every optimization instead of one of them.

A representation is two facts, not one: a register class (`I1 I8 I32 I64 F64
Ptr`) and whether the collector must trace the value. The Python IR spells this
as `PTR` versus `BOXED`, two members of one enum, which works until something
asks a register class about a `BOXED`.

**A value's representation lives in a side table, and that is forced rather
than chosen.** The alternative is a self-describing value carrying its own
representation, which is what LLVM and Go have -- and both can, because a value
there is a *pointer* to one shared object. Turkey records have reference
semantics and no identity (FINDINGS 10), so such a value would be *copied* at
every mention and two mentions of one value would be two records agreeing only
by convention: an inconsistent state made representable, in order to avoid a
table. It would also cost, since `Value` is a newtype and erases to a machine
word, so the hottest query a backend has answers a packed array of integers
rather than an array of pointers. Cranelift reaches the same shape from the
same constraint, with `Value` an index and `DataFlowGraph::value_type` the
lookup.

What the table costs is ergonomic, and a builder pays it: a value cannot be
made without being given a representation and an instruction cannot be
appended except through a cursor that does, so the table cannot fall out of
step. There is one such table and it is persistent; everything else an analysis
needs -- dominance, liveness, definition sites -- is computed and thrown
away.

Beside the opcode ADT, two functions derived from it by one `match` each:

* `signature(op)` -- operand and result representations, which the verifier
  checks;
* `effects(op)` -- `Pure`, `Reads`, `Writes`, `Allocates`, `Diverges`.

**`Allocates` means may-collect, which means safepoint.** That one bit is what
lets the register allocator emit a stack map -- which registers and spill slots
hold traced values at each safepoint -- instead of rooting every pointer live
anywhere in the function into one array. `Turkey.Opt#expr` carries 481 of those
today (FINDINGS 55). Retrofitting precise stack maps into a backend that did
not plan for them is the rewrite this design exists to avoid, so the bit is
there from the first commit even though nothing reads it until phase 5.

## The optimizations

Four, taken from QBE's set because it is the one that has been measured against
a stated goal:

* **registerization of stack slots** -- mem2reg. The lowering emits slots for
  what is genuinely mutable, and this promotes the rest.
* **sparse conditional constant propagation** -- subsumes constant folding and
  unreachable-block elimination in one pass, and after lowering it is what
  removes bounds checks against known lengths and tag tests on known
  constructors. The *propagation* is the whole of the value, and this is
  measured rather than assumed: across the corpus 8,724 backend instructions
  take a constant operand and only **7** take nothing else, because almost
  every one is a `scalar_eq` against a tag. A folder alone would fire seven
  times. See `CORE-OPT.md`.
* **copy elimination**.
* **dead instruction elimination**.

Block-local common-subexpression elimination is *not* on the list and is the
one candidate worth revisiting once the backend exists: 1,536 instructions in
the corpus repeat an identical earlier instruction within their own block,
which is 0.5% and the first real number on the question. It is left out now
because its cost is register pressure, and the backend is the only place that
can see it.

Plus loop nesting depth, which is not an optimization but is what the spiller
needs to make good decisions, and is cheap once dominance exists.

Not GVN, not LICM, not inlining. Core does the term-level ones and does them
where a golden checks them; the rest are what Cwerg means by leaving loop
optimization to the front end.

## Instruction selection and register allocation

**Selection is table-driven, and rewrites the low IR in place.** Bottom-up tree
matching over the instruction DAG, in the Thompson and QBE line: each node is
numbered, a number identifies which patterns match, and the patterns are data.
Handwritten selection is what ossified in Cranelift; a DSL with a generator is
what they replaced it with, and is more machinery than this budget carries. A
table interpreted at compile time is the middle, and it is what QBE ships.

**Allocation is linear scan with hinting, with the spiller split out**, which
SSA is what makes possible. It runs on the machine instantiation only, and
assigns physical registers to the values a machine instruction already names.

**Stack maps come out of the allocator**, because it is the only pass that
knows where a value is at a given point.

**The allocator gets a fuzzer, not a test suite.** Cranelift's experience is
that this is what made a high-complexity allocator transition safe, and a
register allocator is the one component here whose bugs are both easy to write
and invisible in a conformance run.

## Verification

Every stage so far was verified by byte-identical diffs against the Python
implementation. A backend designed independently has no such oracle, and should
not have one: diffing boot's IR against a JIT's would couple boot's design to
the artifact this decouples from, and every place boot's IR is better would
appear as a diff to suppress.

The observable that matters for a backend is what the compiled program does.

* **Differential execution.** Compile every conformance program with `boot`,
  run it, compare stdout, exit status and panic trace against the Python
  implementation. `tests/programs/*.expected` and `tests/test_system.py` are
  already this, for the other host.
* **The verifier after every pass**, so a miscompile is a rejected program at
  the pass that caused it rather than a wrong answer at the end.
* **A fuzzer for the allocator**, per above.
* **Two emitters over one IR** while LLVM is still there: compile twice from
  the same low IR and compare the runs.
* **M26 is unchanged and gets stronger.** stage2 against stage3 is one Turkey
  program compiled by two hosts.

The loss is real and worth naming: a textual diff localizes a bug to a line,
differential execution to a program. The verifier, the fuzzer and the
two-emitter check are what buy that back.

## Migration

Each phase runs and is verified before the next begins.

* **Phase 0.** `Turkey.Ssa`: the IR, the opcode ADT with `signature` and
  `effects`, the verifier including dominance, the printer.
* **Phase 1.** Core to the low IR. The *logic* of `backend_lower.py` --
  closure conversion, pattern tests, join lowering, the calling convention --
  is correct and hard-won and is what carries across; its IR is not. Split into
  more than one pass; the original is 1,579 lines doing four jobs.
* **Phase 2.** Low IR to LLVM IR text, and `boot build`. The conformance suite
  runs under differential execution. **`boot` is self-sufficient here**, and
  everything above this line is now exercised by every program in the suite.
* **Phase 3.** The four optimizations, one at a time, each measured.
* **Phase 4.** Instruction selection, arm64, table-driven.
* **Phase 5.** Register allocation, stack maps, encoding, object emission.

LLVM is transitional: it is what phase 5 is differentially checked against, so
it outlives the allocator's first working version by however long that takes to
trust, and is then dropped. What it must not become is a constraint -- nothing
in the low IR is shaped to suit it, so the day it goes costs one module.

## Open decisions

* **The first native target.** arm64, because it is what this is developed on
  and a backend that cannot be run is not being tested.
* ~~Whether Core gains CSE and constant folding.~~ Settled by measurement:
  neither fires often enough to build. Folding in Core would fire *zero* times
  across the whole corpus, because the constants are made by lowering and are
  not in the program. See `CORE-OPT.md`.

## Rejected alternatives

### A high IR between Core and the low IR

The earlier draft of this document proposed one, and it was one IR too many.
Core is already a CFG with block parameters and already carries the term-level
optimizations; a second high-level IR would duplicate its structure to hold
optimizations that are better written where a golden checks them.

### Two fully separate IRs, each with its own CFG

The first draft's proposal, and the reason it looked expensive: dominance,
liveness, CFG traversal, the printer and dead-code elimination would all be
written twice. That cost is real and is what the parameterized CFG removes; it
is not an argument against having two *instruction* types.

### QBE's single IL, rewritten in place by the selector

The second draft's proposal, and wrong for a reason particular to this
language. See "Why two instruction types" above: one opcode ADT containing both
virtual and machine instructions makes every low-IR pass carry arms for
opcodes it can never see, and the catch-all that avoids that is the thing an
ADT was chosen to prevent. QBE pays nothing for this because C has no
exhaustiveness to lose.

### Port `turkey/backend_ir.py` and extend it

Block-local SSA makes memory promotion a precondition rather than an
optimization; string opcodes cannot be selected on exhaustively; no effects
model means no precise stack maps. Three retrofits, all in the layer that must
not be rewritten later.

### A DSL and generator for instruction selection

Cranelift's answer, and correct at Cranelift's size. Here it is a second
language, its compiler, and a build step, to replace a table.

### Diff boot's IR against the Python backend's as the oracle

Couples boot's design to a JIT's. Behaviour of the compiled program is the
stronger check and constrains nothing.

### Skip LLVM and go straight to instruction selection

The low IR and its optimizations would have no oracle until an allocator
existed, and every bug in either would present as a miscompile with nothing to
compare against.

### Conservative stack scanning instead of stack maps

Already rejected in `LLVM-BACKEND.md`, for reasons that have not changed.

## Sources

* Hoopl: a modular, reusable library for dataflow analysis and transformation,
  Ramsey, Dias and Peyton Jones, <https://www.cs.tufts.edu/~nr/pubs/hoopl10.pdf>
* A nanopass framework for commercial compiler development, Keep and Dybvig,
  <https://www.cs.tufts.edu/comp/150FP/archive/icfp13.pdf>
* Go's SSA opcodes, generic and per-architecture in one enum,
  <https://pkg.go.dev/cmd/compile/internal/ssa>
* QBE, <https://c9x.me/compile/>
* QBE 1.3, LWN, <https://lwn.net/Articles/1080519/>
* Cwerg backend README,
  <https://github.com/robertmuth/Cwerg/blob/master/BE/README.md>
* GHC optimization guide,
  <https://ghc.gitlab.haskell.org/ghc/doc/users_guide/using-optimisation.html>
* Cranelift's instruction selector DSL,
  <https://cfallin.org/blog/2023/01/20/cranelift-isle/>
* Cranelift, part 4: a new register allocator,
  <https://cfallin.org/blog/2022/06/09/cranelift-regalloc2/>
