# The native backend, in Turkey

Status: proposal

## Summary

`boot` owns the whole path from optimized Core to machine code: an SSA IR, the
optimizations over it, instruction selection, register allocation, and object
emission. Written in Turkey, in `boot/`, and nothing above it changes -- the
front end, the elaboration, `mono`, `opt` and layout sharing are already two
implementations that agree, and this proposal does not touch them.

The Python implementation's backend (`turkey/backend_ir.py`,
`backend_lower.py`, `llvmgen.py`) is left as it is. It is a JIT: it will never
do register allocation or instruction selection, so hardening its IR to prepare
for those is work on the wrong artifact. It stays the differential oracle for
everything above Core, and takes bug fixes only.

The design's organizing decision is a line between what must never be rewritten
and what is expected to be replaced:

```text
optimized Core
  -> Turkey.Ssa                 durable: the IR, its verifier, its printer
  -> optimizations over it      durable
  -> ...
       -> LLVM IR text          replaceable: the first emitter
       -> machine IR            the second emitter: isel, regalloc, encoding
```

Everything above the last arrow is written once. The first emitter exists so
the durable half is exercised, and wrong where it is wrong, long before a
register allocator exists to confuse the question.

## Goals

* An SSA IR that instruction selection and register allocation can be built on
  without changing its shape.
* Precise garbage collection through stack maps rather than through rooting
  every pointer (FINDINGS 55).
* Optimizations that Core cannot express, at the level where they are cheap.
* A verification story that does not depend on the Python backend's design.
* Native code for one target, with LLVM remaining available.

## Non-goals

* Changing the Python backend, beyond fixes.
* Matching the Python backend's IR, its value names, or its emitted LLVM.
* Multiple targets before one works.
* Replacing `opt`. Inlining, case-of-known-constructor and join specialization
  happen on Core and stay there; the SSA level does the machine-level ones Core
  cannot see, and redoing either at both levels is how the two disagree.

## What the Python backend got right, and what it did not

`turkey/backend_ir.py` is 253 lines and worth reading before designing
anything, because one of its decisions is the one that would have forced a
rewrite and it was made correctly.

**Block parameters rather than phi nodes.** `Block.params` with
`Jump(target, args)`, which is Cranelift's form, Swift SIL's and MLIR's. It
matters more than anything else here: a jump carrying arguments *is* a parallel
copy at a named point, which is exactly what going out of SSA and allocating
registers need. Phi nodes put the copy on an edge that has no place to live,
and every allocator that meets them has to reconstruct what block arguments
state directly. This proposal keeps it unchanged.

Three things do not carry over, and each is in the layer that must not be
rewritten later:

* **SSA is block-local.** `_check_function` rebuilds `local` per block, so a
  value defined in one block cannot be used in another; anything crossing an
  edge goes through `Function.slots`, which are memory. Under that rule
  promoting memory to registers is mandatory rather than an optimization, and
  every pass that wants to move a computation starts by undoing the lowering's
  own work.
* **An opcode is a string.** `Instruction(op="prim.arrayGet.i64", ...)`, with
  every rule about what an opcode means -- its arity, its operand layouts, its
  result -- living in `llvmgen` rather than beside the IR. LLVM re-verifies
  downstream, so this costs nothing today. Instruction selection is a match on
  opcodes, and a string cannot be matched exhaustively.
* **There is no effects model.** `Instruction` carries `diverges` and nothing
  else. Reordering, common-subexpression elimination and code motion all need
  to know what an instruction reads, writes and allocates -- and so does
  garbage collection, which is why the backend roots every pointer that is live
  anywhere in a function into one array sized to the function's worst case.
  `Turkey.Opt#expr` carries 481 of them.

## Proposed components

### `boot/Turkey/Ssa.tl` -- the IR

Values are a number and a *representation*; blocks take parameters; a function
is a set of blocks with an entry.

```
type Rep = Rep {
    -- What a register has to hold. Instruction selection reads this.
    class_ : RepClass,          -- I1 I8 I32 I64 F64 Ptr
    -- Whether the collector must find this value at a safepoint. `Ptr` and
    -- `Boxed` are one machine class and two different obligations, and the
    -- Python backend spells the distinction as two members of one enum --
    -- which works until something asks a register class about a `Boxed`.
    traced : Bool,
}
```

