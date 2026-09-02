# Replacing the Python-source backend with llvmlite

Status: proposal

## Summary

Replace `turkey/pygen.py` with a native JIT backend built on llvmlite, while
keeping parsing, inference, elaboration, specialization, optimization, and the
checked Core IR unchanged.

The replacement should not lower Core directly to LLVM instructions in one
pass. It should introduce two explicit boundaries:

1. a small backend CFG that makes evaluation order, closures, joins, calls,
   pattern tests, and runtime operations explicit; and
2. a stable C-compatible runtime ABI for allocation, garbage collection,
   arrays, strings, panics, and output.

The resulting pipeline is:

```text
source
  -> checked Core
  -> monomorphized Core
  -> optimized Core
  -> backend CFG + closure conversion + layout selection
  -> llvmlite IR
  -> LLVM verification and optimization
  -> machine code
  -> turkey runtime
```

The backend should initially be an opt-in differential target. `turkey run`
should switch from `pygen` only after the native path passes the complete
conformance suite, preserves panic output, and has no known memory-safety
failures under stress.

## Motivation

`pygen` was the right first compiled backend. It proved several important
properties cheaply:

- optimized Core is executable rather than merely printable;
- `CJoin` and `CJump` can become labels and branches instead of recursive
  evaluator calls;
- strict left-to-right evaluation survives control-flow operands;
- closures distinguish snapshot captures from captured `CRef` cells; and
- the evaluator can remain a differential oracle.

It has now reached its natural boundary. Generated programs still use Python
objects for every record, constructor, array, cell, and closure; calls and
arithmetic still cross Python semantics; and the generated basic blocks are
rendered as a `_pc` dispatcher rather than native branches. LLVM can remove
those costs, but only after the language has an explicit runtime
representation. Replacing string rendering with calls to `IRBuilder` without
first settling that representation would move the hard problem rather than
solve it.

## Goals

- Execute `Checked.opt` as native code in the current Python compiler.
- Preserve all observable language behavior, including strict evaluation
  order, reference semantics, stack-safe joins, and panic frames.
- Keep Core, `coretc`, `mono`, `opt`, and `joins` independent of LLVM.
- Give residual polymorphism a terminating compilation strategy rather than
  requiring unlimited monomorphization.
- Make generated LLVM IR deterministic and inspectable.
- Keep the evaluator and, during migration, `pygen` as independent oracles.
- Establish a runtime ABI that the later bootstrap compiler can target without
  depending on Python or llvmlite.

## Non-goals

- A supported object-file or standalone executable format in the first
  release.
- Replacing the front end, Core optimizer, or dictionary-passing model.
- LLVM-specific optimization in Core.
- A production-grade optimizing garbage collector. A correct non-moving
  collector is sufficient initially.
- Debugger integration or full DWARF information. Source-accurate Turkey panic
  frames are required; native debugger metadata is not.
- Removing `eval.py`. It remains the semantic oracle.

## Current contracts that must survive

The new backend inherits contracts that are currently enforced across
`core.py`, `pygen.py`, and `tests/test_pygen.py`:

1. **Input is checked optimized Core.** The backend may assume `coretc` has
   accepted the program, but it must reject unsupported Core nodes loudly.
2. **Evaluation is strict and left-to-right.** This includes function and
   constructor arguments, record fields, array elements, assignment operands,
   and jump arguments.
3. **Jump arguments are simultaneous.** All arguments are evaluated before any
   join parameter is overwritten.
4. **Recursive joins are stack safe.** `CJoin`/`CJump` are native basic blocks,
   not calls, exceptions, or a program-counter loop.
5. **Closures snapshot immutable locals.** A closure made on each loop
   iteration retains that iteration's values.
6. **Mutable variables are shared cells.** Core already expresses this with
   `CRef`, `CDeref`, and `CAssign`; closure conversion must capture the cell
   pointer, not its contents.
7. **Type abstraction erases.** `CTyLam` and `CTyApp` affect layout selection
   and specialization but do not become runtime type objects.
