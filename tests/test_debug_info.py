"""`boot build -g`: a line table and the DWARF a debugger needs to find it.

`-g` writes assembly whose `.file`/`.loc` rows the system assembler turns into
`debug_line`, and whose compile unit and subprograms `Turkey.Dwarf` writes as
data. So the checks are the tools a user would reach for: `llvm-dwarfdump`
reads the rows and verifies the DWARF, `nm` shows the debug map the macOS
linker writes, and a debugger stops at a line and names the frames.

A tool that is missing skips its test and says which, rather than passing.

The other half is that `-g` changes no code. Positions ride on every
instruction through the backend, so a position that reached a decision would
show up here first: `boot` built with and without `-g` has the same code
section, byte for byte.
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
from tests.test_encode import _code

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"

PROGRAM = """\
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

# The lines of `PROGRAM` that hold a statement of `fact` and of `main`.
FACT_LINES = {2, 3, 5}
MAIN_LINES = {9, 10}

pytestmark = pytest.mark.skipif(toolchain.missing(), reason="no C compiler")


def _darwin() -> bool:
    return toolchain.target() == "arm64-darwin"


def _assemble_flags() -> list[str]:
    """DWARF 5 for the assembler's line table, as the compile unit is. gcc
    passes `-gdwarf-5` on to GNU `as` as `--gdwarf-5`."""
    return ["-gdwarf-5"]


@functools.lru_cache(maxsize=None)
def _dwarfdump() -> str | None:
    """`llvm-dwarfdump`, or macOS's `dwarfdump`, which is the same program."""
    for candidate in ("llvm-dwarfdump", "/opt/homebrew/opt/llvm/bin/llvm-dwarfdump",
                      "/usr/local/opt/llvm/bin/llvm-dwarfdump"):
        if shutil.which(candidate):
            return candidate
    if _darwin() and shutil.which("dwarfdump"):
        return "dwarfdump"
    # Debian and Ubuntu install each LLVM release under its own prefix.
    for found in sorted(Path("/usr/lib").glob("llvm-*/bin/llvm-dwarfdump"), reverse=True):
        return str(found)
    return None


def _needs_dwarfdump() -> str:
    tool = _dwarfdump()
    if tool is None:
        pytest.skip("no llvm-dwarfdump on $PATH to read the DWARF with")
    return tool


