"""Unwind information: what lldb, gdb, `sample`, `perf` and the C library read
to find a function's caller from any instruction in it.

Every build carries it, `-g` or not. On Mach-O it is a compact unwind entry
per function, frame mode for all but `turkey_swap_stack`, whose rules change
from one instruction to the next and so are a DWARF description in
`__eh_frame`. On ELF it is `.eh_frame`, exact at every instruction.

**The executable's oracle is the assembler**, as for the line table: the
unwind information in the executable `turkey` writes is what the system
assembler makes of `turkey`'s own assembly, whose `.cfi_*` directives say the
same rules, for the test programs and for `turkey` itself
(`tests/test_executable.py`, `_unwind_agrees`).

Then the tools, on the executable `turkey` writes: `objdump` and `readelf` show
an entry for every function, and a debugger stopped on a function's first
instruction or on its `ret` -- where frame mode does not hold, and lldb reads
the code instead -- shows the whole stack, which inside a coroutine ends at
the coroutine's base.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import bootc, toolchain
from tests.test_debug_info import _split
from tests.test_executable import _unwind_agrees

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
PROGRAMS = REPO_ROOT / "tests" / "programs"
CORPUS = [path.name for path in bootc.corpus()]

FACT = """\
fun fact(n : Int) -> Int {
    if n <= 1 {
        return 1
    }
    n * fact(n - 1)
}

fun main() {
    let x = fact(10)
    print(x)
}
"""

# A task runs on a coroutine of its own, on a worker thread.
TASK = """\
import System.Task as Task

fun leaf(n : Int) -> Int {
    if n <= 0 {
        return 7
    }
    leaf(n - 1) + 1
}

