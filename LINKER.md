# Producing an executable without `cc`

Status: surveyed, not decided. Blocks nothing until M28.

`boot llvm` emits a module and one `cc` invocation turns it into a binary. M28
replaces LLVM with instruction selection and object emission, and the question
that then arrives is what stands between "we have machine code" and "we have a
file the kernel will run". This is the survey CLAUDE.md asks for before that
gets built, because the answer changes what M28 emits and is expensive to
reverse afterwards.

The short version: **"write a linker" is not one job, it is about eight, and
this project needs one and a half of them.** The compromises that make that
true are listed below, and they are mostly compromises this project has already
made for other reasons.

## What the platform forces

Measured on the development machine (arm64, macOS 26, Apple clang 17), not
assumed.

**A static binary is not available.** Apple ships no static `libSystem` and no
`crt0.o`, so every executable is dynamically linked. Cwerg's answer to this
whole question -- emit a static ELF and talk to the kernel by syscall -- is
simply not on the table here. It remains available on Linux.

**An ad-hoc code signature is mandatory.** The arm64 kernel rejects an unsigned
binary outright, which is why `ld` signs every arm64 executable it produces.
Anything that emits a Mach-O executable must also compute a `LC_CODE_SIGNATURE`
blob: SHA-256 over every page, plus a `CodeDirectory`. Zig implements this in
its self-hosted Mach-O linker and hit an edge case worth knowing -- it needs
enough padding between the load commands and `__text` to insert the load
command, and when there is not enough it gives up and defers to the system
linker.

So on this machine the floor is: a dynamically linked, code-signed Mach-O.
There is no simpler artifact.

## What we would actually have to link

Also measured. `boot`'s output for `adt.tl`, compiled to an object:

```
$ nm -u adt.o | wc -l
      17
```

Seventeen undefined symbols, and **every one of them is a `turkey_*` runtime
entry point**. Generated code never references libc directly -- the runtime
does, and the runtime is a separate translation unit.

Building the runtime as a dylib and linking against only that works today:

```
$ cc -nostdlib -o adt-min adt.ll libturkey.dylib -Wl,-rpath,@executable_path -e _main
$ ./adt-min          # correct output
$ dyld_info -fixups adt-min
    __DATA_CONST  __got  0x100008000  bind  libturkey/_turkey_array_new
    ... 17 rows ...
```

**Seventeen GOT binds, one flat table, no PLT stubs, no relocations between
objects, no archives.** The executable does not even need `libSystem` in its
own load commands; the dylib pulls it in.

That is the entire linking problem for this compiler, and it is why the job is
small. It is small *because of* choices already made, not by luck.

## What a linker does, and how much of it we need

| Job | Needed? | Why not |
|---|---|---|
| Parse many input objects | **No** | One module per program; `boot` already compiles whole programs |
| Archives (`.a`), member selection | **No** | Nothing to select from |
| Symbol resolution across objects | **No** | 17 externals, one provider |
| Relocation processing | **Barely** | We choose the layout, so most addresses are known at emission; only the GOT needs fixups |
| Section and segment layout | **Yes** | Trivial for one module: `__TEXT`, `__DATA_CONST`, `__DATA` |
| Dynamic bind metadata | **Yes** | Chained fixups, or the older bind opcodes |
| Code signature | **Yes, mandatory** | SHA-256 over pages; roughly 200 lines plus the hash |
| Debug info (DWARF) | **No** | See below |
| Dead stripping, ICF, LTO | **No** | Optimizations, not correctness |

Dropping DWARF costs less here than it would elsewhere, and that is worth
saying rather than assuming: the runtime already carries its own panic
backtrace through `PanicSite` and the frame table, so a Turkey-level stack
trace does not depend on debugger metadata. What is lost is `lldb` and
symbolized crash reports from the OS.

## Prior art

**Cwerg** is the closest peer and the one with a stated budget: *"10kLOC
(target independent code)"* and *"5kLOC (per target)"*, and it *"directly
generate[s] ELF executables for Arm32, Arm64 and X86-64 ISAs"* with no
dependencies -- no linker at all. It pays for that with an explicit list of
non-goals: *"no ABI compatibility except for simple cases and syscalls"*, *"no
plans to emit linkable object code"*, no shared libraries (which it notes
*"likely precludes Windows as a target platform"*), and DWARF ruled out because
*"full-blown dwarf debug info ... [is] unlikely to fit into the complexity
budget"*. The 5kLOC per target includes instruction encoding, which M28 needs
anyway; the ELF writing is a fraction of it.