An instruction is an opcode, operands, an optional result, and a source
position for panics.

**The opcode is an ADT.** This is the concrete reason to design rather than
port: Turkey checks a `match` for exhaustiveness, so adding an opcode becomes a
compile error in the verifier, the printer, every optimization and the
selector. A string cannot do that, and the passes that would have to be found
by hand are exactly the ones whose omissions are silent miscompiles.

Beside the ADT, and derived from it by one `match` each:

* `signature(op) -> (Array Rep, Option Rep)` -- what the verifier checks.
* `effects(op) -> Effects` -- `Pure`, `Reads`, `Writes`, `Allocates`,
  `Diverges`. `Allocates` implies *may collect*, which is what makes an
  instruction a safepoint.

### The verifier

Run after every pass, as `bir.check` already is. It checks what the Python one
checks -- block termination, jump arity and representation, branch conditions,
return representation -- and two things it cannot:

* **Every use is dominated by its definition.** This is the function-wide SSA
  rule, and having it is what lets the lowering stop routing values through
  memory.
* **Every instruction matches its opcode's signature.**

Dominance is computed here anyway, and the optimizations and the allocator both
need it, so it is a shared analysis rather than a checking cost.

### `boot/Turkey/SsaLower.tl` -- Core to SSA

The *logic* of `turkey/backend_lower.py` -- closure conversion, pattern tests,
join points, layout selection, the calling convention -- is correct and hard-won,
and is the part to carry across. Its IR is not.

Two differences follow from function-wide SSA. Values that cross an edge become
block arguments or plain dominating definitions rather than slots, so slots are
left for what actually is mutable. And a safepoint is emitted where an
allocating instruction is, rather than a root frame being opened for the whole
function.

`turkey/backend_lower.py` is 1,579 lines doing closure conversion, layout
selection, pattern lowering and rooting in one pass. The port should split
those; that is a decision about a pass and does not reach the IR.

### `boot/Turkey/SsaOpt.tl` -- optimizations

In dependency order, each behind the verifier:

* simplify-CFG, copy propagation, constant folding, dead code elimination --
  the ones that pay for themselves immediately and clean up after lowering;
* global value numbering, and sparse conditional constant propagation;
* loop-invariant code motion, which needs loop nesting;
* bounds-check elimination and load forwarding through the record and array
  opcodes, which are the ones this program is actually made of.

Not inlining, and not case-of-known-constructor. Core does those, `opt` is two
implementations that agree about them, and a second opinion at this level is a
disagreement waiting to be found by a golden.

### `boot/Turkey/Llvm.tl` -- the first emitter

SSA to LLVM IR text. No library: llvmlite is a Python binding and boot has no
Python, so it prints `.ll` and hands it to `clang`. Textual output is also
diffable and readable when it is wrong, which is the reason `turkey llvm`
prints text rather than emitting through a builder.

This is what makes `boot` self-sufficient, and it is where the durable half
gets exercised. It is expected to remain, as a reference and a fallback.

### `boot/Turkey/Machine.tl` -- the second emitter

After instruction selection: target instructions, physical and virtual
registers, and the shape a register allocator wants. Derived, narrow, generated
per target, and carrying no optimizations of its own beyond peepholes.

Two IRs rather than one because every backend of this kind converges on two --
LLVM IR and MachineIR, Cranelift's CLIF and VCode, Go's SSA and obj -- and
because the alternative is the rewrite this document exists to avoid. A single
IR that is target-independent enough to optimize and target-specific enough to
allocate registers over does not stay one IR; it becomes two, later, under
worse conditions.

**Register allocation** is a live-range-splitting allocator over SSA, in the
Wimmer-Franz and `regalloc2` family. It needs dominance, loop nesting depth for
spill costs, and precise liveness -- all of which the IR above already
provides. Going out of SSA turns block arguments into parallel copies on edges,
resolved with the usual cycle breaking (Boissinot et al.); block arguments are
what make those copies land somewhere real.

