"""`boot build -g`: a line table and the DWARF a debugger needs to find it.

`-g` works on both of `build`'s paths. As assembly, the `.file`/`.loc` rows
are what the system assembler turns into `debug_line`; as an object, `boot`
builds `debug_line` itself from the same rows. On both, `Turkey.Dwarf` writes
the compile unit, the subprograms, and an inlined subroutine for each call the
optimizer inlined. So the checks are the tools a user would reach for, on a
program built each way: `llvm-dwarfdump` reads the rows and verifies the
DWARF, `nm` shows the debug map the macOS linker writes, a debugger stops at a
line and names the frames, inlined calls among them, and on macOS `atos`, which
reads DWARF the way `sample` and Instruments do, names the inlined frames
too.

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
CORPUS = [path.name for path in bootc.corpus()]

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

# The README's example, whose `parseRectangle` and `area` the optimizer inlines
# into `main`: `area` is called on line 18 and its body is line 11.
RECTANGLES = """\
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
"""

# A loop whose body is a call inlined into a call inlined into `main`, and
# whose code the inlined calls cover in more than one range.
LOOP = """\
fun step(x : Int) -> Int = (x * 1103515245 + 12345) % 2147483648

fun spin(n : Int) -> Int {
    var x = 1
    for var i = 0; i < n; i = i + 1 {
        x = step(x)
    }
    x
}

fun main() {
    print(spin(1000))
}
"""

# A function a debugger stops in twice, with a variable of each kind of type
# live at both stops: `look` calls itself, so it is not inlined, and every
# parameter is read after the second stop. The strings, the vector and the
# tree are live across calls, so the collector roots them and they move from
# their registers into frame slots.
VALUES = """\
type Point = Point { x : Int, y : Int }
type Color = Red | Green | Blue
type Tree = Leaf | Node(Tree, Int, Tree)

fun depth(t : Tree) -> Int = match t {
    Leaf -> 0
    Node(l, _, r) -> 1 + depth(l) + depth(r)
}

fun look(n : Int, s : String, p : Point, c : Color, t : Tree, o : Option String,
         v : Vec Int) -> Int {
    if n > 1000 { return look(n - 1, s, p, c, t, o, v) }
    let total = n * 2
    print(s)
    let d = depth(t)
    print(Int.toString(len(v)))
    print(s + Option.unwrapOr(o, ""))
    let shade = match c {
        Red -> 1
        Green -> 2
        Blue -> 3
    }
    print(s)
    n + total + d + p.x + p.y + shade + len(v) + String.byteLength(Option.unwrapOr(o, ""))
        + depth(t)
}

