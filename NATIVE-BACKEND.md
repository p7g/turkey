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
small stack slots*, plus a loop-based spilling heuristic. It notes that SSA
lets the spiller and the allocator be separate passes, which is "simpler and
faster than graph coloring" -- a claim that is half right, and the half that is
wrong is corrected under *Register allocation, surveyed* below, along with what
its allocator turns out actually to be.

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

* **registerization of stack slots** -- implemented by `Turkey.Promote`.
  `SsaLower` replaces non-escaping cells and record fields with private local
  slots. Backward slot liveness, block parameters and load/store elimination
  promote those slots before either LLVM emission or native selection. Captured
  or escaping objects retain heap storage. See FINDINGS 81.
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

**Allocation is spill, then colour, then coalesce** -- the SSA decomposition,
not linear scan and not IRC. It runs on the machine instantiation only, and
assigns physical registers to the values a machine instruction already names.

The survey below settles the algorithm, and the deciding fact is one sentence
of Saarland's: *"The dominance relation of the SSA-form program induces a
perfect elimination order on the program's interference graph"*, and therefore
**"the interference graph does not have to be constructed as a data
structure."** Colouring is a walk of the dominator tree giving each definition
a register no live value holds. There is no graph, no worklist, and no
iteration.

**Why not IRC, given WebKit's numbers.** Air is not SSA, and IRC's shape
follows from that: build the interference graph, then simplify, coalesce,
freeze, spill and select -- and when a potential spill becomes a real one,
insert the spill code and *run the whole thing again*. That loop is where its
time and its subtle bugs are. On SSA it is unnecessary, because maximum
register pressure is not merely a lower bound on the registers needed, it is
exactly the answer: chordal graphs have ω(G) = χ(G). So spilling until
pressure fits is **sufficient**, colouring afterwards **cannot fail**, and the
decoupling costs nothing in quality. In non-SSA that is false -- pressure ≤ K
does not imply colourable -- which is the whole reason IRC iterates.

What WebKit's measurement is still worth is the correction it makes to QBE's
framing: colouring is not the option you take when compile time does not
matter. It is 1,300 lines and at parity with LLVM. That kills the axis this
document was reasoning on; it does not make Air's allocator the one to copy.

**Critical edges are a non-problem here, structurally.** Go requires their
absence so it can "add fixup code to the end of that block", and every
allocator that shuffles registers at a merge needs the same. Two properties
remove the question. `Term` gives arguments to `Jump` and not to `Branch`, so
an edge out of a multiple-successor block carries no bindings *by type* and not
by discipline -- selection's guard splitting cannot violate it. And
dominance-order colouring gives each value **one register for its whole live
range**, so no value needs shuffling on any edge. Only block parameters need
copies, and only `Jump` carries those.

**Where the quality actually comes from, and what to expect.** Not from the
colourer, which is optimal in register count by the theorem above. From two
heuristics and one absence:

* **Spilling** is the dominant term and is a heuristic choice: Belady's
  furthest-next-use, which is what Go does, or cost times loop depth, which is
  what QBE does.
* **Coalescing** is our weakest point and should be expected to be. Selection
  emits move traffic at every call -- arguments into physical registers, the
  result out of `x0`, `MovConst`, block-parameter copies -- and *iterated*
  coalescing is precisely what IRC is named for. Hint-driven greedy coalescing
  catches most and not all. Nobody solves this: coalescing stays NP-hard on
  chordal graphs too.
* **No live-range splitting**, which is exactly what LLVM attributes Greedy's
  win to: "a large live range may be idle a lot of the time, but used
  intensively in a hot loop". Go, QBE and Air do not have it either.

So the expectation is Go and QBE's tier, and LLVM's own number bounds the whole
question: **1-2% smaller and up to 10% faster** is the entire distance between
a plain allocator and a world-class one. It is also the wrong thing to optimize
first here, because what surrounds the allocated code costs more than the
allocation does -- a shadow-stack store per live pointer at every call, a
test-and-branch after every call, a guard on every arithmetic operation, and
none of phase 3's optimizations yet. The allocator should not be the reason
this code is slow, and that is the whole bar.

**The root array is a spill slot, and the spiller should know.** A pointer live
across a safepoint is *already* being stored to memory, because that is what
rooting is. Spilling it therefore costs only the reload; the store is
sunk cost. So traced values live across a call are the cheapest things in the
function to spill, and pressure peaks at exactly those points. This is
specific to having a shadow stack rather than stack maps, and it is the one
place this design is cheaper than the peers rather than dearer. Measured under
phase 5: those values are not merely the cheapest to spill, they are *already*
spilt, which takes `boot`'s worst function from 88 live values to 35. It does
not remove the spiller -- 35 is still more than 28 -- but it is most of the
problem, and it is a saving no peer gets.

**Stack maps come out of the allocator**, because it is the only pass that
knows where a value is at a given point.

**The allocator gets a fuzzer, not a test suite.** Cranelift's experience is
that this is what made a high-complexity allocator transition safe, and a
register allocator is the one component here whose bugs are both easy to write
and invisible in a conformance run. This project has a second check the peers
did not: the same low IR compiled through LLVM and through arm64 must produce
identical output over the whole corpus, under `TURKEY_GC_STRESS=1`. That says
*a program* is wrong where a checker would say *an instruction* is, so it wants
the fuzzer beside it rather than instead of it.

## Register allocation, surveyed

Written before the allocator, and it reversed two things this document already
said. Five implementations, each one's algorithm read out of its *source*
rather than its summary, with what it measured and what it cost in lines.

| | algorithm | lines | what they measured |
|---|---|---|---|
| **QBE** `rega.c` + `spill.c` | backwards greedy, hint-driven, blocks ordered to peel loop nests inside-out; cost-based spiller (uses x loop depth) | **1,172** | nothing published |
| **WebKit Air** | Iterated Register Coalescing -- graph colouring | **~1,300** | "no significant difference in the quality of code" vs LLVM Greedy |
| **Go** `ssacompile/regalloc.go` | greedy over the whole function as one long block; spills the value whose next use is farthest away | **3,464** | -- |
| **LLVM Greedy** | priority queue, eviction, global live-range splitting | **~5,000** | 1-2% smaller and up to 10% faster than the linear scan it replaced |
| **Cranelift regalloc2** | backtracking, bundle merging, splitting | **>10,000** | ~20% off total compile time; 10-20% on register-pressure benchmarks |

**QBE's allocator is not linear scan, and this document said it was.** The
project page says "Linear register allocator with hinting", and that is where
the claim came from. `rega.c` is a *backwards* walk over blocks sorted by loop
depth, assigning from a hint table, with no intervals, no scan order over a
linearised function, and no backtracking. Its own comments are the tell:
`prio1` is a "trivial heuristic to begin with, later we can use the distance to
the definition instruction", and the block order carries "todo, evaluate if
this order is really better than the simple postorder". Neither file cites a
paper. What *is* real and is worth taking is the split: the spiller
(`spill.c`, 485 lines) runs first and decides what lives in memory, and the
allocator never revisits that decision -- which SSA is what makes sound.

**And the 70% is an aim, not a measurement.** "QBE is a compiler backend that
*aims to provide* 70% of the performance of industrial optimizing compilers in
10% of the code", with no benchmark cited anywhere on the page. It is a
statement of budget, which is how this document has used it, and it is not
evidence about output quality. The 10% half is checkable and holds; the 70%
half is a goal nobody has published a number for.

**Graph colouring is not the expensive option, and that is the counterexample
that matters.** QBE's line -- a split spiller is "simpler and faster than graph
coloring" -- reads as though colouring were what you pick when compile time
does not matter. WebKit picked it *for a JIT*, where compile time is on the
user's critical path, and gives the numbers: "IRC is around 1300 lines of code,
LLVM's Greedy is close to 5000 lines", chosen because it is "very concise",
needs little tuning, and "makes it easy to model the kinds of register
constraints" Air has. Their throughput results "seem to indicate that there
isn't a significant difference in the quality of code produced by B3's and
LLVM's register allocators" -- graph colouring at a quarter of the size, at
parity. So the axis is not colouring-versus-scan at all.