8. **Panics unwind through Turkey call sites.** Bounds failures, division by
   zero, integer overflow, `error`, and failed matches produce the same ordered
   source frames as the evaluator. Optimizer-inlined calls must not invent
   frames.
9. **Top-level initialization order is observable.** Dictionaries are made
   available recursively, their fields are initialized, ordinary bindings run
   in program order, and `main` is called last when present.

These are acceptance criteria, not implementation suggestions.

### Primitive semantics are authoritative

`PRIMITIVES.md` (currently on the `prim-types` line of development) is the
backend-independent contract for `Int`, `Byte`, `Float`, `String`, `Char`,
`Bool`, and `Unit`. The LLVM backend must implement that document; it must not
infer semantics from Python's host types or choose convenient LLVM defaults.

In particular:

- `Int` is exactly signed 64-bit and ordinary arithmetic traps on overflow;
- `Byte` is unsigned 8-bit and `Array Byte` is packed;
- `Float` is IEEE 754 binary64 with no fast-math and no division-by-zero panic;
- `String` is well-formed UTF-8 bytes, not a character array;
- `Char` is a Unicode scalar value represented in 32 bits; and
- `Bool` remains a two-constructor Prelude ADT and `Unit` is zero-sized.

The primitive-semantics changes and their evaluator tests should land in the
LLVM branch before differential LLVM work begins. Until then, current `pygen`
behavior is not an oracle where it disagrees with `PRIMITIVES.md`.

## Proposed components

### `turkey/backend_ir.py`

Define a typed, machine-oriented CFG between Core and LLVM. It should be small
enough that its checker and printer are straightforward. A function contains
basic blocks; a block contains ordered instructions and exactly one
terminator.

Representative instructions are:

- constants, copies, box, and unbox;
- primitive arithmetic and comparison;
- heap allocation and field load/store;
- cell, array, and string operations;
- direct and closure calls;
- closure allocation and environment loads; and
- source-frame push/pop around calls that may panic.

Terminators are `return`, `jump`, conditional branch, tag switch, and panic.
Block parameters model Core join parameters and branch results. They lower to
LLVM `phi` nodes or edge stores, with one rule chosen consistently by the LLVM
emitter.

The CFG is valuable even though LLVM already has basic blocks. It permits unit
tests for evaluation order, closure capture, root placement, and pattern
lowering without executing machine code, and prevents llvmlite API details
from leaking into semantic lowering.

### `turkey/backend_lower.py`

Lower optimized Core into the backend CFG.

This pass performs:

- ANF-like sequencing of every compound operand from left to right;
- closure conversion;
- constructor and field layout lookup;
- pattern decision-tree construction;
- conversion of `CJoin` to blocks and `CJump` to edges;
- recognition of direct calls to known top-level functions and primitives;
- insertion of explicit boxing/unboxing at ABI boundaries; and
- insertion of GC roots and panic-frame operations at safepoints.

It should not perform general-purpose optimization. Core already owns
inlining, beta reduction, join discovery, and case simplification. The only
backend-local rewrites should be representation-driven, such as replacing a
known `Prim.intAdd` with integer addition or eliminating a redundant box/unbox
pair.

### `turkey/llvmgen.py`

Translate the checked backend CFG to `llvmlite.ir`, verify the resulting
module through `llvmlite.binding`, run a deliberately small LLVM optimization
pipeline, and JIT it for the host target.

Public functions should mirror the useful `pygen` boundary:

```python
def generate(program: CProgram, decls: DeclTable, main: str = "main") -> str:
    """Return deterministic textual LLVM IR."""

def compile(program: CProgram, decls: DeclTable, main: str = "main") -> NativeModule:
    """Return a live JIT module whose runtime and code remain owned."""

def execute(program: CProgram, decls: DeclTable, main: str = "main",
            filename: str = "<input>") -> None:
    """Compile and execute one program."""
```

