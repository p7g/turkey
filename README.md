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
`TURKEY_RUN=qemu-aarch64` instead. pytest comes from apt here, because Ubuntu
24.04 refuses a system-wide `pip install`.

With those set, `sh scripts/build.sh` builds the compiler for arm64 Linux from
`bootstrap/arm64-linux.s.gz`, and `--target arm64-linux` makes a compiler on
any platform emit Linux assembly.

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