@functools.lru_cache(maxsize=None)
def _built() -> Path:
    """`PROGRAM` built with `-g -f asm`, assembled and linked, in a directory
    of its own: `prog.s`, `prog.o` beside the executable as the macOS debug
    map needs it, and `prog`."""
    directory = Path(tempfile.mkdtemp(prefix="turkey-debug-"))
    source = directory / "prog.gob"
    source.write_text(PROGRAM, encoding="utf-8")
    compiled = subprocess.run(
        toolchain.command(bootc.binary(), "build", "-g", "-f", "asm", "-o", "prog.s",
                          "prog.gob"),
        cwd=directory, env=dict(os.environ, TURKEY_LIB=str(LIB)),
        capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr[-4000:]
    assembled = subprocess.run(
        [*toolchain.cc(), *_assemble_flags(), "-c", "-o", "prog.o", "prog.s"],
        cwd=directory, capture_output=True, text=True)
    assert assembled.returncode == 0, assembled.stderr[-4000:]
    linked = subprocess.run(
        [*toolchain.cc(), "-o", "prog", "prog.o", *toolchain.libraries()],
        cwd=directory, capture_output=True, text=True)
    assert linked.returncode == 0, linked.stderr[-4000:]
    return directory


def _rows(tool: str, obj: Path) -> dict[str, set[int]]:
    """Each file's lines that start a row of `obj`'s line table, by the
    file's name without its directory."""
    text = subprocess.run([tool, "--debug-line", str(obj)], capture_output=True,
                          text=True, check=True).stdout
    names: dict[int, str] = {}
    for match in re.finditer(r'file_names\[\s*(\d+)\]:\s*\n\s*name:\s*"([^"]*)"', text):
        names[int(match.group(1))] = match.group(2).rsplit("/", 1)[-1]
    rows: dict[str, set[int]] = {}
    for line in text.splitlines():
        # Address, line, column, file, ISA, discriminator, op index, flags.
        fields = line.split()
        if len(fields) >= 4 and re.fullmatch(r"0x[0-9a-f]{16}", fields[0]):
            name = names.get(int(fields[3]), "?")
            rows.setdefault(name, set()).add(int(fields[1]))
    return rows


def test_the_line_table_has_a_row_for_each_statement():
    tool = _needs_dwarfdump()
    lines = _rows(tool, _built() / "prog.o").get("prog.gob", set())
    assert FACT_LINES | MAIN_LINES <= lines, sorted(lines)


def test_the_object_has_a_compile_unit_and_a_subprogram_per_function():
    tool = _needs_dwarfdump()
    text = subprocess.run([tool, "--debug-info", str(_built() / "prog.o")],
                          capture_output=True, text=True, check=True).stdout
    assert "DW_TAG_compile_unit" in text
    assert '("prog.gob")' in text
    assert '("Main#fact")' in text
    assert '("Main#main")' in text


@pytest.mark.parametrize("which", ["prog.o", "prog"])
def test_the_dwarf_verifies(which):
    tool = _needs_dwarfdump()
    result = subprocess.run([tool, "--verify", str(_built() / which)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]


def test_the_macos_linker_records_the_object_in_the_debug_map():
    """ld64 writes an `N_OSO` stab, which is how lldb finds the DWARF left
    in the object, only for an object with a compile unit."""
    if not _darwin():
        pytest.skip("the debug map is Mach-O's")
    result = subprocess.run(["nm", "-ap", str(_built() / "prog")],
                            capture_output=True, text=True, check=True)
    assert re.search(r" OSO .*prog\.o$", result.stdout, re.MULTILINE), result.stdout[-2000:]


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


def _debugged(argv: list[str]) -> str:
    """A debugger's batch run of the program, with what it printed. Bounded,
    since a breakpoint that never hits leaves the program to finish and the
    debugger to exit, and one that misbehaves must not hang the suite."""
    result = subprocess.run(argv, cwd=_built(), capture_output=True, text=True,
                            timeout=120)
    return result.stdout + result.stderr


def test_lldb_stops_at_a_line_and_names_the_frames():
    lldb = _lldb()
    out = _debugged([lldb, "--batch", "-o", "breakpoint set --file prog.gob --line 2",
                     "-o", "run", "-o", "bt", "-o", "kill", "./prog"])
    assert re.search(r"frame #0: .*`Main#fact at prog\.gob:2", out), out[-4000:]
    assert re.search(r"frame #1: .*`Main#main at prog\.gob:9", out), out[-4000:]


def test_gdb_stops_at_a_line_and_names_the_frames():
    gdb = _gdb()
    out = _debugged([gdb, "-nx", "-batch", "-ex", "break prog.gob:2", "-ex", "run",
                     "-ex", "bt", "-ex", "kill", "./prog"])
    assert re.search(r"#0 .*Main#fact.* at prog\.gob:2", out), out[-4000:]
    assert re.search(r"#1 .*Main#main.* at prog\.gob:9", out), out[-4000:]


def test_an_object_is_refused_with_debug_information():
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "prog.gob").write_text(PROGRAM, encoding="utf-8")
        result = subprocess.run(
            toolchain.command(bootc.binary(), "build", "-g", "-f", "obj", "-o", "prog.o",
                              "prog.gob"),
            cwd=directory, env=dict(os.environ, TURKEY_LIB=str(LIB)),
            capture_output=True, text=True)
    assert result.returncode != 0
    assert "cannot write debug information into an object" in result.stderr


def _split(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "// === ")
    return [modules[p.name] for p in paths]


def _assembled_code(text: str, flags: list[str]) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "t.s"
        obj = Path(directory) / "t.o"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run([*toolchain.cc(), *flags, "-c", "-o", str(obj), str(source)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[:4000]
        return _code(obj.read_bytes())


@pytest.mark.bootstrap
def test_debug_information_changes_no_code():
    """`boot` itself, with and without `-g`: the same code section. Every
    debug-information change keeps this test, since it is the one that sees a
    position reach code generation."""
    main = bootc.BOOT_MAIN
    plain = bootc.boot_each("asm", [main], _split)[main]
    debug = bootc.boot_each("asm", [main], _split, flags=("-g",))[main]
    assert ".loc " in debug and ".loc " not in plain
    plain_code = _assembled_code(plain, [])
    debug_code = _assembled_code(debug, _assemble_flags())
    assert len(plain_code) == len(debug_code)
    assert plain_code == debug_code
