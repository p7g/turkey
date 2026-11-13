"""Unwind information: what lldb, gdb, `sample`, `perf` and the C library read
to find a function's caller from any instruction in it.

Every build carries it, `-g` or not. On Mach-O it is a compact unwind entry
per function, frame mode for all but `turkey_swap_stack`, whose rules change
from one instruction to the next and so are a DWARF description in
`__eh_frame`. On ELF it is `.eh_frame`, exact at every instruction.

**The object's oracle is the assembler**, as for the line table: the unwind
sections `boot` writes into an object are the ones the system assembler makes
of `boot`'s own assembly, whose `.cfi_*` directives say the same rules, for
the test programs and for `boot` itself. Byte for byte, with the relocations
compared by what they resolve to, since the assembler names a section's
start by a symbol of its own. Where the assembler is not the format's own --
LLVM writing ELF, on macOS -- the two `.eh_frame`s are compared by the rows
they decode to instead: LLVM lays the same rules out with other factors.

Then the tools: `objdump` and `readelf` show an entry for every function, and
a debugger stopped on a function's first instruction or on its `ret` -- where
frame mode does not hold, and lldb reads the code instead -- shows the whole
stack, which inside a coroutine ends at the coroutine's base.
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
from tests.test_debug_info import _first_difference, _split

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "lib"
PROGRAMS = REPO_ROOT / "tests" / "programs"
CORPUS = [path.name for path in bootc.corpus()]

# Assembly through the system assembler and an object `boot` writes, each
# linked by the C compiler, and an executable `boot` writes with nothing
# linked.
PATHS = ["asm", "obj", "exe"]

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


# -- reading an object's unwind sections ---------------------------------------

UNWIND_SECTIONS = ["compact_unwind", "eh_frame"]


class _Object:
    """A relocatable object's sections, by name without the format's prefix,
    and the unwind sections' relocations, each as what it resolves to: its
    offset, its type, its target -- a section, with the offset in it a
    defined symbol names, or an undefined symbol by name -- and the value
    stored in the field or carried as the addend."""

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
                    address, length, offset, _, reloff, nreloc = struct.unpack_from(
                        "<QQIIII", data, s + 32)
                    found.append((name, address, data[offset:offset + length], reloff, nreloc))
            elif cmd == 0x2:
                symoff, nsyms, stroff, strsize = struct.unpack_from("<IIII", data, at + 8)
                strings = data[stroff:stroff + strsize]
                symbols = [struct.unpack_from("<IBBHQ", data, symoff + 16 * k)
                           for k in range(nsyms)]
            at += size
        for name, _, contents, reloff, nreloc in found:
            self.sections[name] = contents
            if name not in UNWIND_SECTIONS:
                continue
            relocations = []
            for k in range(nreloc):
                address, info = struct.unpack_from("<II", data, reloff + 8 * k)
                index, pcrel = info & 0xFFFFFF, (info >> 24) & 1
                length, extern, kind = (info >> 25) & 3, (info >> 27) & 1, info >> 28
                stored = int.from_bytes(contents[address:address + (1 << length)], "little")
                if extern:
                    n, _, section, _, value = symbols[index]
                    if section == 0:
                        target = "symbol " + strings[n:].split(b"\0", 1)[0].decode()
                    else:
                        owner, start = found[section - 1][0], found[section - 1][1]
                        target = f"section {owner} + {value - start}"
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
            if names[h[7]].lstrip(".") not in UNWIND_SECTIONS:
                continue
            relocations = []
            for r in range(h[5] // 24):
                offset, info, addend = struct.unpack_from("<QQq", data, h[4] + 24 * r)
                _, _, _, section, value, _ = symbols[info >> 32]
                relocations.append((offset, info & 0xFFFFFFFF,
                                    "section " + names[section], value + addend))
            self.relocations[names[h[7]].lstrip(".")] = sorted(relocations)


def _uleb(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, at


def _sleb(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            if byte & 0x40:
                value -= 1 << shift
            return value, at


def _rows(cie: dict, program: bytes, initial: tuple) -> list:
    """The rows `program` describes from the state `initial`, each as its
    offset in bytes from the function's start, the CFA rule and every
    register's rule: the last row at each offset, and none that changes
    nothing."""
    cfa, regs = initial[0], dict(initial[1])
    start_regs = cie["regs"]
    stack: list = []
    loc = 0
    rows: list = [(0, cfa, tuple(sorted(regs.items())))]

    def row() -> None:
        state = (cfa, tuple(sorted(regs.items())))
        if rows and rows[-1][0] == loc:
            rows.pop()
        if not rows or rows[-1][1:] != state:
            rows.append((loc, *state))

    at = 0
    code, data = cie["code"], cie["data"]
    while at < len(program):
        op = program[at]
        at += 1
        high, low = op >> 6, op & 0x3F
        if high == 1:
            loc += low * code
        elif high == 2:
            n, at = _uleb(program, at)
            regs[low] = ("at", n * data)
        elif high == 3:
            regs.pop(low, None)
            if low in start_regs:
                regs[low] = start_regs[low]
        elif op == 0x00:
            continue
        elif op in (0x02, 0x03, 0x04):
            width = {0x02: 1, 0x03: 2, 0x04: 4}[op]
            loc += int.from_bytes(program[at:at + width], "little") * code
            at += width
        elif op == 0x05:
            reg, at = _uleb(program, at)
            n, at = _uleb(program, at)
            regs[reg] = ("at", n * data)
        elif op == 0x11:
            reg, at = _uleb(program, at)
            n, at = _sleb(program, at)
            regs[reg] = ("at", n * data)
        elif op == 0x06:
            reg, at = _uleb(program, at)
            regs.pop(reg, None)
            if reg in start_regs:
                regs[reg] = start_regs[reg]
        elif op == 0x07:
            reg, at = _uleb(program, at)
            regs[reg] = ("undefined",)
        elif op == 0x08:
            reg, at = _uleb(program, at)
            regs.pop(reg, None)
        elif op == 0x0A:
            stack.append((cfa, dict(regs)))
        elif op == 0x0B:
            cfa, regs = stack.pop()
        elif op == 0x0C:
            reg, at = _uleb(program, at)
            n, at = _uleb(program, at)
            cfa = (reg, n)
        elif op == 0x0D:
            reg, at = _uleb(program, at)
            cfa = (reg, cfa[1])
        elif op == 0x0E:
            n, at = _uleb(program, at)
            cfa = (cfa[0], n)
        else:
            raise AssertionError(f"call frame instruction {op:#x} is not decoded here")
        row()
    return rows


def _frames(data: bytes) -> dict[int, tuple]:
    """An ELF object's `.eh_frame`, decoded: for each function, by its
    offset in `.text`, its length and its rows."""
    obj = _Object(data)
    eh = obj.sections["eh_frame"]
    starts = {offset: value for offset, _, _, value in obj.relocations["eh_frame"]}
    cies: dict[int, dict] = {}
    frames: dict[int, tuple] = {}
    at = 0
    while at < len(eh):
        (length,) = struct.unpack_from("<I", eh, at)
        if length == 0:
            break
        body, end = at + 4, at + 4 + length
        (pointer,) = struct.unpack_from("<I", eh, body)
        if pointer == 0:
            p = body + 4
            version = eh[p]
            p += 1
            augmentation = eh[p:eh.index(b"\0", p)].decode()
            p += len(augmentation) + 1
            code, p = _uleb(eh, p)
            data_align, p = _sleb(eh, p)
            if version == 1:
                p += 1
            else:
                _, p = _uleb(eh, p)
            encoding = 0
            if augmentation.startswith("z"):
                size, p = _uleb(eh, p)
                if "R" in augmentation:
                    encoding = eh[p]
                p += size
            cie: dict = {"code": code, "data": data_align, "encoding": encoding, "regs": {}}
            last = _rows(cie, eh[p:end], ((31, 0), ()))[-1]
            cie["initial"] = (last[1], last[2])
            cie["regs"] = dict(last[2])
            cies[at] = cie
        else:
            cie = cies[body - pointer]
            field = body + 4
            size = 4 if cie["encoding"] & 0x0F == 0x0B else 8
            span = int.from_bytes(eh[field + size:field + 2 * size], "little")
            p = field + 2 * size
            extra, p = _uleb(eh, p)
            p += extra
            frames[starts[field]] = (span, tuple(_rows(cie, eh[p:end], cie["initial"])))
        at = end
    return frames


# -- the object against the assembler ------------------------------------------

def _agree(ours: bytes, theirs: bytes, exact: bool) -> None:
    """`boot`'s object, `ours`, carries the unwind sections the assembler put
    in `theirs`: the same bytes and relocations if `exact`, and on ELF the
    same rows either way."""
    mine, assembled = _Object(ours), _Object(theirs)
    names = [n for n in UNWIND_SECTIONS if n in mine.sections or n in assembled.sections]
    assert names, "no unwind section in either object"
    if exact:
        for name in names:
            assert name in mine.sections, f"{name}: not written"
            assert mine.sections[name] == assembled.sections.get(name), (
                f"{name}: " + _first_difference(mine.sections[name],
                                                assembled.sections.get(name, b"")))
            assert mine.relocations.get(name, []) == assembled.relocations.get(name, []), name
    if ours[:4] == b"\x7fELF":
        written, expected = _frames(ours), _frames(theirs)
        for start in sorted(set(written) | set(expected)):
            assert written.get(start) == expected.get(start), (
                f"the function at .text + {start:#x}: written {written.get(start)}, "
                f"assembled {expected.get(start)}")


def _object(path: Path, *flags: str) -> bytes:
    result = subprocess.run(
        toolchain.command(bootc.binary(), "build", *flags, "-f", "obj", "-o", "-", str(path)),
        cwd=REPO_ROOT, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()[-4000:]
    return result.stdout


def _assembled(text: str, assembler: list[str]) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        source, obj = Path(directory) / "t.s", Path(directory) / "t.o"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run([*assembler, "-c", "-o", str(obj), str(source)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[:4000]
        return obj.read_bytes()


@pytest.mark.parametrize("name", CORPUS)
def test_the_object_has_the_assemblers_unwind_sections(name):
    paths = [PROGRAMS / n for n in CORPUS]
    texts = bootc.boot_each("asm", paths, _split)
    path = PROGRAMS / name
    _agree(_object(path), _assembled(texts[path], toolchain.cc()), exact=True)


@pytest.mark.bootstrap
def test_the_object_has_the_assemblers_unwind_sections_on_the_compiler():
    main = bootc.BOOT_MAIN
    text = bootc.boot_each("asm", [main], _split)[main]
    _agree(_object(main), _assembled(text, toolchain.cc()), exact=True)


@functools.lru_cache(maxsize=None)
def _llvm_elf() -> list[str] | None:
    """A clang that assembles for arm64 Linux, which is how ELF's unwind
    sections are checked on a Mac."""
    clang = shutil.which("clang")
    if clang is None:
        return None
    command = [clang, "--target=aarch64-linux-gnu"]
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "t.s"
        source.write_text("    .text\nf:\n    .cfi_startproc\n    ret\n    .cfi_endproc\n")
        result = subprocess.run([*command, "-c", "-o", str(Path(directory) / "t.o"),
                                 str(source)], capture_output=True)
    return command if result.returncode == 0 else None


@pytest.mark.parametrize("name", CORPUS)
def test_the_elf_object_has_llvms_unwind_rows(name):
    """ELF from either platform, against LLVM's assembler: the same rows,
    though not the same bytes."""
    if toolchain.target() == "arm64-linux":
        pytest.skip("GNU as is the oracle on Linux, byte for byte")
    assembler = _llvm_elf()
    if assembler is None:
        pytest.skip("no clang that assembles for aarch64-linux-gnu")
    paths = [PROGRAMS / n for n in CORPUS]
    texts = bootc.boot_each("asm", paths, _split, flags=("--target", "arm64-linux"))
    path = PROGRAMS / name
    _agree(_object(path, "--target", "arm64-linux"), _assembled(texts[path], assembler),
           exact=False)


# -- the tools -----------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _built(program: str, how: str) -> Path:
    """A program of `SOURCES` as `prog`, in a directory of its own: linked
    beside its object, through the assembler for `asm` or written by `boot`
    for `obj`, and not position-independent on Linux, so that the addresses
    `nm` reads are the ones gdb stops at; or written by `boot` for `exe`."""
    if how == "exe" and not _darwin():
        pytest.skip("the ELF executable boot writes carries no .eh_frame yet")
    directory = Path(tempfile.mkdtemp(prefix="turkey-unwind-"))
    (directory / "prog.gob").write_text(SOURCES[program], encoding="utf-8")
    output = {"asm": "prog.s", "obj": "prog.o", "exe": "prog"}[how]
    compiled = subprocess.run(
        toolchain.command(bootc.binary(), "build", "-f", how, "-o", output, "prog.gob"),
        cwd=directory, env=dict(os.environ, TURKEY_LIB=str(LIB)),
        capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr[-4000:]
    if how == "exe":
        return directory
    if how == "asm":
        assembled = subprocess.run([*toolchain.cc(), "-c", "-o", "prog.o", "prog.s"],
                                   cwd=directory, capture_output=True, text=True)
        assert assembled.returncode == 0, assembled.stderr[-4000:]
    flags = [] if _darwin() else ["-no-pie"]
    linked = subprocess.run(
        [*toolchain.cc(), *flags, "-o", "prog", "prog.o", *toolchain.libraries()],
        cwd=directory, capture_output=True, text=True)
    assert linked.returncode == 0, linked.stderr[-4000:]
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
    """The code symbols of an object, by name, with their addresses."""
    return {name: address for address, kind, name in _symbols(path)
            if kind in "Tt" and not name.startswith(("ltmp", "$x", "$d"))}


def _check_encodings(functions: dict[str, int], encoding_of) -> None:
    """Frame mode for every function, but frameless for the C faces' Turkey
    entries, which make no frame, and DWARF for `turkey_swap_stack`, whose
    low 24 bits are 0 in an object and its FDE's offset in an image."""
    for name, address in functions.items():
        encoding = encoding_of(address)
        if name == "turkey_swap_stack":
            assert encoding & 0x0F000000 == 0x03000000, f"{name}: {encoding:#x}"
        elif name.startswith("turkey."):
            assert encoding == 0x02000000, name
        else:
            assert encoding & 0x0F000000 == 0x04000000, f"{name}: {encoding:#x}"


