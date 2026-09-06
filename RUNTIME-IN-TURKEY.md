# Replacing the C runtime

Status: surveyed, not decided. Nothing depends on it; M28 and M29 do not.

`LINKER.md` ends by noting that no way of producing an executable removes the C
dependency, because `runtime/turkey_runtime.c` is C and `boot` cannot compile C.
This asks the follow-on question: could pointer primitives and a small FFI let
the runtime be written in Turkey instead?

**Yes, and it is three separable pieces of very different difficulty, and the
expensive one is not the one it looks like.** It is also worth being precise
about what it buys, because it is not "no libc".

## What the runtime is

Measured, by section:

| Section | Lines | What it needs beyond ordinary Turkey |
|---|---:|---|
| Values: strings, objects, arrays, boxes, closures | 546 | Raw loads and stores at an address |
| GC: heap list, mark, sweep, root frames | 310 | Raw memory, **and must not allocate** |
| The outside world: args, files, print, exit | 205 | Nothing but FFI |
| Entry and the big-stack thread | 106 | FFI, and a Turkey function as a C callback |
| Crash diagnostics | 54 | Signal handlers, so also a C callback |
| **Total** | **1221** | |

And the whole libc surface it uses is about thirty-five functions:
`malloc` `free` `realloc` `memcpy` `memmove` `memset` `snprintf` `fprintf`
`fputs` `fwrite` `fread` `fopen` `fclose` `read` `write` `strlen` `strtod`
`getenv` `exit` `signal` `floor` `ceil` `round` `trunc` `fmod` `remainder`
`isnan` and the five `pthread_*`.

Thirty-five is a small FFI. That is the encouraging half.

## What it does *not* buy

**libc stays.** An FFI to libc is still a dependency on libc, and on macOS that
was never optional anyway -- `LINKER.md` establishes that there is no static
`libSystem`, and that Go, which wanted to avoid it more than anyone, was made to
route its Darwin syscalls through it. So this does not remove a library. It
removes *our own C*, which is a different and smaller claim:

* no C **compiler** in the build;
* one language for the whole system;
* `boot` compiling every line it depends on, which is what self-hosting means
  in the strong sense.

Worth stating plainly before costing it, because "get rid of the C part" can
sound like it means shedding a dependency and it means shedding a *toolchain*.

## What the language would need

**1. Raw memory.** A pointer that is not a managed reference, and load/store at
a given representation with pointer arithmetic. This is a new kind of value that
the collector must *not* trace -- which `Rep` already expresses: `traced` is
exactly the bit, and `Ptr` untraced already exists in the IR and is currently
unused. That is a piece of luck worth noticing; the low IR was built with the
distinction in it.

**2. An FFI.** A declaration form for a foreign function, a mapping from Turkey
types to C ones, and a direct call at the C ABI. The backend already emits
`Call(Runtime(name), args)` to exactly these symbols, so the *emission* is
done -- what is missing is the surface syntax and the type mapping.

**3. A low-level subset -- and this is the expensive one.** The collector runs
when nothing may allocate, and has no roots of its own because it *is* the root
scanner. So some functions must compile with no root frames, no allocation, and
no implicit anything. Declaring that is easy. **Enforcing it is a whole-program
analysis**, and that is the real cost.

**4. Callbacks.** A Turkey function usable as a C function pointer, for the
signal handler and the thread entry. Needs the non-GC convention from (3).

## Prior art

**Go** is the most honest estimate of (3), because it has exactly this problem:
a precise, root-based collector written in the collected language. The cost
shows up as a vocabulary of restrictions -- `//go:nosplit`,
`//go:nowritebarrier`, `//go:nowritebarrierrec`, `//go:yeswritebarrierrec`,
`//go:notinheap` -- **and a compiler pass that floods the call graph to check
them**: *"the compiler floods the call graph starting from each
`go:nowritebarrierrec` function and produces an error if it encounters a
function containing a write barrier, and this flood stops at
`go:yeswritebarrierrec` functions."*