Cwerg is the proof that this is affordable. It is also the proof that the
affordability comes from the non-goals, and one of its non-goals -- no dynamic
linking -- is not available to us on macOS.

**Zig** wrote a self-hosted Mach-O linker precisely because of Apple Silicon,
including ad-hoc signing. It is the existence proof for the macOS half and the
source of the padding caveat above. It is also much larger than this project's
budget, because it is a real linker: many objects, archives, incremental
linking, cross-compilation.

**QBE is the counterexample that matters most**, because this backend's design
is modeled on it. QBE emits **assembly text** and stops: `qbe -o out.s file.ssa
&& cc out.s`. The project that set this project's size budget does not emit
object files at all, let alone link them. If the argument were purely "what
does a small backend do", the answer would be: shell out.

**Go** is the most committed in-tree linker there is -- `cmd/link`, descended
from Plan 9, emitting ELF, Mach-O and PE itself -- and it contributes two
things this survey needed.

The first is a warning about the platform, not the linker. Go used to make raw
syscalls on Darwin; it no longer can. Apple *"[is] not willing to commit to a
particular syscall ABI"*, so Go's darwin/arm64 syscalls now go through
`libSystem` with trampolines converting the Go calling convention to C. **The
language that most wanted to avoid libc on macOS was made to link against it
anyway.** Cwerg's static-syscall route is not merely unavailable to us; it is
unavailable to everyone here.

The second is an actual number for the part that sounds worst.
`cmd/internal/codesign` implements ad-hoc Mach-O signing in **283 lines** --
`SuperBlob`, `Blob`, `CodeDirectory`, `CodeSigCmd`, SHA-256 over 4 KB pages --
self-contained apart from the hash. Go's internal linker signs darwin/arm64
binaries with it and they run.

And a caution: Go's internal linker handles *pure Go*, and **cgo forces
external linking**, with a trail of invalid-signature bugs from 2020 to 2023 at
exactly that boundary. That boundary is ours -- we have a C runtime -- which is
the strongest argument for the dylib: it keeps the C on the far side of a
`LC_LOAD_DYLIB` instead of mixing C objects into our own link.

**TCC** is *"meant to be self-relying: you do not need an external assembler or
linker"*, and emits relocatable ELF, executable ELF and dynamic ELF libraries.
Existence proof that the whole path fits in a famously small compiler.

**Delphi** inverts the expectation usefully: its **desktop** compilers use an
internal linker, and only the iOS-device and Android ones shell out to `ld`.
The platforms that force an external linker are the locked-down ones.

**Free Pascal** occupies the middle position and is worth naming because it is
a position: it has *"its own binary object writer"* but still calls `ld`. Emit
your own objects, let someone else link them -- option D below, shipped.

**mold, lld, gold** are the wrong budget by an order of magnitude and are worth
naming only to say so. mold links Chrome's 1.89 GB in 2.2 seconds against
gold's 53. None of that problem is our problem: we link one module with
seventeen undefined symbols.

## Assembly text is not a fork in the road

The obvious worry about option B is that emitting `.s` now makes emitting bytes
later a rewrite, or forces a disassembler to check the bytes against. Both
dissolve, but only under a design constraint worth writing down.

**Instruction selection must produce a machine-instruction *value*, never
text.** `NATIVE-BACKEND.md` already requires this -- selection produces a second
instantiation of the CFG over an `Arm64` instruction type rather than rewriting
the low IR in place -- and it is what makes the question moot. Assembly text and
machine bytes are then two *consumers* of one IR:

* a `Show` instance, one line per instruction;
* an `encode` function, one line per instruction.

Neither is a prerequisite for the other, and neither touches the selector, the
register allocator, or the CFG. Switching is adding a second consumer, not
replacing a design. The danger the worry names is real *only* if selection
emits strings directly -- so the rule is: it must not, and this is the second
reason for that rule after the exhaustiveness argument.

**And the disassembler is not needed, because `as` is the oracle.** For any
instruction the printer can spell, the system assembler gives the ground truth
bytes:

```
$ printf '.text\nadd x0, x1, x2\nsub w3, w4, #7\nldr x5, [x6, #16]\nret\n' > enc.s
$ as -arch arm64 -o enc.o enc.s && otool -t -X enc.o
0000000000000000  8b020020 51001c83 f94008c5 d65f03c0
```

`add x0, x1, x2` is `8b020020`. So the encoder is checked by printing each
instruction, assembling it, and comparing bytes -- instruction by instruction,
automatically, over every form the selector can produce. That is this project's
existing method, differential testing against a second implementation, applied
to the one part of a backend that is hardest to get right and that Cranelift
says needs a fuzzer.