@pytest.mark.parametrize("how", PATHS)
def test_every_function_has_a_compact_unwind_entry(how):
    if not _darwin():
        pytest.skip("compact unwind is Mach-O's")
    directory = _built("task", how)
    if how == "exe":
        # An image's entry stands for every function up to the next entry,
        # so each function's encoding is the last entry's at or below it.
        out = subprocess.run(["objdump", "--macho", "--unwind-info", str(directory / "prog")],
                             capture_output=True, text=True, check=True).stdout
        entries = sorted((0x100000000 + int(start, 16), int(encoding, 16))
                         for start, encoding in re.findall(
                             r"function offset=(0x[0-9a-f]+), encoding=(0x[0-9a-f]+)", out))
        functions = _functions(directory / "prog")
        assert entries and entries[0][0] == min(functions.values()), out[:2000]
        _check_encodings(functions, lambda address: next(
            e for a, e in reversed(entries) if a <= address))
        return
    out = subprocess.run(["objdump", "--macho", "--unwind-info", str(directory / "prog.o")],
                         capture_output=True, text=True, check=True).stdout
    entries = {int(start, 16): int(encoding, 16) for start, encoding in re.findall(
        r"start:\s+(0x[0-9a-f]+).*\n.*\n\s+compact encoding:\s+(0x[0-9a-f]+)", out)}
    functions = _functions(directory / "prog.o")
    assert sorted(entries) == sorted(set(functions.values())), (
        "functions with no entry: "
        + str(sorted(n for n, a in functions.items() if a not in entries)))
    assert entries[functions["turkey_swap_stack"]] == 0x03000000
    _check_encodings(functions, entries.__getitem__)
    linked = subprocess.run(["objdump", "--macho", "--unwind-info", str(directory / "prog")],
                            capture_output=True, text=True, check=True).stdout
    assert "Contents of __unwind_info section" in linked


