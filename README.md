# 🦃 Turkey

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

The compiler is written in Turkey. On an arm64 Mac running macOS 14.4 or
later (the runtime sleeps on `os_sync_wait_on_address`), with a C compiler
installed, it builds from the assembly committed in `bootstrap/` (for Linux,
see below):

```sh
sh scripts/build.sh
```

That leaves a compiler in `build/stages/stage2`. Save the example as
`rectangles.gob`, then compile and run it:

```sh
build/stages/stage2 build -o rectangles.o rectangles.gob
cc -o rectangles rectangles.o
./rectangles
```

On Linux, link with `$TURKEY_CC` (below) and add `-lm`, since glibc keeps
the maths functions the library calls out of libc.

A program that imports `Net.TLS` also links OpenSSL 3: add `-lssl -lcrypto`.
On Linux that is the system's (`apt-get install libssl-dev` on Debian and
Ubuntu). On a Mac it is Homebrew's, since the system's own
`/usr/lib/libcrypto.dylib` ends any process that loads it:

```sh
brew install openssl@3
cc -o client client.o -L"$(brew --prefix openssl@3)/lib" -lssl -lcrypto
```

The tests find it the same way, or under `$TURKEY_OPENSSL` if it is set, and
make their certificates with that installation's `openssl`.

Run the compiler from the repository root, where it finds `lib/`. It has two
commands. `build` writes the program to `-o`, in the format the file's
extension names -- a relocatable object for `.o`, assembly for `.s` -- or
`-f obj` or `-f asm` says; `-o - -f asm` writes it to standard output. An
object needs no assembler, only a linker. `-f exe` writes an executable
that runs as it is, with no assembler and no linker: a signed Mach-O on a
Mac, and on arm64 Linux an ELF that loads glibc 2.34 or later.

```sh
build/stages/stage2 build -f exe -o rectangles rectangles.gob
./rectangles
```

It is written for either target from any host, with `--target`, and links
the C library alone -- libSystem on a Mac, glibc's `libc.so.6` and
`libm.so.6` on Linux -- so a program that imports `Net.TLS` is refused and
keeps the object path. It carries no debug or unwind information yet, so it
takes no `-g`, and a file name without an extension will not choose it until
the executable has what lldb and a profiler read. `check` compiles as far as
the last stage that can reject a program and writes nothing. Either takes `--dump STAGE,...` to
print what a stage produced: `build/stages/stage2 check --dump types
rectangles.gob` shows the inferred types, and running the compiler with no
arguments lists the stages. `build --timings` reports on standard error how
long each phase took, how the backend's time is spread over functions, and how
much of the optimizer's work could run in parallel.

A program's tasks (`System.Task`, `System.Parallel`) run on one worker thread
per performance core -- every core on a Mac without performance levels; on
Linux, every CPU the program may run on, or its cgroup's CPU limit where that
is lower -- and the compiler is such a program: it compiles in parallel, and emits
the same bytes however many workers it has. `TURKEY_WORKERS=N` sets the number
of workers: how many tasks run at once. `TURKEY_WORKERS=1` runs tasks
interleaved on the program's own thread, in a fixed order, unless a task waits
long enough in a call that cannot be polled, such as a file read or `flock`:
then another thread runs the rest meanwhile, as one does beside each worker
blocked in such a call. The test suite sets it to 1 for everything it runs
unless a test asks for more, since it runs a process per core already.
The suite also sets `TURKEY_CHECK_BINDERS`, which has the compiler check that
name resolution recorded a binder for every variable a program binds or reads;
a gap is an internal error. It costs a walk of the program, so it is off
otherwise.

The compiler that builds it is `bootstrap/`, which holds its own arm64
assembly for macOS and for Linux, gzip'd. `scripts/build.sh` builds for whichever of the two the C compiler
links for. `sh scripts/build.sh
--fixpoint` checks the fixed point: the compiler built from today's source
emits exactly the assembly it was built from. `scripts/bump-bootstrap.sh`
replaces the committed compiler, which is rare and deliberate; both scripts
explain themselves at the top.

### Debugging and profiling

`build -g` adds a line table and the DWARF a debugger needs to find it: a
compile unit for the program and an entry for each function. It changes no
code: a program built with `-g` runs the same instructions as one built
without. It works for an object and for assembly alike:

```sh
build/stages/stage2 build -g -o rectangles.o rectangles.gob
cc -o rectangles rectangles.o
```

