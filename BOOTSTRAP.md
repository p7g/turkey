# Building the compiler from a committed bootstrap

Status: built (TIX-95). `tools/build.sh` builds the compiler from
`bootstrap/` with `cc`, `gzip` and a POSIX shell; no Python is involved.

Until this existed, every `boot` binary came from the Python compiler through
llvmlite, so retiring the Python compiler (TIX-96) would have left nothing to
build `boot` with. Every self-hosted compiler meets this problem, and they
answer it in one of two ways: a committed artifact, or a required previous
release. This is the survey behind choosing the first, and the policy that
comes with it.

## The build

```
bootstrap/arm64-darwin.s.gz ─gunzip─► stage1.s ─cc + bootstrap/runtime─► stage1
stage1 native boot/Main.gob ─► stage2.s ─cc + runtime/─► stage2      (the compiler)
stage2 native boot/Main.gob ─► stage3.s ─cc + runtime/─► stage3      (--fixpoint)
stage3 native boot/Main.gob ─► stage4.s ;  cmp stage3.s stage4.s     (--fixpoint)
```

```sh
sh tools/build.sh              # build/stages/stage2
sh tools/build.sh --fixpoint   # and check the fixed point
```

Stage N is built by stage N-1, which is Rust's convention. stage2 is today's
source compiled by the committed compiler. It is correct if the committed
compiler is, but its *code* is whatever that older compiler would emit. stage3
is today's source compiled by today's source. The fixed point is that stage3,
compiling the same source again, emits stage3's own bytes.

What `bootstrap/` holds:

* **`arm64-darwin.s.gz`** is `boot native boot/Main.gob`, the whole-program
  arm64 assembly for the compiler, compressed with `gzip -9 -n`. `-n` leaves
  out the name and the timestamp, so the same text always compresses to the
  same bytes.
* **`runtime/`** is a copy of the C runtime that assembly was emitted against.
  See "The runtime copy" below.
* **`PROVENANCE`** records the commit the artifact was built from, the date,
  the target, the command, the SHA-256 of both the text and the `.gz`, and the
  `cc` that linked it. `build.sh` checks both hashes before it runs anything.
  To reproduce an artifact from source, check out its `commit` and build it
  with the artifact that commit's own `bootstrap/` holds, walking back as far
  as you need.

`boot` reads `lib/` relative to the working directory, so `build.sh` runs
from the repository root and every stage runs there too.

## Measured

On the development machine (arm64, macOS 26, Apple clang 17), 2026-09-19:

| | |
|---|---|
| assembly text | 62,231,080 bytes, 2.87M lines |
| `gzip -9` | 3.54 MB |
| `xz -9` | 2.37 MB |
| `zstd -19` | 2.20 MB |

**Why gzip.** `/usr/bin/gzip` ships with macOS, while `xz` and `zstd` come
from Homebrew. The better compressors would save 1.2–1.3 MB per bump, which
does not pay for a dependency in a build whose point is to have almost none.

## Bumping

A bump is a policy decision, not a build step.

* **Bump only when you have to:** when the compiler's source needs something
  the committed compiler cannot compile, such as a new language feature, or
  when the runtime's ABI changes. This is Rust's `cfg(bootstrap)` discipline:
  land the feature, bump, and only *then* use the feature in the compiler.
* **The bump is its own commit, and it contains only `bootstrap/`.**
  `tools/bump-bootstrap.sh` refuses a dirty tree, runs
  `build.sh --fixpoint`, and writes stage3's assembly, the runtime and
  `PROVENANCE`. A bad bump is fixed by reverting that commit.
* **Bumping across a change the committed compiler cannot build.**
  `--stage1 BINARY` starts the chain from any compiler you already have, such
  as the previous checkout's stage2. The first bump was made this way, from
  the Python-built compiler.

The bytes in `bootstrap/` depend on the compiler's source and on nothing else:
not on `cc` and not on which compiler built stage2. That is what the fixed
point says. `cc` is recorded because it links the artifact, and a `cc` that
cannot assemble it is the likeliest way for an old bootstrap to stop working.