@pytest.mark.parametrize("how", PATHS)
def test_every_function_has_an_fde(how):
    if _darwin():
        pytest.skip(".eh_frame is the only unwind information on ELF")
    if shutil.which("readelf") is None:
        pytest.skip("no readelf on $PATH")
    directory = _built("task", how)
    out = subprocess.run(["readelf", "--debug-dump=frames", str(directory / "prog")],
                         capture_output=True, text=True, check=True).stdout
    starts = {int(start, 16) for start in re.findall(r"FDE cie=\S+ pc=([0-9a-f]+)\.\.", out)}
    ours = set(_functions(directory / "prog.o"))
    functions = {name: address for name, address in _functions(directory / "prog").items()
                 if name in ours}
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
    """The addresses in the linked program to stop at in `function`: its
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


@pytest.mark.parametrize("how", PATHS)
@pytest.mark.parametrize("where", ["first", "ret"])
def test_lldb_shows_the_whole_stack_on_entry_and_return(how, where):
    lldb = _lldb()
    directory = _built("fact", how)
    address = _stops(directory, "Main#fact")[where]
    out = _debugged(directory, [lldb, "--batch", "-o", f"breakpoint set -s prog -a {address:#x}",
                                "-o", "run", "-o", "bt", "-o", "kill", "./prog"])
    frames = _lldb_frames(out)
    assert frames[:1] == ["Main#fact"], out[-4000:]
    assert "Main#main" in frames and "turkey_entry" in frames, out[-4000:]
    callers = frames[1:frames.index("Main#main")]
    assert all(f == "Main#fact" for f in callers), out[-4000:]


@pytest.mark.parametrize("how", PATHS)
@pytest.mark.parametrize("where", ["first", "ret"])
def test_gdb_shows_the_whole_stack_on_entry_and_return(how, where):
    gdb = _gdb()
    directory = _built("fact", how)
    address = _stops(directory, "Main#fact")[where]
    out = _debugged(directory, [gdb, "-nx", "-batch", "-ex", f"break *{address:#x}",
                                "-ex", "run", "-ex", "bt", "-ex", "kill", "./prog"])
    frames = _gdb_frames(out)
    assert frames[:1] == ["Main#fact"], out[-4000:]
    assert "Main#main" in frames and "turkey_entry" in frames, out[-4000:]
    callers = frames[1:frames.index("Main#main")]
    assert all(f == "Main#fact" for f in callers), out[-4000:]
    assert "Backtrace stopped" not in out, out[-4000:]


@pytest.mark.parametrize("how", PATHS)
def test_lldb_ends_a_coroutines_stack_at_its_base(how):
    lldb = _lldb()
    directory = _built("task", how)
    address = _stops(directory, "Main#leaf")["first"]
    out = _debugged(directory, [lldb, "--batch", "-o", f"breakpoint set -s prog -a {address:#x}",
                                "-o", "run", "-o", "bt", "-o", "kill", "./prog"])
    frames = _lldb_frames(out)
    assert frames[:1] == ["Main#leaf"], out[-4000:]
    assert frames[-1] == "turkey_stack_start", out[-4000:]


@pytest.mark.parametrize("how", PATHS)
def test_gdb_ends_a_coroutines_stack_at_its_base(how):
    gdb = _gdb()
    directory = _built("task", how)
    address = _stops(directory, "Main#leaf")["first"]
    out = _debugged(directory, [gdb, "-nx", "-batch", "-ex", f"break *{address:#x}",
                                "-ex", "run", "-ex", "bt", "-ex", "kill", "./prog"])
    frames = _gdb_frames(out)
    assert frames[:1] == ["Main#leaf"], out[-4000:]
    assert frames[-1] == "turkey_stack_start", out[-4000:]