**What sophistication actually buys is about 10%.** LLVM's own report on
replacing linear scan with Greedy: "1-2% smaller, and up to 10% faster".
That is the ceiling on this decision for a mature C compiler on real hardware,
and it is the number to hold against any allocator design that costs more than
a week.

**Nobody's simple allocator is small.** Go runs the simplest algorithm in the
table -- one greedy pass over the function as though it were a single block --
and it is **3,464 lines**, three times QBE's. The algorithm does not predict
the size; the number of target constraints, calling-convention cases and
fixup paths does. Budgeting "linear scan, therefore small" would have been
wrong by a factor of three. Plan for **1,200 to 3,500 lines**, and treat
anything under that as a sign that a case has not been found yet.

**Wimmer and Franz measured what SSA buys an allocator, and it is compile time,
not code quality.** They rebuilt the HotSpot client compiler's linear scan to
run on SSA: lifetime analysis got **25-31% faster** because it needs no global
dataflow analysis, LIR construction **19-27% faster** because SSA deconstruction
is gone, the whole backend **13-19% faster**, and the implementation ended up
**about 200 lines shorter**. Output: machine code size changed by "1% or less",
run-time differences were "generally below the random noise", and there was "no
slowdown for any benchmark". Two details are worth more than the headline. The
allocation loop itself was "mostly unchanged" -- SSA paid in the analysis
around it, not in the assignment. And eliminating the interval-intersection
tests that SSA makes provably redundant "does not gain a measurable speedup",
which is a check this project would otherwise have built for the reason they
built it.

**Go recomputes flags rather than spilling them, and our situation differs.**
Go models the condition flags as an ordinary SSA value with its own type and
runs a 263-line `flagalloc` pass before allocation: "Flag values are recomputed
if they need to be spilled/restored." It needs that machinery because a Go
branch takes a flag value as a *control value*, so a flag can be live across a
block boundary and reach a merge. Ours cannot. `Term` is not parameterized, so
a condition is materialized into a register by `cset` and the branch reads the
bit -- which means a flag's live range is always exactly one instruction pair
inside one block. So the constraint stated earlier in this document stands, and
now for a reason rather than a preference: **"never insert between a
flag-setter and its `cset`" is a local invariant the allocator can hold, not an
analysis it has to run.** The cost of the design that makes it local is the one
extra instruction per guard, which is the trade already made and is cheaper
than 263 lines. If flags ever become live across a block -- which would mean
parameterizing `Term` -- Go's answer is the one to copy: recompute, do not
spill.

**The fuzzer is not optional, and this is the one place every peer agrees.**
regalloc2 "had *only* ever performed register allocation for fuzz-target-
generated inputs" for its first four months, with a purpose-built SSA validator
and a symbolic checker, and since shipping "we haven't found any miscompiles
caused by RA2 itself". Cranelift's own retrospective calls fuzzing what made a
high-complexity allocator transition safe. No project in this table verifies
its allocator with a conformance suite.

**What this project has that none of them had.** regalloc2 needed a symbolic
checker because there was no second implementation to disagree with. There is
one here: LLVM stays until the allocator is trusted, and the same low IR
compiled twice must produce two programs with identical output over the whole
corpus, under `TURKEY_GC_STRESS=1`. That is a stronger oracle than a checker
for the bugs that matter and a weaker one for locality -- it says a program is
wrong, not which instruction -- so it wants the fuzzer beside it, not instead
of it. It is also the argument, now evidenced, for the phase ordering already
written down: LLVM outlives the allocator's first working version.

## Spilling, surveyed

Written before the spiller, once measurement had settled that one is needed:
`boot` has 35 untraced values live at once against 28 registers, and up to 84
traced plus 31 untraced live across a single call against ten callee-saved
registers. The question is not whether to spill but what shape the first
spiller takes, and the peers split on exactly one axis: **whether a reload
creates a new definition that SSA has to be repaired for.**

| | what it does | where spills and reloads go | size | measured |
|---|---|---|---|---|
| **Braun & Hack** (CC 2009), in libFirm | Belady's furthest-next-use generalized to a CFG, per block in reverse postorder | spill at the first eviction; reloads on first use in a block or on incoming edges; **SSA reconstructed** for every reloaded variable | libFirm `bespillbelady.c` + `bespillutil.c` (~800 lines, the latter) | executed reloads **-54.5%** (spills -61.5%) vs a linear-scan allocator, **-58.2%** (-41.9%) vs graph colouring, on CINT2000, x86 |
| **libFirm** `bespillutil.c` | the placement back end for the above | spill after the definition by default, moved later when execution frequencies say it is cheaper; rematerializes where that beats a load | ~800 | -- |
| **QBE** `spill.c` | loop-depth-weighted cost, runs after SSA is left | spills after definitions, reloads before the instruction needing the register | **531** | nothing published |
| **Go** `regalloc.go` + `stackalloc.go` | greedy; restores made lazily at a use | "The spill of v must dominate that block" -- placed at a dominator of all restores where `v` is still in a register | 3,464 + ~450 | -- |
| **regalloc2** | split at the first conflict, one split per iteration; leftovers go to a "spill bundle" | split points chosen by conflict; spillslots shared by non-overlapping spillsets | >10,000 | -- |
| **Poletto & Sarkar** linear scan | spill the active interval that ends last | the whole interval, everywhere | small | "within 12%" of graph colouring with 31 registers |

**The spill-everywhere shape is the one Braun & Hack measure against, and they
are right about what it costs.** Their own description of the baseline is
exact: in extreme cases a failure "results in spilling the whole live range of
a variable: Stores will be put after each definition and loads in front of each
use, regardless of their location in the program." A value defined before a
loop and used inside it reloads every iteration; theirs reloads once in front
of the loop. That is the 54.5%.

**And what buys the 54.5% is SSA reconstruction.** Their algorithm "retains
the SSA form" by recording "all inserted reload operations per variable" and
reconstructing SSA for those variables -- "adding a reload causes a φ-function
to be created". libFirm does the same through `be_ssa_construction_fix_users`.
Go avoids it by not being in SSA at that point, and QBE by having left SSA
before spilling.

**Why this backend starts with spill-everywhere anyway, and what differs from
the peers who did not.** Three facts of this design, none of which the peers
share:

* **A reload that needs a φ needs a block parameter, and a `Branch` cannot pass
  one.** `Term` gives arguments to `Jump` only -- the property that made critical
  edges a non-problem above. So SSA reconstruction here is also edge splitting,
  which is a second transformation of the CFG, and a mistake in either is a
  wrong program that `Ssa.verify` does not catch (it checks dominance, not
  that the right definition reached the merge). libFirm has phis and splits
  nothing.
* **There is no execution oracle yet.** The spiller's output is not run until
  the frame and the emitter exist. Spill-everywhere is checkable locally --
  one slot per value, one store directly after its definition, every load used
  by the very next instruction -- and a reconstruction is not.
* **The ceiling on the whole question is already known.** LLVM's 1-2% smaller
  and up to 10% faster for a world-class allocator over a plain one, and
  Poletto and Sarkar's 12%, bound what spill placement can be worth, on
  programs where every call is already followed by a panic test and every
  pointer by a root store.

So the first slice keeps SSA the cheap way: every reload dominates its only use.
The chordal theorem still holds, colouring still cannot fail once pressure
fits, and **splitting is a later pass that rewrites these reloads** -- reload once
after a call rather than at every use -- when a measured load count says it
pays. Braun & Hack's algorithm is the one to reach for then, and their numbers
are the ones to hold it to.

**The root array still counts, and more than before.** A rooted value spilled
this way is written to its root slot once, at its definition, rather than at
every safepoint; the safepoint then writes only the mask. That is the one place
spill-everywhere is *cheaper* than the LLVM path's rooting. It needs slots at 64
and above zeroed in the prologue, which LLVM already does.

