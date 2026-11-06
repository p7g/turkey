"""`boot build -g`: a line table and the DWARF a debugger needs to find it.

`-g` works on both of `build`'s paths. As assembly, the `.file`/`.loc` rows
are what the system assembler turns into `debug_line`; as an object, `boot`
builds `debug_line` itself from the same rows. On both, `Turkey.Dwarf` writes
the compile unit and the subprograms. So the checks are the tools a user would
reach for, on a program built each way: `llvm-dwarfdump` reads the rows and
verifies the DWARF, `nm` shows the debug map the macOS linker writes, and a
debugger stops at a line and names the frames.

**The object's oracle is the assembler.** The object `boot` writes with `-g`
has to carry the debug sections the system assembler makes of `boot`'s own
`-g` assembly, for the test programs and for `boot` itself. On macOS that is
exact: every debug section has the same bytes, and the same relocations.
GNU `as` encodes the line table its own way -- other opcodes for the same
advances, its own file and directory tables -- so on Linux `debug_line` and
`debug_line_str` are compared by the rows they decode to, each row's file by
its full path. The other debug sections are compared byte for byte there too,
and their relocations by what they resolve to: GNU `as` relocates against a
local symbol's section where `boot` names the symbol.

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
import struct
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import bootc, toolchain
from tests.test_encode import _code

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
PROGRAMS = REPO_ROOT / "tests" / "programs"
CORPUS = sorted(path.name for path in PROGRAMS.glob("*.gob") if not path.name.startswith("err_"))

# The two ways `build -g` makes an object: assembly through the system
# assembler, and an object directly.
PATHS = ["asm", "obj"]

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
def _built(how: str) -> Path:
    """`PROGRAM` built with `-g` and linked, in a directory of its own:
    `prog.o` beside the executable as the macOS debug map needs it, and
    `prog`. `how` is `asm`, through `prog.s` and the assembler, or `obj`,
    with `boot` writing the object."""
    directory = Path(tempfile.mkdtemp(prefix="turkey-debug-"))
    source = directory / "prog.gob"
    source.write_text(PROGRAM, encoding="utf-8")
    output = "prog.s" if how == "asm" else "prog.o"
    compiled = subprocess.run(
        toolchain.command(bootc.binary(), "build", "-g", "-f", how, "-o", output,
                          "prog.gob"),
        cwd=directory, env=dict(os.environ, TURKEY_LIB=str(LIB)),
        capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr[-4000:]
    if how == "asm":
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


@pytest.mark.parametrize("how", PATHS)
def test_the_line_table_has_a_row_for_each_statement(how):
    tool = _needs_dwarfdump()
    lines = _rows(tool, _built(how) / "prog.o").get("prog.gob", set())
    assert FACT_LINES | MAIN_LINES <= lines, sorted(lines)


@pytest.mark.parametrize("how", PATHS)
def test_the_object_has_a_compile_unit_and_a_subprogram_per_function(how):
    tool = _needs_dwarfdump()
    text = subprocess.run([tool, "--debug-info", str(_built(how) / "prog.o")],
                          capture_output=True, text=True, check=True).stdout
    assert "DW_TAG_compile_unit" in text
    assert '("prog.gob")' in text
    assert '("Main#fact")' in text
    assert '("Main#main")' in text


@pytest.mark.parametrize("how", PATHS)
@pytest.mark.parametrize("which", ["prog.o", "prog"])
def test_the_dwarf_verifies(how, which):
    tool = _needs_dwarfdump()
    result = subprocess.run([tool, "--verify", str(_built(how) / which)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]


@pytest.mark.parametrize("how", PATHS)
def test_the_macos_linker_records_the_object_in_the_debug_map(how):
    """ld64 writes an `N_OSO` stab, which is how lldb finds the DWARF left
    in the object, only for an object with a compile unit."""
    if not _darwin():
        pytest.skip("the debug map is Mach-O's")
    result = subprocess.run(["nm", "-ap", str(_built(how) / "prog")],
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


def _debugged(how: str, argv: list[str]) -> str:
    """A debugger's batch run of the program, with what it printed. Bounded,
    since a breakpoint that never hits leaves the program to finish and the
    debugger to exit, and one that misbehaves must not hang the suite."""
    result = subprocess.run(argv, cwd=_built(how), capture_output=True, text=True,
                            timeout=120)
    return result.stdout + result.stderr


@pytest.mark.parametrize("how", PATHS)
def test_lldb_stops_at_a_line_and_names_the_frames(how):
    lldb = _lldb()
    out = _debugged(how, [lldb, "--batch", "-o", "breakpoint set --file prog.gob --line 2",
                          "-o", "run", "-o", "bt", "-o", "kill", "./prog"])
    assert re.search(r"frame #0: .*`Main#fact at prog\.gob:2", out), out[-4000:]
    assert re.search(r"frame #1: .*`Main#main at prog\.gob:9", out), out[-4000:]


@pytest.mark.parametrize("how", PATHS)
def test_gdb_stops_at_a_line_and_names_the_frames(how):
    gdb = _gdb()
    out = _debugged(how, [gdb, "-nx", "-batch", "-ex", "break prog.gob:2", "-ex", "run",
                          "-ex", "bt", "-ex", "kill", "./prog"])
    assert re.search(r"#0 .*Main#fact.* at prog\.gob:2", out), out[-4000:]
    assert re.search(r"#1 .*Main#main.* at prog\.gob:9", out), out[-4000:]


# -- the object against the assembler ------------------------------------------

DEBUG_SECTIONS = ["debug_abbrev", "debug_info", "debug_str", "debug_line", "debug_line_str"]
# The sections GNU `as` builds its own way, compared by their rows on ELF.
LINE_SECTIONS = {"debug_line", "debug_line_str"}


class _Object:
    """A relocatable object's sections, by name without the format's prefix
    (`debug_line` for `__debug_line` and `.debug_line`), and the debug
    sections' relocations, each as what it resolves to: its offset, its type,
    its target -- a section or a symbol, by name -- and the constant added to
    the target's address."""

    def __init__(self, data: bytes):
        self.sections: dict[str, bytes] = {}
        self.relocations: dict[str, list[tuple]] = {}
        if data[:4] == b"\x7fELF":
            self._elf(data)
        else:
            self._macho(data)

    def _macho(self, data: bytes) -> None:
        (ncmds,) = struct.unpack_from("<I", data, 16)
        at = 32
        found = []
        symbols, strings = [], b""
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", data, at)
            if cmd == 0x19:
                (nsects,) = struct.unpack_from("<I", data, at + 64)
                for k in range(nsects):
                    s = at + 72 + 80 * k
                    name = data[s:s + 16].rstrip(b"\0").decode().lstrip("_")
                    segment = data[s + 16:s + 32].rstrip(b"\0").decode()
                    _, length, offset, _, reloff, nreloc = struct.unpack_from(
                        "<QQIIII", data, s + 32)
                    found.append((f"{segment},{name}", name, data[offset:offset + length],
                                  reloff, nreloc))
            elif cmd == 0x2:
                symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", data, at + 8)
                strings = data[stroff:stroff + strsize]
                symbols = [struct.unpack_from("<IBBHQ", data, symoff + 16 * k)
                           for k in range(nsyms)]
            at += size
        for full, name, contents, reloff, nreloc in found:
            self.sections[name] = contents
            # An arm64 code relocation can carry an addend in place of a
            # symbol number, so only the debug sections' are read.
            if not full.startswith("__DWARF,"):
                continue
            relocations = []
            for k in range(nreloc):
                address, info = struct.unpack_from("<II", data, reloff + 8 * k)
                index, pcrel = info & 0xFFFFFF, (info >> 24) & 1
                length, extern, kind = (info >> 25) & 3, (info >> 27) & 1, info >> 28
                stored = int.from_bytes(contents[address:address + (1 << length)], "little")
                if extern:
                    n = symbols[index][0]
                    target = "symbol " + strings[n:].split(b"\0", 1)[0].decode()
                else:
                    target = "section " + found[index - 1][0]
                relocations.append((address, kind, pcrel, length, target, stored))
            self.relocations[name] = sorted(relocations)

    def _elf(self, data: bytes) -> None:
        (shoff,) = struct.unpack_from("<Q", data, 0x28)
        shnum, shstrndx = struct.unpack_from("<HH", data, 0x3C)
        headers = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + 64 * k)
                   for k in range(shnum)]
        names_at = headers[shstrndx][4]
        names = [data[names_at + h[0]:].split(b"\0", 1)[0].decode() for h in headers]
        symbols = []
        for h in headers:
            if h[1] == 2:
                symbols = [struct.unpack_from("<IBBHQQ", data, h[4] + 24 * k)
                           for k in range(h[5] // 24)]
        for k, h in enumerate(headers):
            if h[1] != 4:
                self.sections.setdefault(names[k].lstrip("."),
                                         data[h[4]:h[4] + h[5]] if h[1] != 8 else b"")
                continue
            if not names[h[7]].startswith(".debug"):
                continue
            relocations = []
            for r in range(h[5] // 24):
                offset, info, addend = struct.unpack_from("<QQq", data, h[4] + 24 * r)
                _, _, _, section, value, _ = symbols[info >> 32]
                relocations.append((offset, info & 0xFFFFFFFF,
                                    "section " + names[section], value + addend))
            self.relocations[names[h[7]].lstrip(".")] = sorted(relocations)


def _line_rows(tool: str, obj: Path) -> list[tuple]:
    """Every row of `obj`'s line table, in order, each file by its full path:
    the address, line, column, file, and the flags that are set."""
    text = subprocess.run([tool, "--debug-line", str(obj)], capture_output=True,
                          text=True, check=True).stdout
    directories = [m.group(1) for m in
                   re.finditer(r'include_directories\[\s*\d+\] = "([^"]*)"', text)]
    files: dict[int, str] = {}
    for m in re.finditer(r'file_names\[\s*(\d+)\]:\s*\n\s*name:\s*"([^"]*)"\s*\n'
                         r'\s*dir_index:\s*(\d+)', text):
        name, directory = m.group(2), directories[int(m.group(3))]
        files[int(m.group(1))] = os.path.normpath(
            os.path.join(directories[0], directory, name))
    rows = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 4 and re.fullmatch(r"0x[0-9a-f]{16}", fields[0]):
            rows.append((int(fields[0], 16), int(fields[1]), int(fields[2]),
                         files.get(int(fields[3]), "?"), tuple(sorted(fields[7:]))))
    return rows


def _kept_by_gnu_as(rows: list[tuple]) -> list[tuple]:
    """The rows of a line table that GNU `as` writes too.

    GNU `as` drops a `.loc` at line 0, which marks code with no source
    position, so its previous row runs on over that code. An end of sequence
    carries the line of the row before it, which is then a dropped row's, so
    only its address is compared."""
    kept = []
    for row in rows:
        address, line, _, _, flags = row
        if "end_sequence" in flags:
            kept.append((address, "end_sequence"))
        elif line != 0:
            kept.append(row)
    return kept


def _first_difference(ours: bytes, theirs: bytes) -> str:
    at = next((k for k, (a, b) in enumerate(zip(ours, theirs)) if a != b),
              min(len(ours), len(theirs)))
    return (f"{len(ours)} bytes written, {len(theirs)} assembled; first difference at "
            f"{at:#x}: {ours[at:at + 16].hex()} against {theirs[at:at + 16].hex()}")


def _agree(ours: Path, theirs: Path) -> None:
    """`boot`'s object, `ours`, carries the debug sections the assembler put
    in `theirs`, from the same program."""
    mine, assembled = _Object(ours.read_bytes()), _Object(theirs.read_bytes())
    exact = _darwin()
    for name in DEBUG_SECTIONS:
        if name in LINE_SECTIONS and not exact:
            continue
        assert mine.sections[name] == assembled.sections[name], (
            f"{name}: " + _first_difference(mine.sections[name], assembled.sections[name]))
        assert mine.relocations.get(name, []) == assembled.relocations.get(name, []), name
    if not exact:
        tool = _needs_dwarfdump()
        mine_rows = _kept_by_gnu_as(_line_rows(tool, ours))
        assembled_rows = _kept_by_gnu_as(_line_rows(tool, theirs))
        for k, (a, b) in enumerate(zip(mine_rows, assembled_rows)):
            assert a == b, f"row {k}: written {a}, assembled {b}"
        assert len(mine_rows) == len(assembled_rows)


def _object(path: Path) -> bytes:
    """`path` built with `-g` as an object, the way `boot_each` builds its
    assembly: from the repository's root, by its absolute path, so the two
    name the same compile directory and the same main file."""
    result = subprocess.run(
        toolchain.command(bootc.binary(), "build", "-g", "-f", "obj", "-o", "-", str(path)),
        cwd=REPO_ROOT, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()[-4000:]
    return result.stdout


def _compare_with_assembler(path: Path, text: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        source, theirs = Path(directory) / "t.s", Path(directory) / "theirs.o"
        ours = Path(directory) / "ours.o"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run(
            [*toolchain.cc(), *_assemble_flags(), "-c", "-o", str(theirs), str(source)],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[:4000]
        ours.write_bytes(_object(path))
        _agree(ours, theirs)


@pytest.mark.parametrize("name", CORPUS)
def test_the_object_has_the_assemblers_debug_sections(name):
    paths = [PROGRAMS / n for n in CORPUS]
    texts = bootc.boot_each("asm", paths, _split, flags=("-g",))
    _compare_with_assembler(PROGRAMS / name, texts[PROGRAMS / name])


@pytest.mark.bootstrap
def test_the_object_has_the_assemblers_debug_sections_on_the_compiler():
    """`boot` itself: four hundred thousand rows, from every file of the
    compiler and the library."""
    main = bootc.BOOT_MAIN
    _compare_with_assembler(main, bootc.boot_each("asm", [main], _split, flags=("-g",))[main])


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