Assembly written with `-g` needs the assembler to build the line table, in
DWARF 5 as the rest is: `cc -gdwarf-5 -c rectangles.s -o rectangles.o`.

`-g` is `--debug=full`. `--debug=lines` writes less: the line table, the
functions and the calls inlined into them, which is all a profiler or a
backtrace reads, and none of what a debugger needs to show a variable.

On a Mac, keep `rectangles.o` where it is. The linker leaves the DWARF in the
object and writes only a map to it into the executable, which is how lldb and
Instruments find it. To make a copy that does not need the object, run
`dsymutil rectangles`, which writes `rectangles.dSYM` beside it. On Linux the
DWARF is linked into the executable.

Then a debugger stops at a line and names the frames by function, file and
line:

```sh
lldb ./rectangles -o 'breakpoint set --file rectangles.gob --line 4' -o run -o bt
gdb ./rectangles -ex 'break rectangles.gob:4' -ex run -ex bt
```

A debugger names a function by its display form, which spells the
compiler's separators the way a reader would: `Main#area@Rectangle`, `area`
specialized to `Rectangle`, is `Main.area[Rectangle]`; an instance's method,
`%inst.Data.Hashable#Hash.Int#hashInto@FNV1a`, is
`Hash(Int).hashInto[FNV1a]`; a lambda in `makeRoom` is
`Turkey.Stdout.makeRoom.<lambda0>`. `src/Turkey/Demangle.gob` has the rules.
The display form is also a breakpoint, and a call the optimizer inlined is
still a frame:

```sh
lldb ./rectangles -o 'breakpoint set --name Main.area[Rectangle]' -o run -o bt
gdb ./rectangles -ex "break 'Main.area[Rectangle]'" -ex run -ex bt
```

stops in `Main.main` where `area`'s code was copied, and in `area` itself
where it was not, and `bt` shows `area` at its own line above `main` at the
line of the call. gdb needs the quotes around a name with brackets in it. A
breakpoint by symbol, `breakpoint set --name Main#area@Rectangle` or
`break 'Main#area@Rectangle'`, stops only in the copy that has the symbol.
In lldb a breakpoint on every specialization of `area`, inlined copies
included, is a regular expression: `breakpoint set --func-regex
'^Main\.area\['`. gdb's `rbreak ^Main\.area\[` sets a breakpoint on each
specialization it matches, but each one stops only in the copy that has the
symbol, as a breakpoint by symbol does.

`frame variable` in lldb and `info locals` in gdb show a function's
parameters and locals wherever the code still holds them: in a register until
its last use, or in the frame slot the collector or the register allocator
keeps it in.

The debug information describes values as the compiled code holds them, so
without help a debugger shows a `String` as a pointer and a `Vec` as its
storage. The formatters in `debugger/` show them as the source writes them --
a `String` as its text, an `Array`, a `Vec` or a `Map` as its elements, a sum
such as `Option` or `Either` as the constructor it holds, a record as its
fields:

```sh
scripts/turkey-lldb ./rectangles -o 'breakpoint set --file rectangles.gob --line 4' -o run -o 'frame variable'
```

`scripts/turkey-lldb` is lldb with `debugger/turkey_lldb.py` imported; in a
session of your own, `command script import debugger/turkey_lldb.py` does the
same. On Linux, a program built with `-g` names `turkey_gdb.py` for gdb to
load. gdb looks for it along its source path, so it loads it once
`~/.gdbinit` adds the directory there and to the safe path:

```
directory /path/to/turkey/debugger
add-auto-load-safe-path /path/to/turkey/debugger
```

Profilers and backtraces need no `-g` to name functions. Every function keeps
a frame pointer, so `sample PID` and Instruments on a Mac, and
`perf record --call-graph=fp` on Linux, walk the stack and show the functions
by name. Every build also carries unwind information -- compact unwind on a
Mac, `.eh_frame` on Linux -- which lldb and gdb read to find a function's
caller from any instruction in it, its first and its `ret` included. A
backtrace inside a task or coroutine ends at the coroutine's base. With `-g`,
Instruments and `perf report --sort srcline` also show the lines samples fall
on, and Instruments shows inlined calls as frames. `sample` shows a sample in
inlined code at the line of the outermost call.

A profiler names a function by its symbol, which `scripts/demangle.py`
rewrites to the display form, in `sample`'s and `perf script`'s output and
in a crash report:

```sh
sample rectangles 5 -file report.txt && python3 scripts/demangle.py report.txt
perf script | python3 scripts/demangle.py
```