**Stack maps** fall out of the allocator, because it is the only pass that
knows where a value lives at a given point. At each safepoint it records which
registers and spill slots hold traced values, and the runtime walks that
instead of a root frame. That is the fix for FINDINGS 55, and it is the reason
`Allocates` has to be in the IR from the first commit: retrofitting precise
stack maps into a backend that did not plan for them is precisely the rewrite
this design is trying not to need.

## Verification

This is the part that changes, and it should be said plainly rather than
discovered later.

Every stage so far was verified by a byte-identical diff against the Python
implementation. A backend designed independently has no such oracle, and should
not have one: diffing boot's IR against a JIT's IR would couple boot's design
to the artifact this proposal is decoupling from.

The observable that matters for a backend is not its IR. It is what the
compiled program does.

* **Differential execution.** Compile every conformance program with `boot`,
  run it, and compare stdout, exit status and panic trace against the Python
  implementation running the same program. `tests/programs/*.expected` and
  `tests/test_system.py` are already exactly this, for the other host.
* **The verifier, after every pass.** A miscompile becomes a rejected program
  at the pass that caused it rather than a wrong answer at the end.
* **Two emitters, one IR.** While the LLVM emitter is there, a program can be
  compiled twice from the same SSA and the two runs compared -- which tests the
  emitters against each other and the IR against neither.
* **M26 is unchanged and gets stronger.** stage2 against stage3 is one Turkey
  program compiled by two different hosts, which is what catches a divergence
  no single implementation can see.

There is a real loss here and it is worth naming: a textual diff localizes a
bug to a line, and differential execution localizes it to a program. The
verifier and the two-emitter check are what buy that back, and the Core-level
goldens still cover everything above this boundary.

## Migration

Each phase is runnable and verified before the next begins.

* **Phase 0.** `Turkey.Ssa`: the IR, the opcode ADT with its signature and
  effects, the verifier, the printer.
* **Phase 1.** Core to SSA. Verified by the verifier and by reading the dump;
  nothing executes yet.
* **Phase 2.** SSA to LLVM text, and `boot build`. The conformance suite runs
  under differential execution. **`boot` is self-sufficient at this point**,
  and the durable half is exercised.
* **Phase 3.** The optimizations, one at a time, behind the same oracle and
  measured against phase 2.
* **Phase 4.** Machine IR and instruction selection, for one target.
* **Phase 5.** Register allocation, stack maps, encoding, object emission. The
  LLVM path stays.

## Open decisions

* **The first native target.** arm64, on the argument that it is what this is
  being developed on and a backend that cannot be run is not being tested.
* **Whether LLVM remains permanently.** Recommended: yes, as a reference
  emitter. It is what phase 5 is differentially tested against.
* **Whether `turkey/`'s backend is kept in step at all.** Recommended: no. It
  is the oracle for Core and above, and freezing it is what makes it one.

## Rejected alternatives

### Port `turkey/backend_ir.py` and extend it

Three retrofits, all in the layer that must not be rewritten: block-local SSA
makes memory promotion a precondition for every optimization, string opcodes
cannot be selected on exhaustively, and no effects model means no precise stack
maps. Each is cheaper to do now than after a lowering, an optimizer and a
selector have been written against the old shape.

### One IR from Core to machine code

No backend of this kind does this. The pressures are opposite -- an optimizer
wants target independence and an allocator wants physical registers -- and the
IR that tries to be both becomes two anyway, later, with more code depending on
the shape that has to change.

### Diff boot's IR against the Python backend's as the oracle

It would couple boot's design to a JIT's: every place boot's IR is better would
show up as a diff to be suppressed. The behaviour of the compiled program is
the stronger check and constrains nothing.

### Skip LLVM and go straight to instruction selection

The SSA IR and its optimizations would then have no oracle at all until a
register allocator existed, and every bug in either would present as a
miscompile with nothing to compare against. Phase 2 costs one emitter and pays
for itself across phases 3 to 5.

### Conservative stack scanning instead of stack maps

Already rejected in `LLVM-BACKEND.md`, for reasons that have not changed, and
the collector is precise today.
