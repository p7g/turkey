# Turkey

A small procedural language with an ML-style type system.

Turkey brings type inference, algebraic data types, and first-class functions
to code with familiar loops, early returns, and mutable state. The aim is to
make straightforward programs easy to write, while keeping the type system
strong enough to support reusable abstractions.

## Philosophy

- **Write the algorithm directly.** Evaluation is strict, functions take ordinary
  argument lists, and loops and mutation are part of everyday code.
- **Let the compiler connect the types.** Types are checked statically and inferred
  across functions. Add annotations where they help explain an interface.
- **Make alternatives explicit.** Model choices with algebraic data types,
  handle them with pattern matching, and compose `Option` and `Either` with `?`.
- **Build power in the library.** Operators, iteration, indexing, and generic
  functions are defined through type classes. User-defined types can participate
  in the same syntax as the standard library.

## A taste

Parse a few rectangles and add up their areas:

```kotlin
type Rectangle = Rectangle { width : Int, height : Int }

fun parseRectangle(text) {
    let (width, height) = String.splitOnce(text, "x")?
    Some(Rectangle {
        width = Int.parse(width)?,
        height = Int.parse(height)?
    })
}

fun area(rect) = rect.width * rect.height

fun main() {
    var total = 0
    for text in ["3x4", "5x2", "oops"] {
        match parseRectangle(text) {
            Some(rect) -> {
                total = total + area(rect)
            }
            None -> print("Skipping: " + text)
        }
    }
    print(total)
}
```

```text
Skipping: oops
22
```

The compiler infers `parseRectangle : fun(String) -> Option Rectangle`.
Each `?` unwraps a successful result; a missing separator or invalid integer
makes the parser produce `None`. The caller handles both cases explicitly.
Tuple destructuring, named record fields, and an ordinary mutable accumulator
work together without type annotations on the functions.

## Try it

The compiler is written in Turkey. On an arm64 Mac, with a C compiler
installed, it builds from the assembly committed in `bootstrap/` (for Linux,
see below):

```sh
sh scripts/build.sh
```

That leaves a compiler in `build/stages/stage2`. Save the example as
`rectangles.gob`, then compile and run it:

```sh
build/stages/stage2 native rectangles.gob > rectangles.s
cc -o rectangles rectangles.s runtime/turkey_runtime.c
./rectangles
```

On Linux, link with `$TURKEY_CC` (below) and add `-lm`, since glibc keeps
`log10` out of libc.

Run the compiler from the repository root, where it finds `lib/`. Use
`build/stages/stage2 types rectangles.gob` to inspect inferred types; the
other subcommands print what a stage produced, and `check` just compiles.

The compiler that builds it is `bootstrap/`, which holds its own arm64
assembly for macOS and for Linux, gzip'd, with the runtime it was emitted
against. `scripts/build.sh` builds for whichever of the two the C compiler
links for. `sh scripts/build.sh
--fixpoint` checks the fixed point: the compiler built from today's source
emits exactly the assembly it was built from. `scripts/bump-bootstrap.sh`
replaces the committed compiler, which is rare and deliberate; both scripts
explain themselves at the top.

### The C compiler, and running on another machine

Two environment variables choose the toolchain, for `scripts/build.sh` and for
the tests alike. Each is split into words the way a shell would split it.

- `TURKEY_CC` is the C compiler that assembles and links a program with the
  runtime. Unset, it is `cc`.
- `TURKEY_RUN` is a prefix for running a program that compiler linked, or a
  built compiler. Unset or empty, the program runs directly.

On x86-64 Linux, the arm64 output runs under qemu-user. Link it statically,
because a dynamically linked arm64 binary would need an arm64 libc to load.
Set up the machine with:

```bash
apt-get update
apt-get install -y \
    qemu-user-static \
    gcc-aarch64-linux-gnu libc6-dev-arm64-cross \
    python3-pytest python3-pytest-xdist
B=/proc/sys/fs/binfmt_misc
if [ -d $B ] && [ ! -e $B/register ]; then
    mount -t binfmt_misc binfmt_misc $B 2>/dev/null
fi
if [ -w $B/register ]; then
    cat /usr/lib/binfmt.d/qemu-aarch64.conf > $B/register 2>/dev/null || true
fi
```