### The C compiler, and running on another machine

Two environment variables choose the toolchain, for `scripts/build.sh` and for
the tests alike. Each is split into words the way a shell would split it.

- `TURKEY_CC` is the C compiler that links a program, and assembles the
  bootstrap compiler. Unset, it is `cc`.
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
any platform emit Linux objects and assembly.

`--target arm64-linux -f exe` needs none of the cross toolchain: it writes a
dynamically linked executable on any host, with no linker and no arm64 libc.
That executable runs on glibc 2.34 or later -- RHEL 9 and its rebuilds,
Ubuntu 22.04, Debian 12, and anything newer -- the first release with
`pthread_create` in `libc.so.6`. On an older glibc it stops before `main`
with `symbol lookup error: undefined symbol`. It names its loader
`/lib/ld-linux-aarch64.so.1`, as every glibc distribution installs it; on
NixOS or Guix, `patchelf` it as any prebuilt binary.

The whole test suite runs and passes this way, `pytest -m bootstrap` and the
fixed point included, and the goldens in `tests/programs/` are the same on
both platforms. It is slow: qemu runs the compiler about fifteen times slower
than an arm64 Mac, so one self-compile takes about 26 minutes rather than
105 seconds. On a 4-core x86-64 machine `sh scripts/build.sh` takes about
27 minutes and `--fixpoint` about 80. `python3 -m pytest` takes about 75
minutes once `boot` is built and two hours from a cold cache; the few tests
that compile the compiler's own source are most of that.

On an arm64 Linux machine none of that is needed: `cc` links for it and the
programs run directly, and `-f exe` needs no C compiler at all. CI does this on
GitHub's arm64 runners (`.github/workflows/linux.yml`), building from the
bootstrap under gcc and running the suite and the fixed point split across jobs
by `scripts/ci-shard.sh`.

`tests/test_debug_info.py` reads `-g`'s DWARF with `llvm-dwarfdump`, and it
and `tests/test_unwind.py` run lldb on macOS and gdb on Linux; each skips a
check, saying which tool is missing, where one is not installed.

One group of tests depends on the C compiler, and skips, saying why, where it
cannot do what it needs: under
`TURKEY_CC="clang --target=aarch64-linux-gnu -static"`, the UBSan probes in
`test_allocators.py` and `test_runtime_gc.py`. Linking `-fsanitize=undefined`
statically needs compiler-rt built for aarch64, which Ubuntu's clang does not
ship. gcc's cross toolchain has `libubsan.a`, so under gcc they run.

### Editor support

`vim/` is a Vim and Neovim plugin for `.gob` files: filetype detection,
syntax highlighting and indentation. Add the directory to `runtimepath`, or
point a plugin manager at it (`Plug '~/path/to/turkey/vim'`). Its tests run
headless in whichever of `vim` and `nvim` are installed, and check that the
fixture parses when `TURKEY_BOOT` or `build/stages/stage2` is a compiler:

```sh
sh vim/test/run.sh
```

## Project status

Turkey is an experimental language under active development. The compiler is
written in Turkey and compiles itself, with an arm64 backend that emits
assembly directly. The standard library is written
in Turkey over a small set of runtime primitives. A Python implementation
served as the reference while the compiler was ported to Turkey, and was
retired once the tests ran against the self-hosted one.

For more depth:

- [Language reference](docs/ref/README.md): the syntax and meaning of every construct
- [Compiler written in Turkey](src/)

The recorded programs in `tests/programs/`, the case archives in
`tests/cases/` and the examples in `docs/ref/` are run by a harness written in
Turkey, `tests/harness/`:

    sh scripts/test.sh                    # every case, one per core at once
    sh scripts/test.sh tests/programs/bf  # the programs whose path starts so
    sh scripts/test.sh tests/cases/placeholders.cases::condition  # some of an archive's cases
    sh scripts/test.sh docs/ref/types.md  # one chapter's examples
    sh scripts/test.sh -v --shard 2/4     # a quarter of them, passes listed
    sh scripts/test.sh --selftest         # the harness checked on its own cases
    sh scripts/test.sh --bless PATH       # PATH's goldens rewritten from its output