fun main() {
    let t = Task.runScope(do {
        let a = Task.spawn(fun() = leaf(3))?
        pure(a)
    })
    print(Task.join(t))
}
"""

SOURCES = {"fact": FACT, "task": TASK}

pytestmark = pytest.mark.skipif(toolchain.missing(), reason="no C compiler")


def _darwin() -> bool:
    return toolchain.target() == "arm64-darwin"


# -- the executable against the assembler ---------------------------------------

def _compare_with_assembler(path: Path, text: str) -> None:
    """`path` written as an executable, against the system assembler's object
    of `text`, its assembly, both from the repository's root."""
    with tempfile.TemporaryDirectory() as directory:
        source, theirs = Path(directory) / "t.s", Path(directory) / "theirs.o"
        ours = Path(directory) / "ours"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run([*toolchain.cc(), "-c", "-o", str(theirs), str(source)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[:4000]
        written = subprocess.run(
            toolchain.command(bootc.binary(), "build", "-o", str(ours), str(path)),
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert written.returncode == 0, written.stderr[-4000:]
        _unwind_agrees(ours, theirs)


@pytest.mark.parametrize("name", CORPUS)
def test_the_executable_has_the_assemblers_unwind_information(name):
    paths = [PROGRAMS / n for n in CORPUS]
    texts = bootc.boot_each("asm", paths, _split)
    _compare_with_assembler(PROGRAMS / name, texts[PROGRAMS / name])


@pytest.mark.bootstrap
def test_the_executable_has_the_assemblers_unwind_information_on_the_compiler():
    main = bootc.BOOT_MAIN
    _compare_with_assembler(main, bootc.boot_each("asm", [main], _split)[main])


# -- the tools -----------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _built(program: str) -> Path:
    """A program of `SOURCES` as the executable `prog`, in a directory of its
    own."""
    directory = Path(tempfile.mkdtemp(prefix="turkey-unwind-"))
    (directory / "prog.gob").write_text(SOURCES[program], encoding="utf-8")
    compiled = subprocess.run(
        toolchain.command(bootc.binary(), "build", "-o", "prog", "prog.gob"),
        cwd=directory, env=dict(os.environ, TURKEY_LIB=str(LIB)),
        capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr[-4000:]
    return directory


def _symbols(path: Path) -> list[tuple[int, str, str]]:
    """`path`'s symbols, by address: each address, `nm`'s type letter and the
    name as the program spells it, without Mach-O's underscore."""
    out = subprocess.run(["nm", "-n", str(path)], capture_output=True, text=True,
                         check=True).stdout
    symbols = []
    for line in out.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3:
            name = fields[2][1:] if _darwin() and fields[2].startswith("_") else fields[2]
            symbols.append((int(fields[0], 16), fields[1], name))
    return symbols


def _functions(path: Path) -> dict[str, int]:
    """The code symbols of an executable or object, by name, with their
    addresses."""
    return {name: address for address, kind, name in _symbols(path)
            if kind in "Tt" and not name.startswith(("ltmp", "$x", "$d"))}


def _check_encodings(functions: dict[str, int], encoding_of) -> None:
    """Frame mode for every function, but frameless for the C faces' Turkey
    entries, which make no frame, and DWARF for `turkey_swap_stack`, whose
    low 24 bits are its FDE's offset."""
    for name, address in functions.items():
        encoding = encoding_of(address)
        if name == "turkey_swap_stack":
            assert encoding & 0x0F000000 == 0x03000000, f"{name}: {encoding:#x}"
        elif name.startswith("turkey."):
            assert encoding == 0x02000000, name
        else:
            assert encoding & 0x0F000000 == 0x04000000, f"{name}: {encoding:#x}"


def test_every_function_has_a_compact_unwind_entry():
    if not _darwin():
        pytest.skip("compact unwind is Mach-O's")
    directory = _built("task")
    # An image's entry stands for every function up to the next entry, so
    # each function's encoding is the last entry's at or below it.
    out = subprocess.run(["objdump", "--macho", "--unwind-info", str(directory / "prog")],
                         capture_output=True, text=True, check=True).stdout
    entries = sorted((0x100000000 + int(start, 16), int(encoding, 16))
                     for start, encoding in re.findall(
                         r"function offset=(0x[0-9a-f]+), encoding=(0x[0-9a-f]+)", out))
    functions = _functions(directory / "prog")
    assert entries and entries[0][0] == min(functions.values()), out[:2000]
    _check_encodings(functions, lambda address: next(
        e for a, e in reversed(entries) if a <= address))


def test_every_function_has_an_fde():
    if _darwin():
        pytest.skip(".eh_frame is the only unwind information on ELF")
    if shutil.which("readelf") is None:
        pytest.skip("no readelf on $PATH")
    directory = _built("task")
    out = subprocess.run(["readelf", "--debug-dump=frames", str(directory / "prog")],
                         capture_output=True, text=True, check=True).stdout
    starts = {int(start, 16) for start in re.findall(r"FDE cie=\S+ pc=([0-9a-f]+)\.\.", out)}
    # Every function is the module's, but `_start`, which the writer adds
    # and describes by no FDE.
    functions = {name: address for name, address in _functions(directory / "prog").items()
                 if name != "_start"}
    missing = sorted(name for name, address in functions.items() if address not in starts)
    assert functions and not missing, f"functions with no FDE: {missing}"


# -- debuggers -----------------------------------------------------------------

def _lldb() -> str:
    if not _darwin():
        pytest.skip("lldb is checked on macOS, gdb on Linux")
    if shutil.which("lldb") is None:
        pytest.skip("no lldb on $PATH")
    return "lldb"


def _gdb() -> str:
    if _darwin():
        pytest.skip("lldb is checked on macOS, gdb on Linux")
    if shutil.which("gdb") is None:
        pytest.skip("no gdb on $PATH")
    return "gdb"


def _stops(directory: Path, function: str) -> dict[str, int]:
    """The addresses in the executable to stop at in `function`: its
    first instruction, and its first `ret`."""
    symbols = [(a, n) for a, kind, n in _symbols(directory / "prog") if kind in "Tt"]
    start = next(a for a, n in symbols if n == function)
    end = min(a for a, _ in symbols if a > start)
    out = subprocess.run(["objdump", "-d", f"--start-address={start:#x}",
                          f"--stop-address={end:#x}", str(directory / "prog")],
                         capture_output=True, text=True, check=True).stdout
    rets = [int(m.group(1), 16) for m in
            re.finditer(r"^\s*([0-9a-f]+):.*\bret\b", out, re.MULTILINE)]
    assert rets, out[-2000:]
    return {"first": start, "ret": rets[0]}


def _gdb_break(directory: Path, function: str, where: str) -> str:
    """gdb's command to stop at `where` in `function`, as `_stops` finds it,
    named as a distance from the function's symbol. An executable `turkey`
    writes is position-independent, and gdb moves a breakpoint named by a
    symbol to where the program is loaded, but not one named by an address."""
    stops = _stops(directory, function)
    return f"break *((char *) '{function}' + {stops[where] - stops['first']})"


def _debugged(directory: Path, argv: list[str]) -> str:
    """A debugger's batch run, bounded: a stop that never comes leaves the
    program to finish and the debugger to exit, and one that misbehaves must
    not hang the suite."""
    result = subprocess.run(argv, cwd=directory, capture_output=True, text=True, timeout=120)
    return result.stdout + result.stderr


def _lldb_frames(out: str) -> list[str]:
    """The frames of the last backtrace lldb printed, innermost first."""
    last = out.rsplit("stop reason", 1)[-1]
    return re.findall(r"frame #\d+: 0x[0-9a-f]+ \S+`(\S+)", last)


def _gdb_frames(out: str) -> list[str]:
    return [m.group(1) for m in re.finditer(
        r"^#\d+\s+(?:0x[0-9a-f]+ in )?(\S+)", out, re.MULTILINE)]


@pytest.mark.parametrize("where", ["first", "ret"])
def test_lldb_shows_the_whole_stack_on_entry_and_return(where):
    lldb = _lldb()
    directory = _built("fact")
    address = _stops(directory, "Main#fact")[where]
    out = _debugged(directory, [lldb, "--batch", "-o", f"breakpoint set -s prog -a {address:#x}",
                                "-o", "run", "-o", "bt", "-o", "kill", "./prog"])
    frames = _lldb_frames(out)
    assert frames[:1] == ["Main#fact"], out[-4000:]
    assert "Main#main" in frames and "turkey_entry" in frames, out[-4000:]
    callers = frames[1:frames.index("Main#main")]
    assert all(f == "Main#fact" for f in callers), out[-4000:]


@pytest.mark.parametrize("where", ["first", "ret"])
def test_gdb_shows_the_whole_stack_on_entry_and_return(where):
    gdb = _gdb()
    directory = _built("fact")
    stop = _gdb_break(directory, "Main#fact", where)
    out = _debugged(directory, [gdb, "-nx", "-batch", "-ex", stop,
                                "-ex", "run", "-ex", "bt", "-ex", "kill", "./prog"])
    frames = _gdb_frames(out)
    assert frames[:1] == ["Main#fact"], out[-4000:]
    assert "Main#main" in frames and "turkey_entry" in frames, out[-4000:]
    callers = frames[1:frames.index("Main#main")]
    assert all(f == "Main#fact" for f in callers), out[-4000:]
    assert "Backtrace stopped" not in out, out[-4000:]


def test_lldb_ends_a_coroutines_stack_at_its_base():
    lldb = _lldb()
    directory = _built("task")
    address = _stops(directory, "Main#leaf")["first"]
    out = _debugged(directory, [lldb, "--batch", "-o", f"breakpoint set -s prog -a {address:#x}",
                                "-o", "run", "-o", "bt", "-o", "kill", "./prog"])
    frames = _lldb_frames(out)
    assert frames[:1] == ["Main#leaf"], out[-4000:]
    assert frames[-1] == "turkey_stack_start", out[-4000:]


def test_gdb_ends_a_coroutines_stack_at_its_base():
    gdb = _gdb()
    directory = _built("task")
    stop = _gdb_break(directory, "Main#leaf", "first")
    out = _debugged(directory, [gdb, "-nx", "-batch", "-ex", stop,
                                "-ex", "run", "-ex", "bt", "-ex", "kill", "./prog"])
    frames = _gdb_frames(out)
    assert frames[:1] == ["Main#leaf"], out[-4000:]
    assert frames[-1] == "turkey_stack_start", out[-4000:]