That is the shape of the bill. Not the pointer primitives, which are a
week -- the *verification* that runtime code obeys restrictions the rest of
the language does not have, transitively, at compile time. Go's own
`runtime/HACKING.md` exists because those rules cannot be inferred from
reading the code.

**RPython** writes PyPy's GC *in RPython*: *"this is not reference counting; it
is a real GC written as more RPython code"*, made to work by a **GC transformer
that rewrites the flow graphs of everything else** while the collector itself
stays outside the transform. Structurally the same answer as (3): a subset that
the managed-code transformation does not apply to.

With a caveat that matters here: **RPython translates to C.** So does Squeak's
VM, written in Slang, a Smalltalk subset, and then translated to C. Both prove
the *subset* idea and neither escapes the C toolchain -- they generate C rather
than depending on someone else's. If the goal is "no C compiler", these are not
precedents for it.

**Oberon** is the precedent that actually is one: Wirth's system, collector
included, in Oberon, compiled by its own compiler, no C anywhere. It is also
from an era with a much smaller platform contract to satisfy -- no dyld, no
code signature, no libc ABI.

**Zig** has no GC, so it answers (1) and (2) and says nothing about (3), which
is the part that decides this.

## Staging, and where to stop

The four sections come apart, and conveniently in increasing order of
difficulty. Each is independently shippable and the sequence can be abandoned
at any point with the rest still in C.

1. **The outside world, 205 lines.** Pure FFI: no raw memory, no allocation, no
   GC interaction. Moving it proves the FFI on real code and removes a sixth of
   the C.
2. **Values, 546 lines.** Needs raw loads and stores but allocates normally, so
   it is ordinary managed Turkey with a pointer type. The largest section and
   the second easiest.
3. **Entry and crash diagnostics, 160 lines.** Needs callbacks.
4. **The collector, 310 lines.** Needs the low-level subset and its
   enforcement. Last, and the only one that requires (3).

Stopping after 1 and 2 leaves 470 lines of C -- the collector, the entry, the
signal handler -- and is 60% of the way with none of the hard machinery.

## The risk worth naming

The collector manages roots that this compiler emits, and writing it in the
language whose roots it manages closes a loop. This session took the native
backend from **0 of 28 to 28 of 28** under `TURKEY_GC_STRESS=1` by finding four
separate kinds of missing root, three of which no amount of reading suggested.
A collector written in Turkey would be subject to the same emission it depends
on, and `GC_STRESS` -- currently the thing that catches everything -- would be
testing the collector with the collector.

That is not an argument against doing it. It is an argument for doing it *after*
there is a second way to check the collector, and for keeping step 4 last.

## Recommendation

**Split the question, and do the cheap half on its own merits.**

**An FFI is worth having regardless of the runtime.** Turkey today cannot call C
at all, which is a limitation of the language and not of the runtime. Justify it
as a language feature, size it against the thirty-five functions above, and the
runtime rewrite becomes a *user* of it rather than the reason for it. That also
makes it testable independently -- a program that calls `getenv` is a test, and
does not need a collector rewritten first.

**The low-level subset should wait for a reason beyond this.** It is the piece
that pays for the collector and nothing else, its true cost is a whole-program
enforcement pass on Go's evidence, and the collector is the one section where
being wrong is silent. Neither M28 nor M29 needs it: stage1, stage2 and stage3
can all link a prebuilt `libturkey.dylib`, and self-hosting in the sense the
plan means -- `boot` compiling `boot` -- is unaffected by which language the
collector is in.

**Raw pointers are the ordering constraint.** They are a prerequisite for both
halves and are the smallest of the three pieces, and the `traced` bit that makes
them expressible is already in the IR. If any of this is done, it is first.

## Sources

- Go runtime pragmas and the call-graph flood that enforces them:
  <https://github.com/golang/go/blob/master/src/runtime/HACKING.md>
- Go's hidden pragmas: <https://dave.cheney.net/2018/01/08/gos-hidden-pragmas>
- RPython, a GC written in RPython and inserted by a flow-graph transformer:
  <https://rpython.readthedocs.io/en/latest/garbage_collection.html>
- PyPy architecture, translation to C:
  <https://aosabook.org/en/v2/pypy.html>
