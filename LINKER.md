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

**mold, lld, gold** are the wrong budget by an order of magnitude and are worth
naming only to say so. mold links Chrome's 1.89 GB in 2.2 seconds against
gold's 53. None of that problem is our problem: we link one module with
seventeen undefined symbols.

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

**B is the fallback worth remembering.** If M28 runs long, emitting assembly
text is strictly less work than emitting objects and gets a working native
compiler sooner, at the cost of keeping the process spawn. QBE has shipped that
compromise for a decade.

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