Which turns the fallback into a recommendation: **build the text printer
regardless**, because it is the encoder's test fixture. It costs a `Show`
instance, it makes `boot`'s output readable while the selector is being
debugged, and it is the difference between an encoder that is believed and one
that is checked. Whether the *shipping* path goes through it is then a separate
and much smaller question.

## The options

**A. Keep `cc`.** What happens today. Zero new code. Costs a C toolchain at
every build and a process `boot` cannot start itself.

**B. Emit assembly text, shell out to `cc` or `as`+`ld`.** QBE's answer. M28
emits `.s` instead of object code, which is *easier* than object emission --
no encoding tables, no Mach-O. Still needs a process spawn, so it does not
remove the dependency, but it makes M28 smaller.

**C. Emit a Mach-O executable directly, runtime as a dylib.** The measured
shape above: segments, load commands, 17 chained-fixup binds, an ad-hoc
signature. No `cc` at run time. Estimated 800–1500 lines of Turkey on top of
the instruction encoding M28 needs regardless -- a guess anchored on Cwerg's
5kLOC-per-target including its assembler.

**D. Emit a relocatable object, shell out to `ld`.** The worst of both: the
full Mach-O writer *and* an external process, plus real relocation records
because the addresses are no longer ours to choose.

## What none of these remove

**The runtime is C.** `turkey_runtime.c` is 1,200 lines of C with the
collector, the string and array primitives, and the panic machinery. `boot`
cannot compile C, so a C compiler is required to produce the runtime whichever
option is taken.

Option C therefore does not make the toolchain self-contained; it moves the C
dependency from *every build* to *whenever the runtime changes*, with a
prebuilt `libturkey.dylib` shipped alongside. That is a real improvement and it
is not the improvement it might sound like, and the distinction should be made
before the work is costed rather than after.

The alternative -- rewriting the runtime in Turkey -- is a much larger question
than this document, and it runs into the same floor: something must make
syscalls, and on macOS that something must be `libSystem`.

## Recommendation

**Not yet, and probably C when it is time.**

Nothing here blocks M28. Instruction selection, register allocation and
encoding are the milestone, and they are the same work whether the output is
assembly text, an object file, or an executable -- so the decision can be
deferred until they exist, at which point the cost of the last step is a much
smaller unknown.

When it is time: **C**, on the strength of the measurement. Seventeen GOT binds
against one dylib is not a linker, it is a file format writer with a hash in
it, and the jobs that make linkers big are all jobs this compiler has already
opted out of. The honest caveats are that it is macOS-specific work that a
Linux target would need again differently (though Linux is *easier* -- Cwerg's
static-ELF route reopens there), and that it does not remove the C toolchain,
only its position in the build.

**B is not a fallback, it is a stage.** The assembly printer should exist
whatever ships, because it is how the byte encoder gets an oracle -- see above.
If M28 runs long it is also a working native compiler sooner, at the cost of
keeping the process spawn, which QBE has shipped for a decade.

**Order of work, then.** Selection and allocation over a machine-instruction
IR; a `Show` for it; an `encode` checked against `as` instruction by
instruction; and only then the Mach-O writer and the signature, by which point
the only untested thing left is the file format.

## Sources

- Cwerg backend README, budget and non-goals:
  <https://github.com/robertmuth/Cwerg/blob/master/BE/README.md>
- QBE, output format: <https://c9x.me/compile/>
- Zig, code signing in the self-hosted Mach-O linker:
  <https://github.com/ziglang/zig/issues/7103>
- Zig, hot-code reloading on macOS/arm64 (padding and signing caveats):
  <https://www.jakubkonka.com/2022/03/16/hcs-zig.html>
- lld, adding arm64 macOS code signing: <https://reviews.llvm.org/D96164>
- mold: <https://github.com/rui314/mold>
- Go, forced onto libSystem for Darwin syscalls:
  <https://github.com/golang/go/issues/17490>
- Go, `cmd/internal/codesign` (283 lines of ad-hoc Mach-O signing):
  <https://tip.golang.org/src/cmd/internal/codesign/codesign.go>
- Go, cgo and invalid signatures on darwin/arm64:
  <https://github.com/golang/go/issues/43105>
- TCC, self-relying (no external assembler or linker):
  <https://bellard.org/tcc/tcc-doc.html>
- Delphi, internal linker on desktop and external `ld` on mobile:
  <https://docwiki.embarcadero.com/RADStudio/Athens/en/Linking>