`NativeModule` must retain the execution engine, loaded module, runtime state,
callback references, and any symbol storage for at least as long as generated
code may run. Returning a bare `ctypes` function pointer is unsafe because its
engine and Python callbacks could otherwise be collected.

llvmlite deliberately separates pure-Python IR construction from the binding
layer that parses and compiles textual LLVM IR. The implementation should use
that boundary instead of constructing LLVM text by hand. Its public API may
change between minor releases, so the project should pin and test one llvmlite
minor line rather than accept an unbounded dependency. See the
[llvmlite overview](https://llvmlite.readthedocs.io/en/latest/) and
[user guide](https://llvmlite.readthedocs.io/en/latest/user-guide/index.html).

### `runtime/`

Add a small C runtime with a versioned, C-compatible ABI. It should contain no
compiler policy and no Python object representation. The Python driver loads
it and registers its symbols with LLVM; the future bootstrap compiler can link
the same runtime.

The runtime owns:

- heap allocation and garbage collection;
- arrays, strings, cells, closures, records, and algebraic values;
- bounds and character checks;
- output primitives;
- panic creation and propagation; and
- static metadata for constructor names, field names, source locations, and
  heap tracing.

Keeping this in C rather than Python callbacks matters for both correctness
and performance. A callback-based prototype is acceptable for proving symbol
registration, but it must not become the production allocation or primitive
path.

## Runtime representation

### Layout classes

Map every Core type to one of a finite set of ABI layouts:

- `I64` for `Int`;
- `I32` for `Char`;
- `I8` for `Byte`;
- `F64` for `Float`;
- `PTR` for strings, arrays, records, algebraic values, cells, closures, and
  dictionaries; and
- `BOXED` for a value whose concrete layout is not statically available in a
  shared polymorphic body.

`Unit` is zero-sized and erased; a fixed zero value is used only where the
platform ABI requires a return slot. `Bool` remains an ordinary declared
constructor at the language and Core levels. Layout selection may lower any
two-nullary-constructor type to `i1` or `i8`; this is a structural layout rule,
not a name-based exception for `Bool`.

`BOXED` is a pointer to a runtime value containing a tag and payload. Concrete
callers box an `I64` or `F64` when entering shared polymorphic code and unbox
its result when the expected layout is concrete. Pointer-shaped values can be
wrapped or passed through according to one documented boxed ABI; there must be
no ambiguous pointer whose interpretation depends on the caller.

This design keeps the fast path native without making full monomorphization a
correctness requirement.

### Layout-keyed code sharing

The existing monomorphizer specializes by type on a budget. That cannot be the
only native compilation rule because polymorphic recursion admits an infinite
set of type instantiations.

After type specialization, discover function instances by **layout key**. A
key is the sequence of argument, capture, and result layouts after erasing
nominal types. Compile at most one body per key. All pointer-shaped
instantiations share a body, while integer, floating-point, and boxed generic
positions receive distinct bodies only when their ABI differs.

Discovery is a work-list fixed point over reachable functions and must have a
test demonstrating termination for the existing polymorphic-recursion case.
Dictionary values remain ordinary pointer arguments. If type specialization
did not remove them, shared code continues to receive or construct them at
runtime.

### Heap objects

Every heap allocation starts with a runtime header containing:

- object kind;
- mark state;
- payload size or layout descriptor; and
- constructor/type metadata when required for matching or printing.

Payloads are:

- **constructor/immutable record:** constructor tag followed by fields;
- **mutable record/dictionary:** fixed fields in declaration order;
- **array:** length, element layout, and element storage. `Array Byte` is
  packed at exactly one byte per element and `Array Char` at four bytes per
  element; a shared polymorphic array uses the boxed element representation;
- **string:** byte length followed by UTF-8 bytes;
- **cell:** one traceable value;
- **closure:** code pointer, arity/layout key, and captured environment; and
- **box:** scalar tag and payload.

Field offsets and constructor tags are assigned deterministically from
`DeclTable`, not Python hash order. Metadata retains the resolved internal name
for identity and the short source name for diagnostics.

String constructors validate UTF-8 exactly once at an untrusted boundary;
operations over an existing string preserve the invariant. Opaque
`String.Index` values contain byte offsets internally, and internal `Prim.*`
operations may therefore accept `Int` offsets. The public library ABI must
keep them behind the opaque index type: surface programs cannot construct or
do arithmetic on an offset. Decode and slice operations accept only scalar
boundaries produced by the string API.

There is no generic `Index String` or `Length String` lowering.
`String.byteLength` reads the stored byte length in O(1); byte and code-point
views remain lazy iterator values; equality and hashing operate on bytes; and
ordering is unsigned byte-lexicographic. Unicode normalization, collation,
case mapping, and grapheme segmentation are not implicit runtime operations.

### Closures and calls

Closure conversion lifts each `CLam` to a generated function and gives it an
ordered environment containing its free term variables. Captures use the
backend representation of their Core types. An immutable capture is copied;
a mutable variable is already a cell pointer and is copied as such.

Known top-level functions and lifted lambdas are direct calls. Escaping or
otherwise unknown functions use a closure call through a code pointer. The
closure ABI includes runtime state, the environment, and fixed arguments;
arity and layout are known from checked Core, so ordinary calls need no dynamic
arity check.

`CLetRec` requires two-phase construction: allocate all closure shells first,
then populate their environments. This gives mutually recursive closures
stable addresses before any capture points at them. Top-level recursive
functions use the same model or immutable global closure descriptors.

### Garbage collection

Use a stop-the-world, non-moving mark-sweep collector first. It is simple,
preserves object addresses used by closures and mutable records, and is enough
to make native execution correct. Allocation and explicitly annotated runtime
calls are safepoints.

Generated functions maintain exact shadow-stack root frames linked through
the runtime state. The first implementation may root every pointer-capable
local for the full function, provided slots are initialized before a
safepoint. A later liveness pass may shorten ranges. Roots include:

- live pointer and boxed locals;
- closure environments and arguments;
- partially initialized objects;
- top-level values and dictionaries; and
- the current panic object while unwinding.

The collector traces objects through static layout descriptors. It must not
scan the native stack conservatively; doing so would make behavior depend on
optimizer register allocation and would complicate the later bootstrap
backend.

## Lowering Core

| Core form | Backend lowering |
|---|---|
| `CLit`, `CUnit` | Width-exact native scalar/constant; allocate only when boxing is required |
| `CVar` | SSA value, root slot load, global, or environment load |
| `CCon` | Static nullary value or constructor closure |
| `CPrim` | Direct runtime symbol or recognized LLVM operation |
| `CTuple`, `CArray`, `CRecord` | Evaluate fields left-to-right, allocate, then initialize |
| `CField`, `CProject` | Typed offset load; mutable fields remain addressable |
| `CIndex` | Checked runtime access with the node's source location |
| `CLam` | Lifted function plus allocated closure environment |
| `CApp` | Evaluate callee then arguments left-to-right; direct or closure call |
| `CTyLam`, `CTyApp` | Erase after selecting a layout instance |
| `CLet` | Ordered instruction plus an SSA/local binding |
| `CLetRec` | Two-phase closure construction, then the body |
| `CJoin` | LLVM basic block with block parameters |
| `CJump` | Evaluate all arguments into temporaries, then branch |
| `CRef`, `CDeref`, `CAssign` | Cell allocation/load/store or typed aggregate store |
| `CIf` | Truth/tag test and conditional branch |
| `CMatch` | One scrutinee evaluation followed by tag/literal tests and bindings |

Pattern lowering should prefer an LLVM `switch` for constructor tags and use
ordered branches for literals and nested patterns. It must retain the source
arm order where patterns overlap and must route failure to the runtime panic
path even when exhaustiveness previously emitted only a warning.

## Primitives

Split primitives into two groups.

**Intrinsic lowering** covers integer and floating-point arithmetic,
comparisons, negation, boolean negation, and conversions that LLVM can express
without allocation. Its rules are fixed by `PRIMITIVES.md`:

- ordinary `Int` add, subtract, multiply, and negate use LLVM overflow
  intrinsics and take a cold panic edge on overflow;
- signed division and remainder check zero, and division also checks
  `minInt / -1`, before emitting `sdiv` or `srem`;
- the explicitly named wrapping operations use plain two's-complement
  operations without `nsw`/`nuw` assumptions;
- shifts validate that the count is in `0 .. 63`; left shift truncates to the
  low 64 bits and right shift is arithmetic;
- `Byte` has conversion, comparison, hashing, and bitwise operations but no
  arithmetic operations or numeric class instances;
- `Float` operations use strict binary64 instructions with the default
  round-to-nearest, ties-to-even mode and no fast-math flags;
- `Float` division does not branch on zero: IEEE infinity or NaN is the result;
- float comparisons use the exact ordered/unordered predicates needed for the
  specified NaN behavior; and
- conversion to `Int` checks NaN, infinity, and range before `fptosi`, which is
  otherwise poison outside its domain.

**Runtime calls** cover allocation, array operations, strings, character
validation, formatting, output, and `error`. Float formatting belongs here so
it can implement the specified shortest round-tripping spelling, including
`.0`, `Infinity`, `-Infinity`, `NaN`, and `-0.0`, rather than inheriting libc
or Python formatting.

The primitive table should describe name, Core type, lowering kind, runtime
symbol, panic behavior, and safepoint behavior in one place. `builtins.py` and
the native backend must not maintain unrelated spellings of the same primitive
set.

`Byte` conversion, bitwise operations, string byte length and decoding,
UTF-8 conversion, float bit conversion and total comparison, and checked float
conversion must all be represented in that table. Removed primitives such as
`Prim.stringLength` and `Prim.stringChars` must not acquire compatibility
lowerings in the native backend.

Literal lowering assumes the front end has already rejected an `Int` outside
the signed 64-bit range and a character escape outside the Unicode scalar
range. The backend CFG checker should nevertheless assert these ranges so a
malformed Core program cannot turn into truncated LLVM constants.

No floating-point instruction or LLVM optimization pipeline may add `fast`,
`nnan`, `ninf`, `reassoc`, `contract`, or equivalent flags, and the backend
must not introduce fused multiply-add. Differential float tests compare
results bit-for-bit where the primitive contract requires it.

## Panics and source frames

Do not use platform C++ exceptions. They interact poorly with JIT frames and
would make the runtime ABI platform-specific.

Use an explicit runtime panic state plus a nonlocal return mechanism at the
JIT entry boundary. Two viable implementations are:

1. a runtime `setjmp`/`longjmp` boundary with a shadow stack of Turkey frames;
   or
2. an internal `{ok, value}` result convention on calls that may panic.

The first is recommended initially because every primitive and user call can
keep a normal value ABI. Each generated call site that may transitively panic
pushes a static frame descriptor before the call and pops it after a normal
return. The runtime snapshots the active descriptors when a panic is raised.
The JIT entry converts the captured panic into `TurkeyPanic`, allowing the CLI
to keep its current reporting path.

This area needs a focused spike before broad code generation. Sanitizers must
confirm that nonlocal exit does not bypass required runtime cleanup. GC root
frames live in runtime-managed memory and are reset by the entry boundary on a
panic.

## JIT lifecycle

The first implementation is host-only JIT execution:

1. initialize the host target and verify executable memory is available;
2. construct an `ir.Module` with the host triple and data layout;
3. declare runtime symbols;
4. emit functions and static metadata;
5. parse and verify the textual module through the binding layer;
6. run a fixed optimization pipeline;
7. add the module to an execution engine and finalize it;
8. call a C-compatible entry function through `ctypes`; and
9. keep the engine and runtime alive until execution and all callbacks end.

llvmlite currently exposes MCJIT as its execution engine and provides an early
check for environments that disallow executable mappings; use that check to
produce a normal compiler error rather than a crash. See the
[execution-engine documentation](https://llvmlite.readthedocs.io/en/latest/user-guide/binding/execution-engine.html).

There is no machine-code cache initially. Front-end and LLVM compile time must
be reported separately in benchmarks so a fast warm loop does not hide an
unacceptable startup regression.

## CLI and dependency changes

During migration:

```text
python -m turkey run FILE                 # existing pygen path
python -m turkey run --backend llvm FILE  # native opt-in
python -m turkey llvm FILE                # print LLVM IR
```

After the acceptance gate:

```text
python -m turkey run FILE                 # LLVM by default
python -m turkey run --backend python FILE
python -m turkey python FILE              # retained temporarily for diagnosis
python -m turkey llvm FILE
```

Add llvmlite as a pinned runtime dependency only when the opt-in path exists.
Binary wheels bundle the compatible LLVM pieces, so users should not be asked
to install an arbitrary system LLVM. The project must test its supported
Python versions and host architectures explicitly; llvmlite versions target
specific LLVM versions and are not freely interchangeable.

## Verification strategy

### Static verification

- Check every backend CFG before LLVM emission.
- Parse and verify every generated LLVM module.
- Assert that every block has one terminator and every `phi` has one incoming
  value per predecessor.
- Assert that call-site and callee layout keys agree.
- Assert that every pointer-capable live value is rooted at each safepoint.
- Keep deterministic `.ll` golden tests for small, deliberately chosen Core
  fragments rather than for whole Prelude-heavy programs.

### Differential tests

Generalize `tests/test_pygen.py` into backend-independent tests. Every
successful conformance program should agree among:

- the evaluator;
- `pygen`, while it remains; and
- the LLVM backend.

Compare exact stdout and exact rendered panic messages. Add dedicated tests
for:

- left-to-right operands containing branches and matches;
- simultaneous jump arguments;
- 100,000+ iteration recursive joins;
- per-iteration closure snapshots;
- closures that mutate captured cells;
- mutually recursive local closures;
- top-level recursive dictionaries;
- allocation during partial object initialization;
- collection with cycles and unreachable closures;
- every pattern shape and non-exhaustive failure;
- array bounds and negative lengths;
- trapping and wrapping integer overflow, invalid shifts, division by zero,
  and `minInt / -1`;
- IEEE float zero division, NaN comparisons, signed zero, conversions, and
  exact display spelling;
- invalid Unicode scalars, UTF-8 validation, byte/code-point views, opaque
  string indices, and slicing at multibyte boundaries;
- packed `Array Byte` element addressing and GC tracing; and
- polymorphic recursion that terminates by layout sharing.

### Native safety tests

Build the runtime in CI with AddressSanitizer and UndefinedBehaviorSanitizer.
Run randomized allocation/collection stress with a collection on every
allocation. Test x86-64 and AArch64 on Linux and macOS before making LLVM the
default; add Windows only when the runtime's build and panic boundary have a
defined implementation there.

### Performance tests

Replace the current single ratio gate with separately reported measurements:

- Core-to-CFG lowering;
- LLVM IR construction and verification;
- LLVM optimization/code generation;
- cold end-to-end execution;
- warm execution; and
- peak heap use and collection time.

Keep the join loop and `bf.tl`, then add allocation-heavy, closure-heavy,
string-heavy, and polymorphic workloads. The correctness gate is mandatory;
performance gates should prevent gross regressions but should not encourage
unsafe removal of checks.

## Migration plan

### Phase 0: freeze semantics

- Extract backend-neutral parity helpers from `test_pygen.py`.
- Land the implemented `PRIMITIVES.md` changes and their evaluator tests from
  the `prim-types` development line.
- Record the complete primitive/runtime ABI inventory.
- Add panic, initialization-order, and closure-recursion cases missing from
  the current backend suite.

Exit criterion: evaluator and `pygen` agree on all newly pinned behavior.

### Phase 1: runtime and JIT spike

- Add the runtime header, allocator, root stack, and panic boundary.
- JIT one hand-written LLVM function that allocates, triggers collection,
  calls output, and panics with one source frame.
- Exercise it under sanitizers on x86-64 and AArch64.

Exit criterion: the ownership and unwinding model works independently of Core.

### Phase 2: backend CFG and scalar subset

- Implement and check `backend_ir.py`.
- Lower literals, lets, direct functions, primitives, `if`, joins, and jumps.
- Add `turkey llvm` and opt-in execution.

Exit criterion: scalar loop programs run natively, recursive joins do not grow
the stack, and printed IR is deterministic.

### Phase 3: heap values and patterns

- Add constructors, records, arrays, strings, cells, assignment, and matches.
- Enable GC stress mode.
- Match current panic messages and frames.

Exit criterion: all non-closure conformance programs pass three-way
differential tests and sanitizer runs.

### Phase 4: closures and residual polymorphism

- Add closure conversion and mutually recursive environments.
- Add layout-key discovery, shared boxed bodies, and ABI bridges.
- Add the polymorphic-recursion termination test.

Exit criterion: the full conformance suite and bootstrap interpreter pass.

### Phase 5: cutover

- Make LLVM the default `run` backend.
- Retain `--backend python` for one release or milestone.
- Update README, benchmarks, packaging, and CI support statements.
- Remove `pygen` only after the fallback has stopped finding unique bugs.

Exit criterion: two consecutive development milestones with no LLVM-only
correctness, leak, or crash regression.

## Rejected alternatives

### Emit LLVM directly while walking Core

This duplicates `pygen`'s hidden CFG and closure rules inside LLVM builder
state, making evaluation-order and rooting errors difficult to test. The
explicit backend CFG is a small cost for a checkable boundary.

### Keep Python objects as the native ABI

Calling the CPython C API or Python callbacks for allocation and primitives
would preserve behavior quickly, but it retains the GIL, ties generated code
to CPython object lifetimes, and gives the bootstrap compiler no reusable
runtime. It is suitable only for a throwaway symbol-resolution spike.

### Require complete monomorphization

It does not terminate for polymorphic recursion. A heuristic cap without a
shared representation turns a performance policy into a compile-time or
runtime correctness failure.

### Conservative stack scanning

LLVM may keep pointer-looking integers in registers and move real roots out of
visible stack slots. Exact shadow-stack roots are more work in the emitter but
give deterministic safety and a portable runtime contract.

### Use exceptions for joins or panics

Joins are ordinary branches in Core and should remain so. Platform exception
unwinding through JIT frames is unnecessary for panics and substantially
expands the ABI and portability surface.

## Open decisions

The following must be resolved by Phase 0 or the named spike:

1. The binary layout of `BOXED` values and whether pointer-shaped values need
   wrapper boxes at shared boundaries.
2. Whether block parameters lower to `phi` nodes or to edge stores in the
   first implementation.
3. The exact closure-call ABI for each layout key.
4. `setjmp`/`longjmp` versus an explicit panic result ABI, after the Phase 1
   sanitizer spike.
5. Supported host triples for the first default release.
6. The pinned llvmlite minor line and corresponding wheel availability for
   the project's supported Python and platform matrix.

None of these requires changing the surface type system. They are runtime and
backend choices behind the opaque representation promised by `design.md`.

## Acceptance criteria

The replacement is complete when:

- `turkey run` executes through llvmlite by default;
- every successful conformance program agrees exactly with the evaluator;
- every runtime-error golden has the same message and Turkey frame sequence;
- every `PRIMITIVES.md` semantic and representation test passes, including
  packed `Array Byte` and strict IEEE float code generation;
- recursive joins and tail-recursive discovered joins remain stack safe;
- GC stress, ASan, and UBSan runs are clean;
- layout discovery terminates on polymorphic recursion;
- warm native execution beats `pygen` on the join-loop and `bf.tl` benchmarks,
  with compile time reported separately;
- the emitted LLVM IR is deterministic and verifiable; and
- the runtime ABI is documented independently enough for the bootstrap
  compiler to target it.