## The runtime copy

The compiler's output calls into the C runtime by symbol, so the committed
assembly works only against the runtime it was emitted for. A runtime ABI
change needs a new compiler, which is built by the old one, which is linked
against the old runtime. So stage1 links against `bootstrap/runtime/`, and
stage2 and later link against `runtime/`. Without the copy, the first ABI
change would make the bootstrap unbuildable at exactly the moment a bump
needed it. The copy is 55 KB.

**It shrinks as the runtime moves into Turkey** (RUNTIME-IN-TURKEY.md).
Giblet code lives in `lib/` and is compiled into the whole-program assembly,
so the artifact already carries its own copy of that part. When nothing is
left in C, delete `bootstrap/runtime/` and `build.sh`'s runtime step: every
artifact becomes self-contained, and ABI skew stops being possible. libc and
`cc` as the linker stay until LINKER.md's work lands.

## Prior art

| | Committed or required | Size | Bumped when | Fixed point |
|---|---|---|---|---|
| OCaml | `boot/ocamlc` and the stdlib's `.cm*`, in git | `boot/ocamldep` alone went from 700 kB to 2 MB in one change, and review cited "2MB of space in the GIT repository at each bootstrap commit" | bytecode, runtime primitive or `.cmi` format changes | `make compare` |
| Zig | `stage1/zig1.wasm.zst`, a reduced compiler | 2.6 MiB wasm, **637 KB** with zstd | "only when a breaking change or new feature affects the compiler when building itself" | zig2 builds stage3 |
| Rust | nothing: stage0 is the downloaded beta | — | every six-week release | stage3 is optional, and "ought to be identical" |
| Go | nothing: Go 1.N needs Go 1.(N-2), rounded down to even | — | every two releases | — |

**OCaml** is the closest match, and this design takes its policy wholesale.
The artifact is committed. A bump is triggered by a format or ABI change,
never by routine work. Its `BOOTSTRAP.adoc` says to "commit the result of the
bootstrap separately". CI (`tools/ci/inria/bootstrap`) checks that a bootstrap
can be repeated, which `build.sh --fixpoint` does here.

**Zig is the counterexample** and has to be answered. It rejected committing
native code, which it called "yucky", and rejected committing generated C,
which was an 80 MiB target-specific file. It chose wasm plus a small wasm-to-C
translator because it has to build on dozens of hosts. This project builds on
one. More to the point, `boot`'s only non-Python backend is arm64, so even a
portable stage1 would need an arm64 target to produce stage2. Portability
buys nothing here until there is a second backend. **Revisit this when there
is one.** The candidate then is the output of `boot llvm`, which any clang can
compile.

Zig's other lesson is size. Its 637 KB comes partly from wasm's density and
partly from zig1 being a *reduced* compiler, with every backend disabled but
the C one. That is the lever if history size ever matters. It does not matter
yet: rare bumps at 3.5 MB each mean twenty bumps add 70 MB.

**Rust and Go** commit nothing and require a previous release instead. That
works for them because they publish binary releases on a fixed schedule, and
the release *is* the artifact, hosted somewhere else. This project has no
releases to point at, so committing the artifact is the same idea without the
release infrastructure. Rust's contribution here is the stage numbering and the
`cfg(bootstrap)` discipline.

## Sources

* Zig, "Goodbye to the C++ Implementation of Zig":
  <https://ziglang.org/news/goodbye-cpp/>
* OCaml, `BOOTSTRAP.adoc`:
  <https://github.com/ocaml/ocaml/blob/trunk/BOOTSTRAP.adoc>
* OCaml PR #1078, on `boot/ocamldep`'s size:
  <https://github.com/ocaml/ocaml/pull/1078>
* rustc-dev-guide, "What Bootstrapping does":
  <https://rustc-dev-guide.rust-lang.org/building/bootstrapping/what-bootstrapping-does.html>
* Go, "Installing Go from source":
  <https://go.dev/doc/install/source>