fun main() {
    let v = Vec.new(4)
    for x in [1, 2, 3] { Vec.push(v, x * 10) }
    let tree = Node(Node(Leaf, 1, Leaf), 2, Leaf)
    print(look(5, "hi", Point { x = 3, y = 4 }, Green, tree, Some("there"), v))
}
"""

# The lines of `VALUES` a debugger stops at: before the first call, and
# after two more.
FIRST_STOP = 15
SECOND_STOP = 18

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
def _built(how: str, program: str = PROGRAM, level: str = "-g") -> Path:
    """`program` built with `level`, `-g` or a `--debug`, and linked, in a
    directory of its own: `prog.o` beside the executable as the macOS debug
    map needs it, and `prog`. `how` is `asm`, through `prog.s` and the
    assembler, or `obj`, with `boot` writing the object."""
    directory = Path(tempfile.mkdtemp(prefix="turkey-debug-"))
    source = directory / "prog.gob"
    source.write_text(program, encoding="utf-8")
    output = "prog.s" if how == "asm" else "prog.o"
    compiled = subprocess.run(
        toolchain.command(bootc.binary(), "build", level, "-f", how, "-o", output,
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
@pytest.mark.parametrize("level", ["-g", "--debug=lines"])
def test_the_object_has_a_compile_unit_and_a_subprogram_per_function(how, level):
    tool = _needs_dwarfdump()
    text = subprocess.run([tool, "--debug-info", str(_built(how, PROGRAM, level) / "prog.o")],
                          capture_output=True, text=True, check=True).stdout
    assert "DW_TAG_compile_unit" in text
    assert '("prog.gob")' in text
    assert '("Main#fact")' in text
    assert '("Main#main")' in text


@pytest.mark.parametrize("how", PATHS)
@pytest.mark.parametrize("which", ["prog.o", "prog"])
@pytest.mark.parametrize("program", [PROGRAM, RECTANGLES, LOOP], ids=["fact", "rectangles", "loop"])
@pytest.mark.parametrize("level", ["-g", "--debug=lines"])
def test_the_dwarf_verifies(how, which, program, level):
    tool = _needs_dwarfdump()
    result = subprocess.run([tool, "--verify", str(_built(how, program, level) / which)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]


def _entries(tool: str, obj: Path) -> list[tuple[int, str, str]]:
    """Every entry of `obj`'s `debug_info`, in order, as its depth, its tag,
    and the text of its attributes."""
    text = subprocess.run([tool, "--debug-info", str(obj)], capture_output=True,
                          text=True, check=True).stdout
    entries = []
    for match in re.finditer(r"^0x[0-9a-f]+:( +)(DW_TAG_\w+)\n((?:[ \t]+\S.*\n)*)", text,
                             re.MULTILINE):
        entries.append((len(match.group(1)), match.group(2), match.group(3)))
    return entries


@pytest.mark.parametrize("how", PATHS)
def test_an_inlined_call_is_an_inlined_subroutine_of_its_caller(how):
    """`area`'s body in `main`: an inlined subroutine inside `main`'s
    subprogram, naming the abstract subprogram `area` and the line of the
    call."""
    tool = _needs_dwarfdump()
    entries = _entries(tool, _built(how, RECTANGLES) / "prog.o")
    main = next(k for k, (_, tag, attributes) in enumerate(entries)
                if tag == "DW_TAG_subprogram" and '("Main#main")' in attributes)
    depth = entries[main][0]
    inside = []
    for entry in entries[main + 1:]:
        if entry[0] <= depth:
            break
        inside.append(entry)
    area = [attributes for _, tag, attributes in inside
            if tag == "DW_TAG_inlined_subroutine" and '"Main#area@Rectangle")' in attributes]
    assert area, "\n".join(a for _, _, a in inside)[-4000:]
    assert any(re.search(r"DW_AT_call_line\s+\(18\)", a) for a in area), area
    assert any(tag == "DW_TAG_subprogram" and '("Main#area@Rectangle")' in attributes
               and "DW_INL_inlined" in attributes for _, tag, attributes in entries)


@pytest.mark.parametrize("how", PATHS)
def test_a_parameter_has_a_location_list(how):
    """`fact`'s `n`: a formal parameter whose location list starts in the
    register it arrives in."""
    tool = _needs_dwarfdump()
    entries = _entries(tool, _built(how) / "prog.o")
    n = [attributes for _, tag, attributes in entries
         if tag == "DW_TAG_formal_parameter" and '("n")' in attributes]
    assert n, "no formal parameter 'n'"
    assert any(re.search(r"DW_AT_location\s+\(indexed .* loclist", a) and "DW_OP_reg" in a
               for a in n), n


@pytest.mark.parametrize("how", PATHS)
def test_the_types_describe_how_values_are_held(how):
    """An `Int` is a name for a C `long`; an enumeration is an enumeration of
    its constructors; a record in its slots is a structure of its fields; a
    boxed sum is a pointer to its object, whose tag is an enumeration and
    whose constructors are a union; a compact `Option` is a union of its
    word as its nullaries and as its payload."""
    tool = _needs_dwarfdump()
    entries = _entries(tool, _built(how, VALUES) / "prog.o")
    def named(tag: str, name: str) -> list[str]:
        return [a for _, t, a in entries if t == tag and f'DW_AT_name\t("{name}")' in a]
    assert named("DW_TAG_typedef", "Int") and named("DW_TAG_base_type", "long")
    assert named("DW_TAG_enumeration_type", "Color")
    assert named("DW_TAG_enumerator", "Green")
    assert named("DW_TAG_structure_type", "Point")
    assert named("DW_TAG_member", "x") and named("DW_TAG_member", "y")
    assert named("DW_TAG_structure_type", "Tree")
    assert named("DW_TAG_enumeration_type", "Tree tag")
    assert named("DW_TAG_union_type", "Tree")
    assert named("DW_TAG_union_type", "Option String")
    assert named("DW_TAG_typedef", "String") and named("DW_TAG_typedef", "Vec Int")


def _locals(text: str) -> dict[str, str]:
    """Each `name = value` a debugger printed on a line of its own, nested
    or not, the last one printed for each name."""
    found = {}
    for match in re.finditer(r"^\s*(?:\([^)]*\) )?(\w+) = (.*)$", text, re.MULTILINE):
        found[match.group(1)] = match.group(2).strip()
    return found


def _stops(out: str, marker: str) -> list[str]:
    """The text between each line that is `marker` and the next."""
    return re.split(f"^{re.escape(marker)}$", out, flags=re.MULTILINE)[1:]


@pytest.mark.parametrize("how", PATHS)
def test_lldb_shows_the_variables_at_each_stop(how):
    lldb = _lldb()
    out = _debugged(how, [lldb, "--batch",
                          "-o", f"breakpoint set --file prog.gob --line {FIRST_STOP}",
                          "-o", f"breakpoint set --file prog.gob --line {SECOND_STOP}",
                          "-o", "run", "-o", "script print('== stop')",
                          "-o", "frame variable n c p total",
                          "-o", "frame variable *t", "-o", "frame variable -- *s",
                          "-o", "continue", "-o", "script print('== stop')",
                          "-o", "frame variable n c p total d",
                          "-o", "frame variable *v", "-o", "frame variable -- *s",
                          "-o", "kill", "./prog"], VALUES)
    stops = _stops(out, "== stop")
    assert len(stops) == 2, out[-4000:]
    for stop in stops:
        seen = _locals(stop)
        assert seen.get("n") == "5", stop
        assert seen.get("c") == "Green", stop
        assert seen.get("x") == "3" and seen.get("y") == "4", stop
        assert seen.get("total") == "10", stop
        assert seen.get("length") in ("2", "3"), stop
    assert _locals(stops[0]).get("tag") == "Node", stops[0]
    assert '"hi"' in stops[0] and '"hi"' in stops[1]
    assert _locals(stops[1]).get("d") == "2", stops[1]
    assert re.search(r"length = 3", stops[1]), stops[1]


@pytest.mark.parametrize("how", PATHS)
def test_gdb_shows_the_variables_at_each_stop(how):
    gdb = _gdb()
    out = _debugged(how, [gdb, "-nx", "-batch",
                          "-ex", f"break prog.gob:{FIRST_STOP}",
                          "-ex", f"break prog.gob:{SECOND_STOP}",
                          "-ex", "run", "-ex", "echo == stop\\n", "-ex", "info args",
                          "-ex", "info locals", "-ex", "print *t",
                          "-ex", "continue", "-ex", "echo == stop\\n", "-ex", "info args",
                          "-ex", "info locals", "-ex", "print *v",
                          "-ex", "kill", "./prog"], VALUES)
    stops = _stops(out, "== stop")
    assert len(stops) == 2, out[-4000:]
    for stop in stops:
        seen = _locals(stop)
        assert seen.get("n") == "5", stop
        assert seen.get("c") == "Green", stop
        assert re.search(r"p = \{x = 3, y = 4\}", stop), stop
        assert seen.get("total") == "10", stop
    assert re.search(r"tag = Node", stops[0]), stops[0]
    assert _locals(stops[1]).get("d") == "2", stops[1]
    assert re.search(r"length = 3", stops[1]), stops[1]


@pytest.mark.parametrize("how", PATHS)
def test_debug_lines_writes_no_variables(how):
    tool = _needs_dwarfdump()
    entries = _entries(tool, _built(how, PROGRAM, "--debug=lines") / "prog.o")
    assert not [tag for _, tag, _ in entries
                if tag in ("DW_TAG_variable", "DW_TAG_formal_parameter")]
    assert any(tag == "DW_TAG_subprogram" for _, tag, _ in entries)


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


def _debugged(how: str, argv: list[str], program: str = PROGRAM) -> str:
    """A debugger's batch run of `program`, with what it printed. Bounded,
    since a breakpoint that never hits leaves the program to finish and the
    debugger to exit, and one that misbehaves must not hang the suite."""
    result = subprocess.run(argv, cwd=_built(how, program), capture_output=True, text=True,
                            timeout=120)
    return result.stdout + result.stderr


@pytest.mark.parametrize("how", PATHS)
def test_lldb_stops_at_a_line_and_names_the_frames(how):
    lldb = _lldb()
    out = _debugged(how, [lldb, "--batch", "-o", "breakpoint set --file prog.gob --line 2",
                          "-o", "run", "-o", "bt", "-o", "kill", "./prog"])
    # With `-g`, a frame shows its function's parameters, and `fact`'s `n` is
    # in its register at the line.
    assert re.search(r"frame #0: .*`Main#fact\(n=10\) at prog\.gob:2", out), out[-4000:]
    assert re.search(r"frame #1: .*`Main#main at prog\.gob:9", out), out[-4000:]


@pytest.mark.parametrize("how", PATHS)
def test_lldb_stops_in_an_inlined_function_and_shows_its_frame(how):
    lldb = _lldb()
    out = _debugged(how, [lldb, "--batch", "-o", "breakpoint set --name Main#area@Rectangle",
                          "-o", "run", "-o", "bt", "-o", "kill", "./prog"], RECTANGLES)
    assert re.search(r"frame #\d+: .*`Main#area@Rectangle at prog\.gob:11:\d+ \[inlined\]",
                     out), out[-4000:]
    assert re.search(r"frame #\d+: .*`Main#main at prog\.gob:18:\d+$", out,
                     re.MULTILINE), out[-4000:]


@pytest.mark.parametrize("how", PATHS)
def test_gdb_stops_in_an_inlined_function_and_shows_its_frame(how):
    gdb = _gdb()
    out = _debugged(how, [gdb, "-nx", "-batch", "-ex", "break 'Main#area@Rectangle'",
                          "-ex", "run", "-ex", "bt", "-ex", "kill", "./prog"], RECTANGLES)
    assert re.search(r"#\d+ .*Main#area@Rectangle.* at prog\.gob:11", out), out[-4000:]
    assert re.search(r"#\d+ .*Main#main.* at prog\.gob:18", out), out[-4000:]


@pytest.mark.parametrize("how", PATHS)
def test_atos_names_the_inlined_frames(how):
    """The system symbolizer, which `sample` and Instruments use, reads an
    inlined call's range list and names each call around an address: at the
    start of `step`'s code, `step`, then every call it is inside, then the
    function they were all inlined into. It stops reading an object's DWARF
    at an encoding it does not know, and then names no line at all."""
    if not _darwin():
        pytest.skip("atos is macOS's")
    tool = _needs_dwarfdump()
    directory = _built(how, LOOP)
    entries = _entries(tool, directory / "prog.o")
    assert any("DW_AT_ranges" in attributes for _, _, attributes in entries)
    # The entries around the first inlined `step`, innermost first.
    around: list[tuple[int, str, str]] = []
    step = None
    for entry in entries:
        depth, tag, attributes = entry
        around = [e for e in around if e[0] < depth]
        if tag == "DW_TAG_inlined_subroutine" and '"Main#step")' in attributes:
            step = attributes
            break
        if tag in ("DW_TAG_subprogram", "DW_TAG_inlined_subroutine"):
            around.append(entry)
    assert step is not None
    function = re.search(r'DW_AT_name\s+\("([^"]+)"\)', around[0][2]).group(1)
    callers = [re.search(r'DW_AT_abstract_origin\s+\(0x[0-9a-f]+ "([^"]+)"\)', a).group(1)
               for _, _, a in reversed(around[1:])]
    low = int(re.search(r"DW_AT_low_pc\s+\((0x[0-9a-f]+)\)", around[0][2]).group(1), 16)
    start = int(re.search(r"(?:DW_AT_low_pc\s+\(|\[)(0x[0-9a-f]+)", step).group(1), 16)
    symbols = subprocess.run(["nm", str(directory / "prog")], capture_output=True, text=True,
                             check=True).stdout
    linked = int(re.search(r"^([0-9a-f]+) T _" + re.escape(function) + "$", symbols,
                           re.MULTILINE).group(1), 16)
    result = subprocess.run(["atos", "-i", "-o", "prog", "-l", "0x100000000",
                             hex(linked + start - low)],
                            cwd=directory, capture_output=True, text=True)
    out = result.stdout + result.stderr
    # A call inlined into `step` may start where it starts, and be named first.
    frames = re.findall(r"^(\S+) \(in prog\) \((\S+):(\d+)\)$", out, re.MULTILINE)
    names = [name for name, _, _ in frames]
    assert "Main#step" in names, out
    k = names.index("Main#step")
    assert names[k:] == ["Main#step", *callers, function], out
    assert frames[k][1:] == ("prog.gob", "1"), out


@pytest.mark.parametrize("how", PATHS)
def test_gdb_stops_at_a_line_and_names_the_frames(how):
    gdb = _gdb()
    out = _debugged(how, [gdb, "-nx", "-batch", "-ex", "break prog.gob:2", "-ex", "run",
                          "-ex", "bt", "-ex", "kill", "./prog"])
    assert re.search(r"#0 .*Main#fact.* at prog\.gob:2", out), out[-4000:]
    assert re.search(r"#1 .*Main#main.* at prog\.gob:9", out), out[-4000:]


# -- the object against the assembler ------------------------------------------

DEBUG_SECTIONS = ["debug_abbrev", "debug_info", "debug_addr", "debug_rnglists",
                  "debug_loclists", "debug_str", "debug_line", "debug_line_str"]
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
        written, built = mine.sections.get(name), assembled.sections.get(name)
        assert written == built, (
            f"{name}: " + (_first_difference(written, built) if written and built
                           else f"written {written is not None}, assembled {built is not None}"))
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
