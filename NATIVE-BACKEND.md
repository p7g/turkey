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

  **More than eight arguments in one register file does not work, either side
  of the call.** AAPCS64 passes the ninth argument and beyond on the stack, and
  there is no frame to put it in -- the prologue that would is the same pass
  that has still to assign a register to anything. The two sides fail
  differently and only one of them is safe:

  * **The caller stops and says so.** `Turkey.Select.call` asks `general >=
    len(A.argRegs)` *before* taking the register and reports "a call with more
    than eight arguments in one register file, which would need stack
    arguments". Until it did, the check ran one line after the array index and
    a nine-argument call panicked inside the compiler -- `array index out of
    bounds: read at index 8, length 8`. No corpus program had a function of
    more than eight parameters, so nothing found it; `tests/programs/manyargs.tl`
    exists now for that reason, and the callee has to be *recursive*, because
    `opt` inlines and folds a call with constant arguments.
  * **The callee is silent, which is the part to fix first.** `start` copies
    `f.params` through as ordinary virtuals and nothing binds them to the
    incoming ABI at all, so a nine-parameter *function* selects cleanly and
    means nothing. There is no diagnostic because there is no code -- the
    incoming convention is established by the prologue, which does not exist.
    A stop on the caller and silence on the callee is not a consistent state;
    it is safe only because nothing downstream consumes the result yet.

  Measured rather than assumed. A module holding a nine-parameter `sum9` and a
  `main` that calls it with constant arguments reports **`selected 9 of 9
  functions`** and no complaint at all: `opt` inlines and folds the call away,
  so the caller check never runs, and `sum9` survives as a top-level function
  whose nine parameters are printed as ordinary virtuals -- `fun @Main#sum9(%0:
  i64, ..., %8:i64)`. Nothing in the pipeline says this function has no
  callable entry sequence. That is the shape to remember: the caller's stop is
  a real diagnostic, and it is also the reason the callee's silence looks
  covered when it is not.

  `tests/test_select.py` holds the caller half as a ratchet: `KNOWN_REASONS`
  has exactly this one entry, a reason outside the set fails the run, and
  `stopped == 2` is asserted exactly rather than as a bound so that the number
  moving is something a person looks at. Closing this deletes the entry, the
  assertion and this paragraph together.
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

  **Status: the corpus colours completely; `boot` does not yet.**

  | | coloured | out of registers |
  |---|---|---|
  | the corpus | **1888 of 1888** | none |
  | `boot` compiling itself | 2773 of 2929 | **156** |

  Zero complaints from `verifyColouring` on either, which is the check that
  matters: a colouring putting two simultaneously live values in one register
  prints, assembles, links, runs, and computes a wrong answer, and nothing
  downstream can see it.

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

  **The function boundary has no calling convention at all yet, on either
  side.** Reading the code to write the prologue turned this up, and it is
  wider than "there is no prologue":

  * `Select.start` copies `f.params` through as ordinary virtuals. Nothing
    binds parameter *i* to `x`*i*. This is the callee half of the
    stack-argument gap recorded under phase 4, and it is not specific to nine
    parameters -- a *one*-parameter function has no incoming convention either.
    It has been invisible because no consumer of the selected code exists yet.
  * `Term.Ret(v)` names a virtual and nothing moves it to `x0`.

  So the "corpus colours 1888 of 1888" above is a statement about the
  *interior* of each function. The edges are missing, and they are what the
  prologue slice has to add. The shape is a copy in and a copy out: fresh
  values pinned to `x0`-`x7` and `d0`-`d7` at entry and to `x0` at exit, with
  an ordinary `mov` between them and the body's values, which the colourer's
  hinting should then coalesce away in the common case. Pinning is what the
  colourer gains for it -- a value whose register is fixed before the walk
  begins -- and it is the one thing in this design that can *fail* rather than
  merely allocate badly, because a parameter pinned to `x0` and live across a
  call has a contradiction the copy exists to break.

  **`boot` needs stack arguments to compile itself.** The same run reports four
  calls stopped for more than eight arguments in one register file, in the
  compiler's own source. The gap recorded under phase 4 is not a hypothetical
  a test program invented; it is on the path to M29.


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