**Flags do not constrain reload placement.** The earlier worry was that a
spiller would insert between a flag-setter and its `cset`. It would -- and
`ldr` and `str` do not touch the flags, so nothing breaks. The invariant to
check is narrower: nothing *flag-setting* is inserted there.

**One hazard every spill shape shares, found by reading the colourer.**
Physical registers are tracked as *clobbered* but not as *live*. Selection
never defines a value between `mov x0, %a` and the `bl` that reads `x0`, so no
bug has shown -- but a reload inserted in front of `mov x1, %b` is exactly such
a value, and could be coloured `x0`. The allocator learns physical liveness
before it learns to spill.

Sources: [Braun & Hack, CC 2009](https://link.springer.com/chapter/10.1007/978-3-642-00722-4_13);
[libFirm `bespillutil.c`](https://github.com/libfirm/libfirm/blob/master/ir/be/bespillutil.c);
[QBE `spill.c`](https://c9x.me/git/qbe.git/tree/spill.c);
[Go `regalloc.go`](https://github.com/golang/go/blob/master/src/cmd/compile/internal/ssa/regalloc.go),
[`stackalloc.go`](https://github.com/golang/go/blob/master/src/cmd/compile/internal/ssa/stackalloc.go);
[regalloc2 `ION.md`](https://github.com/bytecodealliance/regalloc2/blob/main/doc/ION.md);
[Poletto & Sarkar, TOPLAS 1999](https://dl.acm.org/doi/10.1145/330249.330250).

## Frames, calls and roots, surveyed

Written before the frame, because the first draft of the plan for it copied the
LLVM path's root registration into every arm64 prologue -- a *call* to
`turkey_root_enter` on the way in and to `turkey_root_leave` on every way out --
and nothing about arm64 required that. The LLVM path does it because it was the
portable thing to write in IR; this backend owns its frames, and the question
is what owning them buys.

### How a collector finds the roots in a frame

| | what it does at entry and exit | what a safepoint costs | what the collector reads | size |
|---|---|---|---|---|
| **This project, LLVM path** | a call each way; the runtime links a 5-word `RootFrame` into a chain | a store per live root, then a store of the live mask | the chain, and each frame's mask | `turkey_root_enter` is five stores, `leave` one (`turkey_runtime.c:228`) |
| **LLVM `ShadowStackGCLowering`** | **no call**: a load of the chain head, a store of a per-function constant frame map, two stores to link | the root is written to its slot as it changes | the chain, and each frame's constant map | one pass |
| **OCaml native** | nothing | nothing beyond a live value already being in a stack slot: `destroyed_at_oper` for a call is `all_phys_regs`, so no register survives one | `caml_frametable`: per return address, the live stack offsets; a linear-probing hash keyed on the return address | `frame_descriptors.c` ~400 lines |
| **Go** | nothing for the maps | a PCDATA index per call site | a pointer bitmap per call site, found from the return PC through `funcdata`/`pcdata` tables | the runtime's stack scanner and the compiler's liveness pass |

LLVM's own documentation states the trade: the shadow stack is "slower than
using a stack map compiled into the executable as constant data", and its
drawbacks are "high overhead per function call" and that it is "not
thread-safe". Henderson's ISMM 2002 paper is where the shadow stack comes from,
for Mercury's C back end; its overhead figures could not be extracted from the
PDF for this survey, so the numbers below are this project's own.

**Why OCaml's shape fits here better than it fits most compilers.** Frame tables
are cheap only if the collector can find every live pointer from the stack
alone. OCaml gets that by keeping nothing in a register across a call. This
backend gets most of it already: every traced value live across a safepoint has
a root slot (`Turkey.Roots`), and selection stores it there before the call. A
copy of the same pointer may also stay in a callee-saved register, and that is
safe *only because the collector does not move objects* -- which the LLVM path
already relies on, since it keeps using the SSA value after the call rather
than reloading it. A moving collector would need either OCaml's rule or register
maps, and is not planned.

**What frame tables cost this project that they do not cost OCaml.** The
runtime is C and is shared with the LLVM path, which keeps its chain. So the
collector would walk *both*: the chain, as today, and the native frames of arm64
code -- following `x29` frame records, which Apple requires to be valid, and
looking each return address up in a table the emitter writes. The C runtime's
own frames in between are skipped because their return addresses are in no
table. That is a runtime walker of perhaps a hundred lines and a data section in
the emitter, against deleting the entry, exit and mask code from every function.

**Measured on this project, through the LLVM path** (2026-09-15). Stage2 `boot`
-- `Turkey.Llvm`'s own output for `boot/Main.gob`, 68 MB of IR -- running
`boot asm` over the 43-program corpus, built with `cc -O2` on an M-series Mac.
One run makes **455 million** frame enters. The module has 2,529 enter sites,
40,835 leave sites, 251,292 root-slot stores and 45,252 mask stores.

| variant | best | what it isolates |
|---|---|---|
| `turkey_root_enter`/`leave` are real calls (`.ll` and runtime compiled apart) | 8.88 s | today's LLVM path |
| the same with `-flto`, so both inline | 8.91 s | the call itself: **noise** |
| inline, collection never runs | 8.12 s | the shadow stack without GC time |
| push/pop stubbed and mask stores deleted, root stores kept, no collection | **7.05 s** | what frame tables leave behind |
| root stores deleted as well | 7.03 s | the stores rooting needs anyway |

Best of seven for the first two and best of nine, interleaved, for the last
three -- the machine was shared with another build, and the interleaved
minima agree to within 0.1 s run to run. `benchmarks/shadow_stack.sh` rebuilds
the variants and reruns the timings.

So the shadow stack costs **13%** of this workload's time once collection is
taken out, and *inlining it recovers none of that*: the call was never the
expensive part. The expense is 455 million pushes and pops and the mask store
before every safepoint. The root stores themselves cost 0.02 s -- which is why
frame tables keep them without regret. Frame tables remove exactly the 13%, and
an inline shadow stack removes nothing measurable over today's calls.

**Decided: frame tables.** The arm64 backend registers roots OCaml's way.
Selection keeps the store of each live root into its slot before a safepoint
and marks the call; frame layout records, per safepoint, the fp-relative slots
live across it; the emitter writes one table entry per return address; the
runtime's collector walks `x29` frame records and looks each return address up,
beside the chain the LLVM path and the C runtime go on using. No function enters
or leaves anything, and no safepoint writes a mask.

#### The table's encoding, surveyed

"Frame tables rather than a shadow stack" is decided above and measured. What
that leaves open is the part both the emitter and the C runtime have to agree
on for good: what a table entry is keyed by, who builds the lookup structure,
and how wide an offset is.

| | keyed by | who builds the lookup | entry shape |
|---|---|---|---|
| **OCaml** `runtime/frame_descriptors.c`, `caml/frame_descriptors.h` | the **return address** (`uintnat retaddr`) | the runtime, at startup: `caml_init_frame_descriptors` walks the compiler-emitted `caml_frametable[]` and fills an open-addressed hash table, linear probing, capacity a power of two and `num_desc * 2 <= capacity` | `{uintnat retaddr; uint16_t frame_data; uint16_t num_live; uint16_t live_ofs[]}` -- variable length, walked by `next_frame_descr`; `frame_data` packs the frame size with two flag bits |
| **Go** `runtime/stkframe.go`, `cmd/link` pclntab | the **pc offset within a function**, and a `pcdata` index derived from it | the **linker**, which is the only thing that knows final addresses; the runtime indexes `FUNCDATA_LocalsPointerMaps`/`ArgsPointerMaps` by the decoded `pcdata` value | one bitmap per distinct safepoint, indexed rather than searched -- and the pc must be backed up to the call: "Back up to the CALL. If we're at the function entry point, we want to use the entry map (-1)" |
| **LLVM** `__llvm_stackmaps` (`StackMaps.html`) | an ID plus "the offset within the code from the beginning of the enclosing function" | the runtime, explicitly: "compactness of the representation is secondary because the runtime is expected to parse the data immediately after compiling a module and encode the information in its own format" | fixed header per record -- `uint64` ID, `uint32` instruction offset, `uint16` flags, `uint16` NumLocations -- then 12-byte locations |
| **LLVM** shadow stack, for contrast (`GarbageCollection.html`) | -- | -- | the strategy this project measured at 13%: "slower than using a stack map compiled into the executable as constant data, but has a significant portability advantage because it requires no special support from the target code generator" |

**Go is the counterexample, and it does not transfer.** Keying on a pc offset
is better than keying on a return address -- it is denser, it needs no
relocation, and the lookup is an index rather than a probe -- but it costs a
linker that sorts every function by final address and writes the table. This
backend emits assembly and hands it to `cc` (LINKER.md, option B); it does not
know a final address and cannot sort by one. The emitter can write
`.quad <label>` and let the linker relocate it, which is exactly the shape
OCaml's `retaddr` has. So: **keyed by return address**, because the alternative
presupposes a linker this project chose not to write.

**The runtime builds the lookup, at startup.** All three peers agree and LLVM
says why outright: the compiler's format is for getting the facts across, not
for being queried. A table registered once and probed on every frame of every
collection should be shaped by the reader.

**What is being started simple, deliberately.** OCaml packs `frame_data` and
`live_ofs` into `uint16_t` and walks entries variable-length; `boot`'s largest
frame is 16,672 bytes and its largest root set is small, so 16-bit offsets fit
with room. That would quarter a table of 45,097 entries. It is also entirely
reversible -- the format has one producer and one consumer, both in this
repository -- whereas the two decisions above are not. So the first version
writes `.quad` throughout and the section's measured size is what argues for or
against packing it. Likewise the lookup: a sorted copy and a binary search is
fewer lines than a hash table, and the collection time is what decides whether
OCaml's probe is worth writing.

**The format, then.** One flat array, `_turkey_frame_table`, emitted after the
functions and registered once by the entry sequence:

    _turkey_frame_table:
        .quad   <number of entries>
        ;; per entry, in any order:
        .quad   <return-address label>      ;; the label after the `bl`
        .quad   <number of live roots>
        .quad   <offset from x29>           ;; × that many

The return-address label is the address *after* the call, which is what `x30`
holds and what a walker reads out of a frame record -- so the emitter has
nothing to compute: selection already marks the position with `SafepointMap`,
directly after the `bl`, and a label printed there is the key. The offsets are
`x29`-relative and signed, which `Turkey.Frame.layout` has been computing since
phase 4 (`rootsAt + 8·slot - recordAt`).

`turkey_frame_table_register(const int64_t *table)` is called before anything
allocates. A binary that registers nothing -- every LLVM-path binary, which
links this same runtime -- walks no frames, which is what lets one runtime serve
both backends.



### Frames

| | frame record and callee-saves | outgoing stack arguments | scratch registers | size |
|---|---|---|---|---|
| **Apple arm64 ABI** | "`x29` must always address a valid frame record"; `x18` is reserved | packed: an argument narrower than 8 bytes takes its own size; the *caller* extends arguments narrower than 32 bits | -- | -- |
| **QBE** `arm64/emit.c`, `abi.c` | frame record at `x29`, then callee-saves, spill slots, locals; `stp x29, x30, [sp, -N]!` when N ≤ 512 | `sp` adjusted per call | `x16` for frames over 4095 bytes; a scratch register when a slot offset passes `4095 × size` | 693 + 852 |
| **Go** `cmd/internal/obj/arm64` | frame pointer and link register saved at entry, 16-byte aligned; large frames store the record before moving `sp` so a signal never sees half a frame | -- | `REGTMP` for frame sizes past 12 bits | -- |
| **Cranelift** `aarch64/abi.rs` | callee-saves as `stp` pairs "at the top of the frame, just below FP" | a **preallocated** outgoing area, `sp` adjusted once | `x16`/`x17` as spill temporaries | ~1,400 |

The decisions these settle:

* **A preallocated outgoing area, as Cranelift and Go do, not QBE's per-call
  adjustment.** Root and spill slots are addressed from `sp`, and an `sp` that
  moves around each call would move every one of those offsets with it.
* **Stack arguments between Turkey functions take whole 8-byte slots.** Apple's
  packing rule is for arguments narrower than a word, and every value this
  compiler passes on the stack is passed as a word. Only Turkey code calls
  Turkey code with more than eight arguments -- runtime calls take at most four,
  and C enters Turkey only through `turkey_main`'s `void (*)(void)` -- so the
  convention is this compiler's to define, and it is Apple's for words.
* **`x16` and `x17` are reserved**, as QBE, Go and Cranelift all reserve one or
  both: large frames, large slot offsets, and the cycle in a block-parameter
  parallel copy each need a register nobody else holds.

Sources: [LLVM `ShadowStackGCLowering.cpp`](https://github.com/llvm/llvm-project/blob/main/llvm/lib/CodeGen/ShadowStackGCLowering.cpp);
[Garbage Collection with LLVM](https://llvm.org/docs/GarbageCollection.html);
[OCaml arm64 `proc.ml`](https://github.com/ocaml/ocaml/blob/trunk/asmcomp/arm64/proc.ml),
[`frame_descriptors.c`](https://github.com/ocaml/ocaml/blob/trunk/runtime/frame_descriptors.c);
[Go `stkframe.go`](https://github.com/golang/go/blob/master/src/runtime/stkframe.go),
[`obj7.go`](https://github.com/golang/go/blob/master/src/cmd/internal/obj/arm64/obj7.go);
[Henderson, ISMM 2002](https://bernsteinbear.com/assets/img/gc-uncooperative.pdf);
[Writing ARM64 code for Apple platforms](https://developer.apple.com/documentation/xcode/writing-arm64-code-for-apple-platforms);
[QBE `arm64/emit.c`](https://c9x.me/git/qbe.git/tree/arm64/emit.c),
[`abi.c`](https://c9x.me/git/qbe.git/tree/arm64/abi.c);
[Cranelift `aarch64/abi.rs`](https://github.com/bytecodealliance/wasmtime/blob/main/cranelift/codegen/src/isa/aarch64/abi.rs).


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

  Built in slices against real programs rather than in one attempt. An
  unhandled Core form stops one binding and is *reported*, so `boot ssa` prints
  how much of a program lowered and a histogram of what stopped the rest --
  which is the plan for the next slice, and is better at making it than reading
  the input is (FINDINGS 58). What is asserted is not how much lowers but that
  everything which does is well formed: `Ssa.verify` runs over every lowered
  function and the dump carries a line per complaint.

  Two departures from the ancestor pay for themselves in the first slice.
  Destination passing goes into a *block parameter*, so `if` and `match` need
  no machinery -- the arms lower towards the same destination and the merge
  block's parameter is where they meet. And nothing gets a slot merely for
  crossing an edge, because a definition dominates its uses; the Python
  backend's SSA is block-local and stores the scrutinee into a slot to reload
  it in every test block.

  Closure conversion departs from the ancestor twice more. A lifted lambda
  captures its *free* variables rather than the whole environment, which is not
  an optimization: `turkey_object_new` refuses more than 63 slots, and an
  environment in a body the size of `Turkey.Opt#expr` would exceed that, so
  capture-everything is a runtime panic on this compiler's own source. And
  `CLetRec` needs no slots -- the Python original allocates one per binding so
  a sibling's closure has an address before it has a value, and with
  function-wide SSA what must be known before the lift is a capture's
  *representation*, which is a traced pointer whether or not it has been
  allocated.

  A top-level function used as a value becomes a **boxing adapter written in
  this IR**, not an opcode: `llvmgen.py` generates such a thunk at emission
  time, and doing it here means it is verified and optimized like everything
  else, and no emitter has to generate a function body from inside a pass whose
  job is something else. That is the "nothing is shaped to suit LLVM" rule
  paying for itself rather than costing.

  Two hazards this creates are checked rather than trusted. A **signature
  table** answers what a callee is called at, so a direct call coerces to the
  callee's representations and a closure call boxes -- two conventions the
  lowering had been conflating. And **`LowIr.checkCalls`** checks agreement
  *between* functions, which `Ssa.verify` cannot: a lifted function taking
  three parameters and an indirect call passing two are both well-formed
  graphs.

  The **module initializer** is one function computing every global, and it is
  two phases for one reason: a dictionary's fields are the instance's methods,
  and a method mentions the dictionary it belongs to, so building the record in
  one step would need its own address before it had one. Every record-shaped
  dictionary is therefore allocated and published first and filled afterwards
  -- the shell-first discipline `CLetRec` uses, one level up. Unlike a skipped
  function, a skipped *global* is a hard failure: a missing function leaves a
  symbol its callers name and `checkCalls` says so, while a missing global
  leaves an initializer that runs to completion and quietly did not
  initialize something.

  Status: **complete**. All 28 corpus programs lower with nothing skipped and
  with the verifier, the representation checks and the cross-function check
  all silent.
* **Phase 2.** Low IR to LLVM IR text, and `boot build`. The conformance suite
  runs under differential execution. **`boot` is self-sufficient here**, and
  everything above this line is now exercised by every program in the suite.

  Three things make the text form less work than `llvmgen.py`'s library form.
  **Opaque pointers** (LLVM 15+) remove almost every bitcast, since most of
  them existed only to satisfy a type system this level does not need.
  **Quoted symbols** take `#`, `@`, `%` and commas, so there is no mangling
  scheme to invent and later regret -- the symbol in the object file is the
  name in Core. And **block parameters become phis only here**, mechanically,
  because the lowering keeps an invariant worth stating: only a `Jump` carries
  arguments and a `Branch`'s targets take none, so no edge has to be split.

  Status: **all 28 corpus programs** compile to native binaries whose output is
  byte-identical to the reference implementation running the same source.
  Overflow, division-by-zero and panic propagation are all checked -- each is a
  guard that *splits* a block, which is why a block's phis name the label
  control left from rather than the one it entered.

  **GC roots are four things, and two of them are not on a stack.** A stack
  root frame per function -- liveness at each safepoint, a slot per value live
  across one, and a live mask written per program point rather than per
  function, which is what the runtime's `RootFrame.live` exists for. Then the
  **pointer globals**, which live *in* a permanent root array so that storing
  to a global and rooting it are the same store; every instance dictionary is
  one. Then the **interned string literals**, whose caches are globals the
  collector could not otherwise see.

  Two more kinds, and both are about *which* values: a safepoint's **arguments**
  are live across it, because the callee holds them while the collector may
  run, even when they are dead afterwards -- and the **slots past 64**, which
  the live mask has no bits for and the runtime therefore always scans, so they
  have to start null.

  Measured with `TURKEY_GC_STRESS=1`, which collects at every allocation:
  **0 of 28 → 6 → 11 → 22 → 27 → 28**, one kind of root at a time. Done.

  `boot build` is blocked on something small and external: `boot` cannot start
  a process, because there is no `Prim.exec`. It emits the module -- which is
  the compiler -- and one `cc` invocation links it against the runtime.

  What it would take to remove that `cc` is surveyed in `LINKER.md`, and the
  answer is much smaller than "write a linker": the measured requirement is
  **seventeen GOT binds against one dylib**, because generated code references
  only the runtime and never libc. The decision is deferred until phases 4 and
  5 exist, since instruction selection and encoding are the same work whatever
  the output format is -- and because the one thing none of the options do is
  remove the C toolchain, the runtime being C.
* **Phase 3.** The four optimizations, one at a time, each measured.
* **Phase 3.5 -- expansion, and it comes first.** The low IR does not contain
  the language's semantics: overflow checks, division guards, panic
  propagation and the entire GC root apparatus live in `Turkey.Llvm`, about
  300 of its 1,388 lines. A second backend would write all of it again and the
  two would have to agree about which values are live at a safepoint --
  the shape of mistake FINDINGS 56, 59, 60 and 66 are all instances of, with
  the worst failure mode of the four.

  So: a pass over the low IR that rewrites checked arithmetic into an explicit
  compare and branch, inserts the panic-flag test after each call, and
  materializes the root frame. `SlotLoad` and `SlotStore` are already in the
  IR and unemitted, and a root array is exactly the stack slot they were left
  there for, so no opcode is added. Afterwards both emitters are dumb
  translations and neither knows what a safepoint is -- and the pass's output
  is checked by `Ssa.verify`, `LowIr.checkCalls` and differential execution,
  none of which can see the version that lives inside an emitter.

  Found by starting phase 4, which is the general lesson: a backend with one
  consumer cannot tell which of its facts are in its IR and which are in its
  emitter (FINDINGS 70).

  **Why a pass and not the lowering.** The obvious alternative is to emit all
  of this in `SsaLower`, where the Core is still in hand -- one less pass, and
  `boot ssa` would show the true semantics immediately. Go answers this, having
  the same choice for the same reasons: its pass order runs *NilCheckElim,
  Prove, BCE, Loop, Fuse, DSE,* **WriteBarrier**. Nil checks go in early
  because `nilcheckelim` and `prove` can *delete* them; write barriers go in
  late because nothing can, and an early one would only be in the way.

  **But not all three, and the arithmetic is the one to leave alone.** Expanding
  a trapping `Bin(Add, ...)` into a check plus a wrapping add would give the
  opcode two meanings -- traps before the pass, cannot appear after it -- with
  nothing enforcing which phase a function is in. That is the same "one enum
  spanning two levels" this design rejected for virtual and machine opcodes,
  reappearing a level up. Getting the guarantee back would mean a third
  instantiation, an instruction type differing from `Low` by about six
  constructors out of thirty; nanopass would *generate* that from a diff and
  Turkey cannot, so it is 80% duplication for a guarantee about 20%. The
  selection case is worth it because the two languages share almost nothing.
  This one is not.

  The second problem is the deciding one. Testing overflow without hardware
  flags needs either a new opcode or four wrapping operations to compute it by
  hand -- and **the check is precisely the part each target does differently
  and better**. LLVM wants `llvm.sadd.with.overflow`; arm64 wants `adds` and
  `b.vs`, reading a flag the IR has no way to name. A target-neutral expansion
  would pessimize both, and it would be the only part of the pass that does.

  So the split is not "early or late" but **neutral or not**:

  * **GC roots are target-neutral, and the neutral part is the *analysis*.**
    Done: `Turkey.Roots`, 171 lines, answering which values are live across
    which safepoint and what slot each gets. The *emission* -- storing a
    pointer into an array and writing a mask -- stayed in the emitter, because
    it is five lines anyone would write the same way and it is not what two
    backends could disagree about. Hoisting the analysis removes the hazard;
    hoisting the emission would have removed duplication that was never
    dangerous, and would have cost the IR an opcode for the address of a stack
    slot.
  * **Panic propagation is target-neutral too, and has no analysis at all** --
    it is "after every call", which is not a rule two backends can disagree
    about. Left in each emitter.
  * **Overflow and division guards stay in each emitter**, at about 80 lines
    each, because each emitter writes a different and better sequence. Two
    implementations of one *rule* is the hazard; two encodings of one *check*
    is the job.

  A third category turned up once a second backend existed, and it is not on
  the neutral/not axis at all: **facts about an artifact this compiler does
  not own.** The layout codes `mark_children` reads out of an object header,
  the array element widths `turkey_array_new` is called with, and the symbol
  each primitive resolves to are all `runtime/turkey_runtime.c`'s, not any
  backend's. A backend holding its own copy is not a second implementation of
  a rule; it is a second transcription of someone else's constant, and it
  drifts without anything going red -- `Turkey.Select`'s copy of the primitive
  table had already fallen seven float entries behind `Turkey.Llvm`'s.
  Collected in `Turkey.Runtime`.

  `Turkey.Llvm.declareRuntime`'s `declare` lines name those same symbols and
  stayed where they are, which is the boundary of the rule: they carry
  argument types the table does not have, and an undeclared or mistyped symbol
  is something LLVM *refuses*. Duplication a checker sees is not the dangerous
  kind.

  **Two constraints the guards create, for the passes that do not exist yet.**

  * **Nothing may come between a flag-setting instruction and the `cset` that
    reads it.** `adds`/`subs`/`cmp` write NZCV and the `cset` consumes it, and
    the register allocator is the pass that would insert a spill between them.
    arm64 has no way to name the flags as an operand, so nothing in the graph
    says these two are joined -- the allocator has to be told. LLVM models
    NZCV as a register for exactly this reason; the cheaper answer here is to
    treat the pair as indivisible when the allocator is written.
  * **The condition is materialized into a register rather than branched on
    directly.** `Term` is not parameterized the way the instruction type is,
    so `b.vs` is not something the machine CFG can express as a terminator --
    a `cset` and a branch on the bit is. That costs one instruction per guard
    and keeps the graph a graph, which is the trade the CFG bug earlier in
    this phase argued for. Folding `cset`+`cbnz` back into `b.cond` is a
    peephole for the encoder, where it is local and checkable, rather than a
    reason to give the CFG a second type parameter.

  **Two register files.** `Reg` carries a `Bank` on its physical constructor
  and not on its virtual one, which is the asymmetry the design earns: a
  virtual register's file is already written down in `Func.reps` -- `F64` or
  not -- and a second copy of that fact is a second copy that can disagree. A
  physical register has no rep to consult, so `x0` and `d0` are
  `Physical(Gp, 0)` and `Physical(Fp, 0)` and never the same value.

  Three consequences worth having stated before the allocator is written:

  * **AAPCS64 numbers the two files' argument registers separately.** `f(1,
    2.0, 3)` passes `x0`, `d0`, `x1` -- two counters, not one. A single
    counter produces code that assembles, links, runs, and reads the wrong
    register.
  * **The runtime boundary is the bit pattern, not the value.** `turkey_box`
    and friends take an `i64`, so a double crosses through `fmov x, d` --
    which is what `Turkey.Llvm.widened` spells `bitcast`. Loads and stores
    need no such step, because `str d0, [x1]` writes the same bits `str x0,
    [x1]` would.
  * **ARM's `ne` is not LLVM's `one`.** After `fcmp`, `ne` is true when the
    operands are unordered, which makes it LLVM's `une`. Ordered-not-equal
    needs `lo` or `gt` and an `orr`. Nothing in the corpus compares a NaN, so
    this is a rule that would have been wrong silently for as long as that
    stayed true.

  The rule for *when* the neutral parts run is still Go's: **early if an
  optimization can use it, late if it can only be obstructed by it.**

  * **Panic propagation -- late, definitively.** Every call becomes a block
    terminator, so the block count roughly triples and every analysis after it
    -- liveness, dominance, the allocator -- pays for a branch that is never
    taken. Nothing can optimize it away.
  * **GC roots -- last, and not merely late.** They need liveness over the
    *final* CFG, and the panic branches change it. Roots computed at lowering
    time would be a liveness answer about a graph that does not exist yet. That
    is not a preference; it is a correctness argument.

  Keeping the arithmetic abstract also keeps a fact the optimizer reads:
  `effectsOf` says a checked `Bin` *traps*, which is how DCE knows it cannot
  delete an unused one. Expanded into a branch, that becomes a control-flow
  join instead -- strictly less information, and the reason the guards would
  have wanted to be late even if they were neutral.

  The separate pass also buys an oracle the lowering cannot have: it can be
  **toggled**. Run the corpus with and without it and the output must be
  identical for every program that does not trap -- which is a test of the
  expansion, by itself, that a lowering-time version has no way to express.

  What the lowering-time version is right about is that between lowering and
  expansion the IR does not mean what it says. That is FINDINGS 70's actual
  complaint, and the answer is that this is fine as a *named phase* and was not
  fine as an undocumented thing one emitter did -- so `boot ssa` should dump
  after expansion once the pass exists.

  **A known compromise, named now.** Rooting by storing into an array forces a
  rooted value live across the call, which is a shadow stack: portable, and
  slower than what a real collector does. The alternative is stack maps emitted
  *after register allocation*, which is where LLVM's statepoints and Go's maps
  live, because only then is it known where a live pointer actually sits -- a
  spilled pointer is not in an array anyone wrote to. Our way sidesteps that by
  construction. The upgrade path exists and is phase 5's business, not this
  one's.

* **Phase 4.** Instruction selection, arm64, table-driven.

  Selection produces a machine-instruction *value*, never assembly text. That
  was already the rule -- a second instantiation of the CFG, so that a virtual
  opcode is not representable afterwards -- and `LINKER.md` gives it a second
  reason: text and bytes are then two consumers of one IR, a `Show` and an
  `encode`, and choosing between them later is adding a consumer rather than
  replacing a design.

  Build the `Show` first regardless. `as` assembles what it prints, which makes
  the system assembler an instruction-by-instruction oracle for the byte
  encoder -- the part of a backend hardest to get right, and the part Cranelift
  says needs a fuzzer.

  Status: **1866 of 1866 functions across the corpus select**, with
  `Ssa.verify` silent on every one. One gap is open and is phase 5's to close.

  **More than eight arguments in one register file: closed in phase 5.** It
  was open here in two unequal halves -- the caller stopped with a reason, and
  a nine-parameter *callee* selected silently with no incoming convention at
  all, which `opt` hid by folding every constant call to one. Both close with
  the frame: `Select.call` stores the overflow into `[outgoing k]` and
  `Select.incoming` loads it from `[incoming k]`. `tests/programs/manyargs.gob`
  and `stackargs.gob` keep both halves honest; the callees recurse, because a
  constant call folds away and proves nothing.

* **Phase 5.** Register allocation, stack maps, encoding, object emission.

  **Measured before written, because the spiller is the expensive half.** On
  SSA the registers a function needs are exactly the values live at once, so
  "does the corpus need spilling at all" is a question with an answer rather
  than a guess. `Turkey.Regalloc.pressureOf` reports it and `boot asm` prints
  it:

  | | max general | max vector | over budget |
  |---|---|---|---|
  | the corpus, 40 programs | **13** of 28 | 3 of 32 | **none** |
  | `boot` compiling itself, 2,912 functions | **88** of 28 | 3 of 32 | **three** |

  The corpus alone would have said no spiller is needed, and shipping on that
  would have produced an allocator that compiles every test program and cannot
  compile the compiler. The three that exceed the file are
  `%module.initialize` at 88, `Turkey.Llvm#declareRuntime` at 57, and
  `Turkey.Infer#genMethodBodies` at 29 -- and the first of those is 16,968
  instructions across 431 blocks, which is what a module initializer computing
  every global in one function looks like.

  **And then the root array turned out to be the spiller.** `Turkey.Roots`
  already gives a stack slot to every traced value live across a safepoint,
  and the emitter already stores it there before the call -- that store *is*
  rooting. So such a value need not hold a register across the call at all: it
  can be read back afterwards, for the price of a load and no store. Counting
  that, pressure is not 88:

  | | raw | with the root slots counted |
  |---|---|---|
  | the corpus | 13 of 28 | **10** of 28 |
  | `boot` compiling itself | 88 of 28 | **10** of 28 |

  And what is left holding registers across a call -- the untraced values,
  which have no slot -- peaks at **6** against ten callee-saved registers, in
  the corpus and in `boot` alike.

  **This nearly became "so there is no spiller", and that was wrong.** The
  claim survived about an hour, on the strength of the table above, and what
  killed it was measuring the one category that has nowhere free to go. A
  traced value has a root slot; an *untraced* one has none, so it needs a real
  stack frame:

  | | untraced pressure | worst call site |
  |---|---|---|
  | the corpus | 10 of 28 | 11 live, of 10 callee-saved |
  | `boot` compiling itself | **35 of 28** | 84 live |

  Thirty-five untraced values live at once, against twenty-eight registers. No
  arrangement of root slots helps, because none of those values has one. **A
  real spiller with real stack slots is required for `boot` to compile
  itself**, and the frame is its prerequisite.

  What the root slots do buy is the difference between 88 and 35, which is most
  of the problem and is still the thing this backend has that its peers do not.
  QBE, Go and LLVM spill every one of those 88; this backend spills at most 35
  of them, and the other 53 were already written to memory for the collector's
  sake. That is a smaller spiller, not the absence of one.

  **The spill policy is first come, first served, and deliberately not a
  heuristic.** Whichever value the colourer reaches with no register free is
  the one that goes to memory. Nothing is ranked, no cost model is built, and
  loop depth is not consulted. Two reasons. Feasibility does not depend on the
  choice -- any value spilled frees exactly one register -- so an ordering
  could only affect *quality*, and quality here is measurable against the LLVM
  path rather than guessable. And a spill heuristic is the single largest and
  least certain part of a register allocator; building one before there is
  evidence it pays would be the mistake this document's survey section exists
  to prevent. If measurement later says the allocator is what makes the code
  slow, Belady's furthest-next-use and QBE's cost-times-loop-depth are both
  known, both small, and both drop into the same place.

  The 10 was an approximation and said which way it erred: a value live across
  *some* call was excluded from every live set, so it under-counted what such
  values use between calls. It was optimistic by a factor of three and a half;
  the untraced figure above is the honest one.

  **Status (measured 2026-09-15): the allocator finishes on the corpus and on
  `boot`; nothing yet emits runnable code.**

  | | selected | allocated | spilled (into root slots) | reloads | argument hints taken |
  |---|---|---|---|---|---|
  | the corpus, 43 programs | 2741 of 2743 | **2741 of 2741** | 60 (32) | 125 | 5761 of 7107, 81.1% |
  | `boot` compiling itself | 3040 of 3061 | **3040 of 3040** | 5284 (3642) | 9163 | 4845 of 8708, 55.6% |

  Spilling is spill-everywhere ("Spilling, surveyed" above): a value with no
  register is stored once after its definition and reloaded before each use,
  and every function finishes in one round of it. Before it, colouring
  stopped in 10 corpus functions and 249 of `boot`'s. `boot asm boot/Main.gob`
  went from 65 to 88 seconds.

  **How a value is chosen, and the thing the plan got wrong.** The plan was
  first come, first served: spill whichever value the walk could not place.
  That is right for one kind of failure and does nothing for the other. When a
  register is free but *forbidden* -- the value is live across a call or a
  physical register in use -- spilling that value is exactly the fix. When
  every register is *held*, spilling it frees nothing: it still needs a
  register at its own definition, for the one instruction before its store.
  So that case evicts the holder whose next use is furthest away, which is
  Belady's rule applied at one point. Both are in `Regalloc.place`.

  **Three checks run on every allocated function**, because spilling rewrites
  it: `Ssa.verify` (still a graph), `verifyColouring` (still a colouring, now
  including physical registers as live), and `verifyAllocation` (one value per
  slot, each store directly after its definition, each reload read only by the
  instruction it was loaded for, every `cset` still reaching its flag setter).
  None reports anything on either. **These are checkers, not an oracle**:
  whether spilled code computes the right answer is not known until the frame
  and the emitter exist and the corpus runs against LLVM.

  Selection stops nowhere: 2781 of 2781 corpus functions and 3073 of 3073 in
  `boot`, since stack arguments landed. Every function takes its closure
  environment as a hidden first argument, so a helper with eight declared
  parameters is a nine-argument call -- the first version of the spiller added
  two such calls to `boot` itself (FINDINGS 88). The three `Prim.floatBits`
  stops are closed, with `Prim.floatFromBits`, `Prim.floatIsNaN` and
  `Prim.floatFitsInt` (FINDINGS 87).

  Zero complaints from `verifyColouring` on either, which was the check that
  mattered before spilling: a colouring putting two simultaneously live values in one register
  prints, assembles, links, runs, and computes a wrong answer, and nothing
  downstream can see it. It checks reachable blocks only, and only functions
  whose colouring *finished* -- a stopped one has unassigned values by
  construction, and verifying it reports hundreds of consequences of the one
  fact already in the histogram.

  **The hint rate is the first number here that is about quality rather than
  about working at all.** A parameter is *hinted* to its argument register and
  never pinned: the emitter opens each function with a move from `x`*i* into
  wherever the parameter went, so a taken hint makes that move redundant and a
  missed one makes it real. Neither can make the result wrong, which is the
  point -- a pin can contradict, since a parameter fixed in `x0` and live
  across a call has had `x0` destroyed by the callee, and a preference cannot.
  The 31% and 61% that miss are mostly exactly that case, and they cost one
  `mov` each.

  **Dead blocks reach the machine graph, and the arm64 emitter will have to
  drop them.** `SsaLower` emits a fallthrough panic block for every `match` --
  `%4 = const "no match arm applied"; panic %4` -- and when the match is
  exhaustive nothing branches to it. LLVM's own dead-code elimination deletes
  these on that path, so they have never cost anything; nothing on this path
  will unless the emitter skips unreachable blocks. Wasted bytes rather than
  wrong code.

  The 156 are the gap between the approximation and the truth, and they are
  **not** an argument for a spiller. The colourer as written gives each value
  one register for its whole life, so a rooted value still occupies one across
  every call -- which is exactly what the 88-to-10 measurement said not to do.
  What closes them is splitting a rooted value's live range at each call:
  release the register, and read the value back from the slot the rooting
  already wrote. That is a fixed rule rather than a heuristic, it needs no
  spill-cost model, and it is why this is still not a spiller.

  The order is what the project does everywhere else: the corpus works and
  `boot` needs one more slice, and the histogram names it.

  **Boundaries and frames (measured 2026-09-15).** Each function now has both
  halves of the calling convention as instructions, and a laid-out frame:

  * `Select.incoming` gives every function a new entry block that moves each
    parameter out of `x`*i*, `d`*i* or `[incoming k]`; the machine function has
    no `params` of its own, so each parameter has one definition the allocator
    sees. Hints read off those moves, and off argument and result moves too.
  * `Select.call` passes arguments past eight in a file in `[outgoing k]`, a
    preallocated area at the bottom of the frame.
  * Before a safepoint, selection stores each live root into its slot and
    marks the call with a `SafepointMap`; `Turkey.Frame` turns each mark into a
    frame-table entry of `x29`-relative root offsets. Nothing enters or leaves
    a root frame and no safepoint writes a mask -- the decision "Frames, calls
    and roots, surveyed" measured.
  * `Turkey.Frame.layout` places the frame record, the callee-saved registers
    the colouring used, root slots, spill slots and the outgoing area, 16-byte
    aligned; `verifyFrame` checks the regions, that no value holds a reserved
    register (`x16`-`x18`, `x29`, `x30`), that every slot is in the frame, and
    that every root a map lists is stored before its call.

  | | selected | frames: largest | callee-saved used, most | functions with stack arguments | frame-table entries |
  |---|---|---|---|---|---|
  | the corpus, 44 programs | 2781 of 2781 | 2,576 bytes | 10 gp, 8 fp | 7 | 4,070 |
  | `boot` compiling itself | 3073 of 3073 | 16,672 bytes | 10 gp, 1 fp | 22 | 45,097 |

  `verifyFrame`, `verifyAllocation`, `verifyColouring` and `Ssa.verify` report
  nothing on either. These are checkers rather than an oracle, which FINDINGS 91
  is the demonstration of.

  **The emitter prints functions (`boot native`, measured 2026-09-15).**
  `boot asm` keeps the machine IR and its histograms; `boot native` prints what
  the assembler reads. `Turkey.Emit` adds only what had no instruction before:
  the prologue and epilogue from `Turkey.Frame.Layout`, the branch a terminator
  becomes (with the next block falling through), the parallel copy a jump's
  arguments become on the edge, and the substitution of the colouring and the
  slot offsets. A move into the register a value already holds is dropped,
  which is what a taken hint looks like from here.

  | | assembly | `as` | nothing skipped |
  |---|---|---|---|
  | the corpus, 44 programs | 302,146 lines | all 44 assemble | yes |
  | `boot` compiling itself | 2,271,163 lines, 49 MB, 203 s | 8 s, a 10 MB object | yes |

  Two things moved into selection to make that possible, both of which the
  LLVM path already had and the arm64 path did not:

  * **The panic test after every call.** `turkey_panic` sets a flag and
    returns, so a caller that did not look would carry on with a value the
    callee never produced. `Select.propagate` reads
    `turkey_has_panicked` -- through the global offset table, since the runtime
    defines it -- and reuses `guard` to return a zero. 12,614 of them in the
    corpus.
  * **`Panic(v)` as a terminator**, which is `turkey_panic_string` and a return
    of zero. And the zero itself: `mov d0, xzr` is not an instruction, so a
    `Float` function's panic path needs `fmov`, which nothing had exercised
    until whole functions were assembled.

  **`as` is an oracle for spelling and not for meaning.** It accepted a
  parallel copy that lost half its values (FINDINGS 91), and it will accept
  anything else this backend decides wrongly. The oracle for meaning is running
  the program against LLVM, which needs the module data and the runtime walker
  -- the next slice, sketched under phase 5b: globals, string literals, the
  permanent root array, module initialization, the entry point, the frame table
  as data, and the collector walking `x29` records beside the chain it has.

  **Spilling after the root stores**, on `boot`: 5,702 values spilled (3,937 of
  them into root slots), 5,702 stores and **10,001 reloads**. The first version
  had 24,977 -- every root store before a safepoint read its value, and for a
  value already spilled into that very slot the read was a reload of what the
  slot held. `spill` now drops those stores (FINDINGS 90). The allocator's move
  hints now cover argument and result moves as well as parameters: 153,525 of
  184,766 taken. `boot asm boot/Main.gob` takes 126 seconds, up from 88;
  selection runs every function twice (once for the function, once for its
  stop reason) and each run now analyzes roots, which is the first place to look.

LLVM is transitional: it is what phase 5 is differentially checked against, so
it outlives the allocator's first working version by however long that takes to
trust, and is then dropped. What it must not become is a constraint -- nothing
in the low IR is shaped to suit it, so the day it goes costs one module.

## Record layout: what the representation is, and what a packed one needs

Deferred, and written down so that the change is made once and from the facts.
`PROPOSALS.md` 6 argued that a record's layout should follow from its field
types, as an `Array Byte`'s already does. The argument stands; two of the
proposal's premises do not, and the one rule it states is right for a reason
other than the one it gives.

### What the representation is today

* **Header.** 24 bytes: `kind` and `tag` (`i32` each), `count` (`i64`),
  `pointer_bitmap` (`u64`) -- `TurkeyObject` in `runtime/turkey_runtime.c`, `_OBJECT`
  in `turkey/llvmgen.py`, and byte offsets in `boot/Turkey/Llvm.gob`.
* **Slots.** One 8-byte word per field, in declaration order, at `24 + 8*i`.
  Every backend computes a constant offset: `_object_slot`, `slotAddress`, and
  `Select.gob`'s `Ldr/Str [target, #24+8*index], W64`. `field_index` and
  `fieldIndex` answer the declaration position. Scalars are unboxed in the word
  -- zero-extended, a `Float` bitcast -- and `record_stores.gob` pins the mixed
  case.
* **The collector's view.** `pointer_bitmap` is **three bits per slot**, a layout
  code (`UNIT 0, I1 1, I8 2, I32 3, I64 4, F64 5, PTR 6, BOXED 7`), and
  `mark_children` traces a slot whose code is 6 or more. It is written per
  *object*, at the construction site, from the layouts of the values stored
  (`_layout_metadata`, `SsaLower.metadata`). Three bits a slot is why
  `turkey_object_new` caps a constructor at 21 fields.
* **What reads positions.** `field_index`/`fieldIndex`, `_record_layouts`, the
  metadata bit positions, the 21-field cap, and two hard-coded shapes in the
  runtime: `array_parts` reads `Data.Array#ArrayStorage` as slots 0 and 1, and a
  closure is `[code, env]`. Nothing else -- the Python evaluator, `pygen`, the
  local-record flattening and nullary sharing all go by name.

### Where the proposal was wrong

**"Pointers first, so `pointer_bitmap` stays a bitmap over the leading N
slots."** It is not a leading-N bitmap and never was: it is a code per slot, so
the collector already handles pointers and scalars interleaved in any order.
Reordering buys the collector nothing. What packing *does* need from it is a
split between the words it scans and the bytes it skips, which is a different
header change.

**"A field whose type is a type variable is always one word, because `mono.py`
is partial."** The guard against a partial specializer is not a uniform field.
It is `layout.share`, which gives every body that reads a field through a
transparent parameter one copy per layout of its type arguments, and
`mono.check_layouts` (7e3479a), which refuses a program where such a parameter
survives. A layout-keyed copy of `fun get(b : Box a) -> a` knows the width of
`a` perfectly well.

### Why the rule is still right, at first

The reason to keep a type-variable field at one word is **agreement between
producers and consumers without cross-body analysis**. If a field's offset is a
function of its *declared* type alone, ground code, every layout-keyed copy and
every body left generic compute the same offset for the same constructor,
whatever they know about the instantiation. Only fields whose declared type is
concrete -- `Bool`, `Byte`, `Char`, `Unit` -- could be narrower, and every
reader agrees about those because nobody can see them at a different type.

The alternative is MLton's and Rust's: width follows the *instantiation*, so
`Box Byte` is one byte of payload and `Box Int` eight. That needs every producer
of a `Box t` to be a layout-keyed copy too, and `check_layouts` inspects only
parameters today, not constructions or returns.

### A hole to close first

`fun mk(x : a) -> Box a = Box(x)` is not transparent and calls nothing that is,
so `layout.share` never copies it. Left generic past the specialization cap, it
stores its field `BOXED`, a pointer to a box, while a ground reader of `Box
Int` reads the same word as `i64`. That is FINDINGS 53's shape -- a field
written one way and read another -- and it is unverified. It is independent of
packing and should get a failing test before anything here is built on the
current invariant.

### Decisions for when this is done

* Declared-type layout or instantiation layout -- the section above.
* Whether tuples (kind 0, no declaration) and closure environments follow, and
  by which rule.
* The header: `count` as words scanned plus a byte size for the packed tail, or
  a per-constructor descriptor in place of per-object metadata.
* Selection: `Select.gob` has `Ldrb`/`Strb` and `W32`, and no `W16`.
* The runtime's hard-coded `ArrayStorage` slots and the closure shape.
* Measure first: what share of allocated bytes is `Bool`/`Byte`/`Char` payload
  on the boot workload. The proposal said this is not a performance argument,
  and it is not, but the cost of the change should be known before it is paid.

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
* Linear scan register allocation on SSA form, Wimmer and Franz, CGO 2010,
  <https://dl.acm.org/doi/10.1145/1772954.1772979>
* Greedy register allocation in LLVM 3.0, LLVM project blog,
  <https://blog.llvm.org/2011/09/greedy-register-allocation-in-llvm-30.html>
* Introducing the B3 JIT compiler, WebKit -- Air's IRC allocator and its line
  count against LLVM Greedy, <https://webkit.org/blog/5852/introducing-the-b3-jit-compiler/>
* Go's register allocator, read as source rather than as summary:
  `src/cmd/compile/internal/ssa/regalloc.go` (the algorithm's documentation)
  and `src/cmd/compile/internal/ssacompile/regalloc.go` (its 3,464 lines),
  with `ssacompile/flagalloc.go` for the flag-rematerialization pass,
  <https://github.com/golang/go/tree/master/src/cmd/compile/internal>
* QBE's allocator and spiller, likewise as source: `rega.c`, `spill.c`,
  <https://c9x.me/git/qbe.git>