Then set `TURKEY_CC="aarch64-linux-gnu-gcc -static"` and leave `TURKEY_RUN`
empty. The last step of the setup registers qemu with the kernel's binfmt_misc,
so an arm64 binary runs as `./program`, with no prefix, mounting binfmt_misc
first where the container has not. That registration is
lost when a machine is restored from a snapshot, so the repository's
`.claude/settings.json` runs the same line as a SessionStart hook. On macOS the
hook does nothing. Where binfmt_misc cannot be written, set
`TURKEY_RUN=qemu-aarch64-static` instead. pytest comes from apt here, because Ubuntu
24.04 refuses a system-wide `pip install`.

With those set, `sh scripts/build.sh` builds the compiler for arm64 Linux from
`bootstrap/arm64-linux.s.gz`, and `--target arm64-linux` makes a compiler on
any platform emit Linux assembly.

The whole test suite runs and passes this way, `pytest -m bootstrap` and the
fixed point included, and the goldens in `tests/programs/` are the same on
both platforms. It is slow: qemu runs the compiler about fifteen times slower
than an arm64 Mac, so one self-compile takes about 26 minutes rather than
105 seconds. On a 4-core x86-64 machine `sh scripts/build.sh` takes about
27 minutes and `--fixpoint` about 80. `python3 -m pytest` takes about 75
minutes once `boot` is built and two hours from a cold cache; the few tests
that compile the compiler's own source are most of that.

Two groups of tests depend on the C compiler, and skip, saying why, where it
cannot do what they need:

- Under gcc, every test that compiles `boot llvm`'s output: gcc cannot
  compile LLVM IR, and reads a `.ll` as a linker script. That is 217
  tests with the setup above. With
  `TURKEY_CC="clang --target=aarch64-linux-gnu -static"`, which links through
  the same cross binutils and libc, they run and pass.
- Under that clang, the UBSan probes in `test_allocators.py` and
  `test_runtime_gc.py`: linking `-fsanitize=undefined` statically needs
  compiler-rt built for aarch64, which Ubuntu's clang does not ship. gcc's
  cross toolchain has `libubsan.a`, so under gcc they run.

## Project status

Turkey is an experimental language under active development. The compiler is
written in Turkey and compiles itself, with an arm64 backend that emits
assembly directly and an LLVM path beside it. The standard library is written
in Turkey over a small set of runtime primitives. A Python implementation
served as the reference while the compiler was ported to Turkey, and was
retired once the tests ran against the self-hosted one.

For more depth:

- [Language reference](docs/ref/README.md): the syntax and meaning of every construct
- [Language design](design.md) and [changes to the specification](SPEC-DELTAS.md)
- [Standard library](STDLIB.md) and [library design](LIBRARY-DESIGN.md)
- [Compiler written in Turkey](src/) and [lessons from building it](FINDINGS.md)
- [Roadmap](plan.txt)

The tests are Python. Install their dependencies with
`python3 -m pip install -e '.[dev]'`, then run `python3 -m pytest tests -q`.
The tests compile their programs with `boot`, which is built from the
committed bootstrap on first use; `TURKEY_BOOT` points them at one you built
yourself instead.

To check the heap while debugging a compiled program, run it with
`TURKEY_GC_VERIFY=1`. Each collection independently checks object bounds,
traced references, shadow-stack and native roots, marking completeness, and
region bookkeeping before and after sweeping. Failures report
`heap verifier:` and terminate the process with exit status 1 before proceeding
to the next collection phase.
Combine it with `TURKEY_GC_STRESS=1` to collect and verify at every allocation.
Verification scans the allocated heap and uses temporary native memory for a
region index; it is disabled by default. It checks the roots and layouts the
compiler supplies, so behavioral stress tests are still needed to catch
references omitted from those descriptions.
