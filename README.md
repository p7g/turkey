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

From this checkout, with Python 3.11+ and a C compiler installed:

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Save the example as `rectangles.gob`, then run it or build an executable:

```sh
python3 -m turkey run rectangles.gob
python3 -m turkey build rectangles.gob -o rectangles
./rectangles
```

Use `python3 -m turkey types rectangles.gob` to inspect inferred types, or
`python3 -m turkey --help` for the compiler's other commands.

### The compiler written in Turkey

On an arm64 Mac, the self-hosted compiler builds from the committed bootstrap
with only a C compiler, no Python:

```sh
sh tools/build.sh
build/stages/stage2 native rectangles.gob > rectangles.s
cc -std=c11 -O1 -o rectangles rectangles.s runtime/turkey_runtime.c
./rectangles
```

Run it from the repository root, where it finds `lib/`. See
[BOOTSTRAP.md](BOOTSTRAP.md) for how the bootstrap works.

## Project status

Turkey is an experimental language under active development. This repository,
`turkey`, contains the Python implementation, an LLVM native backend, and
a compiler being written in Turkey itself. The standard library is written in
Turkey over a small set of runtime primitives. A generated-Python backend and
differential tests help check that the implementations agree.

For more depth:

- [Language reference](docs/ref/README.md): the syntax and meaning of every construct
- [Language design](design.md) and [changes to the specification](SPEC-DELTAS.md)
- [Standard library](STDLIB.md) and [library design](LIBRARY-DESIGN.md)
- [Compiler written in Turkey](boot/) and [lessons from building it](FINDINGS.md)
- [Building the compiler from its committed bootstrap](BOOTSTRAP.md)
- [Roadmap](plan.txt)

To run the test suite, install the development dependencies with
`python3 -m pip install -e '.[dev]'`, then run `python3 -m pytest tests -q`.
The tests compile their programs with `boot`, which is built from the
committed bootstrap on first use; `TURKEY_BOOT` points them at one you built
yourself instead.