It builds `boot` (or uses `TURKEY_BOOT`) and compiles itself with the committed
bootstrap compiler, both cached in `$TMPDIR/turkey-test/`, as are compiled
programs. A program's expected outcome is a directive in it: `--@ error` (it
must not compile), `--@ panic`, `--@ exit: N`, and otherwise exit 0. A case
that fails is shown with a diff, and every actual output is kept under
`$TMPDIR/turkey-test/actual/`. A reference example is named by its chapter and
the line of its fence, `docs/ref/types.md:40`, and checked against what the
chapter says of it (`docs/ref/README.md`); `--bless` never writes one.

A case archive holds many small cases in one text file, cut into sections by
header lines:

    -- shared Shapes.gob --     a module written beside every case (before the first case only)
    -- case area_of_a_square -- starts a case; `--` comments and `--@` directives follow
    --@ error                   as in a program, plus `check` and `matrix: VAR=1,4`
    -- Main.gob --              the case's entry module; other `NAME.gob` sections sit beside it
    -- stdout --                its exact stdout; also `stderr`, `error`, `panic`,
                                `types`, `kinds` and `classes`

A case with no `stdout` or `stderr` section must print nothing there. `error`
is the diagnostic without its place, `panic` the panic's message, and a dump
section holds only the case's own declarations. `--@ matrix` runs the case once
per value, with the variable set in its environment, as `NAME[VAR=4]`. Any
other header or directive is an error, so a line reading `-- X --` inside a
module has to be reworded. `tests/harness/Archive.gob` gives the whole format.

`--bless` writes the selected programs' actual output over their `.expected`
files, and their dumps over the `.types`, `.core`, `.opt` and `.mono` files
they already have. In an archive it rewrites the case's expectation sections
in place, adding a missing `stdout`, `stderr`, `error` or `panic` section, and
leaves every other byte of the file alone. It refuses a case that crashed,
timed out, did not end as its directive says, printed nothing where its golden
had output, or printed what an archive cannot hold, and says why. When more than 5 are refused it writes nothing at all, since that
is what a broken compiler looks like. It never changes a directive.

The rest of the tests are Python. Install their dependencies with
`python3 -m pip install -e '.[dev]'`, then run `python3 -m pytest tests -q`.
The tests compile their programs with `boot`, which is built from the
committed bootstrap on first use; `TURKEY_BOOT` points them at one you built
yourself instead.

The suite runs in parallel by default, and three commands cover it:

    python3 -m pytest tests -q            # everything but the fixed point
    python3 -m pytest tests -q -m bootstrap   # the fixed point alone
    python3 -m pytest tests -q -m ''      # both, in one run

To run both, use the last one rather than the first two in turn. The fixed
point is a chain nothing can parallelize -- `boot` compiling the compiler,
linking the result, and that compiling it again, about two minutes a step --
and in one run it overlaps the rest of the suite instead of starting after it.
On a 16-core arm64 Mac, after a change to `src/` (so `boot` is rebuilt), the
combined run takes about nine minutes. The build of `boot` and everything the tests
compute from it are cached in `$TMPDIR` by content hash, so a second run
with nothing changed is quicker; `-n0` runs serially, for debugging.

To check the heap while debugging a program, compile it with
`boot build --gc-verify`. Each collection independently checks object bounds,
each object's size class against the size its allocation recorded (objects
carry no size of their own, so such a build keeps a size table per region),
traced references, shadow-stack and native roots, marking completeness,
region bookkeeping before and after sweeping, and the generational
invariants: an old object holding a young one must have been remembered by
the write barrier. Failures report
`heap verifier:` and terminate the process with exit status 1 before proceeding
to the next collection phase.
Run it with `TURKEY_GC_STRESS=1` to collect and verify at every allocation.
At the compiler's own scale that does not finish; `TURKEY_GC_STRESS=N`
collects at random intervals averaging N allocations instead, and
`TURKEY_GC_STRESS_SEED` picks the sequence (1 by default), so a failure can be
repeated and several seeds put the collections at different allocations.
Under stress one collection in eight is full and the rest are minor;
`TURKEY_GC_GENERATIONAL=0` makes every collection full, to tell a collector
bug from a missed write barrier.
Verification scans the allocated heap and uses temporary native memory for a
region index. A program compiled without `--gc-verify` has no verifier in it
at all, and says so on stderr if it is run with `TURKEY_GC_VERIFY` set. It
checks the roots and layouts the compiler supplies, so behavioral stress tests
are still needed to catch references omitted from those descriptions.

`boot build --gc-stats` compiles in the collector's accounting: a line per
collection and a summary at exit, on stderr. Counting sends every allocation
through the runtime rather than the code compiled in line for it, so a stats
build's times are slower than a plain one's.
