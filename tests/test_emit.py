"""`boot build -f asm`: the machine IR as assembly the system assembler accepts.

The oracle here is `as`, and it is a much sharper one than `test_select`'s.
That test could only assemble the lines with no virtual register left in them --
there was no allocator, so most of the output was not assembly at all. This
assembles *whole functions*: the prologue, every instruction with the registers
the allocator chose, the branches, the parallel copies on the edges, the frame
offsets and the epilogue. An immediate out of range, a register spelled for the
wrong file, an `stp` whose offset does not reach -- `as` knows every one of
those, and a printer written against a manual does not.

What it does not check is behaviour: nothing here links or runs.
`tests/test_arm64_native.py` runs the corpus.
"""

from __future__ import annotations

import functools
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import bootc, lang, toolchain

REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = REPO_ROOT / "tests" / "programs"

CORPUS = sorted(
    path.name for path in PROGRAMS.glob("*.gob")
    if not path.name.startswith("err_")
)


def _split(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "// === ")
    assert set(modules) == {p.name for p in paths}, (
        sorted({p.name for p in paths} - set(modules)))
    return [modules[p.name] for p in paths]


@functools.lru_cache(maxsize=None)
def _all() -> dict[str, str]:
    paths = [PROGRAMS / name for name in CORPUS]
    texts = bootc.boot_each("asm", paths, _split)
    return {path.name: texts[path] for path in paths}


def _native(name: str) -> str:
    return _all()[name]


def _assembles(text: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "t.s"
        source.write_text(text, encoding="utf-8")
        return subprocess.run(
            [*toolchain.cc(), "-c", "-o", str(Path(directory) / "t.o"),
             str(source)],
            capture_output=True, text=True)


def _functions(text: str) -> dict[str, list[str]]:
    """Each function's instructions and labels, keyed by its Turkey name.

    The assembler directives around a function -- `.globl`, `.p2align`, the
    symbol itself -- are not part of what it does, so they are dropped here and
    the first line of a body is its first instruction. A local label is kept:
    it is `L1:` on Mach-O and `.L1:` on ELF, so a line starting with `.` is a
    directive only when it does not end in a colon.
    """
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(".globl "):
            current = stripped[len(".globl "):].strip('"').lstrip("_")
            out[current] = []
        elif stripped.startswith(".") and not stripped.endswith(":"):
            continue
        elif stripped.startswith('"') and stripped.endswith('":'):
            continue
        elif current is not None and stripped:
            out[current].append(stripped)
    return out


@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
@pytest.mark.parametrize("name", CORPUS)
def test_the_whole_program_assembles(name):
    result = _assembles(_native(name))
    assert result.returncode == 0, result.stderr[:4000]


# A virtual register is `%` and digits where an operand goes. Turkey *names*
# contain `%` too -- `%module.initialize`, `%default.Data.Eq#Eq…` -- and
# they reach the symbol table intact, so the test is about operands and not
# about the character.
VIRTUAL = re.compile(r"(?:^|[ ,\[])%\d")


@pytest.mark.parametrize("name", CORPUS)
def test_no_virtual_register_survives(name):
    """A `%n` operand is an unallocated value printed as if it were a register.

    The emitter substitutes the colouring; anything it could not is a bug in
    the allocator or in the substitution, and would otherwise reach `as` as a
    syntax error a long way from its cause.
    """
    left = [line for line in _native(name).splitlines() if VIRTUAL.search(line)]
    assert not left, left[:10]


@pytest.mark.parametrize("name", CORPUS)
def test_nothing_was_skipped(name):
    skipped = [line for line in _native(name).splitlines()
               if line.startswith("// skipped ")]
    assert not skipped, skipped[:10]


@pytest.mark.parametrize("name", CORPUS)
def test_every_return_comes_after_an_epilogue(name):
    """A `ret` that did not restore `x30` and `sp` returns into the caller's
    frame with the callee's stack still claimed."""
    for function, body in _functions(_native(name)).items():
        for n, line in enumerate(body):
            if line == "ret":
                window = body[max(0, n - 8):n]
                assert any(w.startswith("ldr x30,") for w in window), (
                    function, window)
                assert any(w.startswith("add sp, sp,") for w in window), (
                    function, window)


def test_a_function_opens_by_claiming_its_frame():
    body = _functions(_native("adt.gob"))["Main#main"]
    assert body[0].startswith("sub sp, sp, #") or body[0].startswith("movz"), body[:3]
    assert any(line.startswith("str x29,") for line in body), body[:6]
    assert any(line.startswith("add x29, sp,") for line in body), body[:6]


def test_stack_arguments_are_addressed_from_the_stack_pointer():
    """The caller writes its outgoing area and the callee reads the caller's.

    Both are `sp`-relative here: `sp` does not move inside a function, so the
    incoming arguments are the frame's own size above it.
    """
    functions = _functions(_native("stackargs.gob"))
    callee = functions["Main#ints"]
    assert any(line.startswith("ldr ") and ", [sp, #" in line for line in callee)
    assert any(line.startswith("str ") and ", [sp, #" in line for line in callee)


def test_pressure_saves_and_restores_callee_saved_registers():
    body = _functions(_native("pressure.gob"))["Main#wide"]
    saved = {line.split()[1].rstrip(",") for line in body
             if line.startswith("str x") and ", [sp, #" in line}
    assert any(r in saved for r in
               ("x19", "x20", "x21", "x22", "x23", "x24", "x25", "x26",
                "x27", "x28")), sorted(saved)


SLOT_LOAD = re.compile(r"ldr [xwd]\d+, (\[sp(?:, #\d+)?\])$")


def test_a_value_in_a_slot_is_loaded_once_between_calls():
    """A spilled value is reloaded after a call, and that one reload serves
    every read up to the next call.

    `Main#mixed` keeps more integers live across its loop's call than the
    callee-saved registers hold, and reads each several times in the
    straight-line code after the call. Within a run of instructions with no
    label and no call, no slot is loaded twice.
    """
    body = _functions(_native("reload_once.gob"))["Main#mixed"]
    # The prologue's saves come before the first label; a store after it is
    # spill code, and without any this test would check nothing.
    first_label = next(n for n, line in enumerate(body) if line.endswith(":"))
    assert any(line.startswith("str ") and ", [sp" in line
               for line in body[first_label:]), "nothing was spilled"
    seen: set[str] = set()
    for line in body:
        if line.endswith(":") or line.startswith(("bl ", "blr ")):
            seen = set()
            continue
        match = SLOT_LOAD.match(line)
        if match:
            assert match.group(1) not in seen, (
                f"{match.group(1)} loaded twice between calls")
            seen.add(match.group(1))


ZERO = re.compile(r"mov (x\d+), xzr$")
CALLEE_SAVED = {f"x{n}" for n in range(19, 28)}


def test_a_zero_read_after_a_call_is_computed_again():
    """A zero defined before a call and read after it is not kept across it.

    `Main#label` passes `String.concat` a null closure environment, which
    selection materializes before the calls in its argument list. Kept, it
    would hold a callee-saved register or a stack slot; computed again, it is
    a `mov` of `xzr` between the last of those calls and the one that reads
    it.
    """
    body = _functions(_native("rematerialize.gob"))["Main#label"]
    calls = [n for n, line in enumerate(body) if line.startswith("bl ")]
    concat = next(n for n in calls if "String#concat" in body[n])
    before = max(n for n in calls if n < concat)
    assert "mov x0, xzr" in body[before + 1:concat], body[before:concat]
    first_label = next(n for n, line in enumerate(body) if line.endswith(":"))
    loads = [line for line in body[first_label:concat] if SLOT_LOAD.match(line)]
    assert not loads, loads
    held = [line for line in body
            if (m := ZERO.match(line)) and m.group(1) in CALLEE_SAVED]
    assert not held, held


def test_a_constant_jump_argument_is_computed_into_its_parameter():
    """A zero passed to a block parameter is written into the parameter's
    register on the edge, not into another register and then copied.

    `Main#pick` joins a call's result with a zero, and the join's parameter is
    live across a call, so it holds a callee-saved register that the zero's
    own definition would not have chosen.
    """
    body = _functions(_native("rematerialize.gob"))["Main#pick"]
    assert any((m := ZERO.match(line)) and m.group(1) in CALLEE_SAVED
               for line in body), body
    for line, after in zip(body, body[1:]):
        m = ZERO.match(line)
        assert not (m and after.startswith("mov ")
                    and after.endswith(", " + m.group(1))), (line, after)


ROOT_STORE = re.compile(r"str (%\d+), \[root (\d+)\]$")
PARAMETER = re.compile(r"mov (%\d+), x\d+$")


def test_a_root_is_stored_once_at_its_definition():
    """A root slot is written once, where its value is defined, and never again
    before a safepoint: the slot belongs to that value alone and the collector
    does not move what it points to.

    `Main#scan` keeps an array, a string, a vector and a closure live across the
    calls in its loop. All four are parameters, so all four stores are in the
    first block of the function's body, ahead of the loop. And in every
    function of the program, the Prelude's included, no root slot is stored
    twice.
    """
    result = lang.dump("select", PROGRAMS / "root_once.gob")
    assert result.code == 0, result.stderr
    assert "!!" not in result.stdout, result.stdout
    # Function -> its root stores as (block, value, slot), and its parameters:
    # the values the entry block moves out of the argument registers.
    stores: dict[str, list[tuple[str, str, str]]] = {}
    parameters: dict[str, set[str]] = {}
    name = block = ""
    entry = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if line.startswith("fun @"):
            name = line[len("fun @"):].split("(")[0]
            stores[name], parameters[name] = [], set()
        elif line.startswith("  b") and stripped.endswith(":"):
            block = stripped.split()[0].split("(")[0].rstrip(":")
            entry = stripped.endswith("entry:")
        elif (match := ROOT_STORE.match(stripped)):
            stores[name].append((block, match.group(1), match.group(2)))
        elif entry and (match := PARAMETER.match(stripped)):
            parameters[name].add(match.group(1))
    scan = stores["Main#scan"]
    assert len(parameters["Main#scan"]) == 4, parameters["Main#scan"]
    assert parameters["Main#scan"] <= {value for _, value, _ in scan}, scan
    for block, value, _ in scan:
        if value in parameters["Main#scan"]:
            assert block == "b0", f"{value} is stored in {block}, not before the loop"
    for name, made in stores.items():
        slots = [slot for _, _, slot in made]
        assert len(slots) == len(set(slots)), f"{name} stores a root slot twice: {made}"


def test_a_call_is_followed_by_the_panic_check():
    """`turkey_panic` sets a flag and returns, so every call tests it.

    The flag is the first word of the running worker's record, which `x28`
    holds while Turkey code runs, so the check is a load through it. The
    program defines the record itself, so where code coming in from C sets
    `x28` to it, its address is a page-relative `add` rather than a load from
    the global offset table.
    """
    # Quoted, like every symbol this backend writes: `#` is the assembler's
    # immediate prefix, so one quoting rule covers Turkey names and C ones.
    text = _native("adt.gob")
    flag = f'"{toolchain.c_symbol("turkey_has_panicked")}"'
    # Mach-O and ELF spell a page relocation differently.
    if toolchain.target() == "arm64-linux":
        page, offset = flag, f":lo12:{flag}"
    else:
        page, offset = f"{flag}@PAGE", f"{flag}@PAGEOFF"
    assert f".globl {flag}" in text
    assert page in text
    assert offset in text
    assert "GOT" not in text and ":got" not in text
    lines = [line.strip() for line in text.splitlines()]
    for n, line in enumerate(lines):
        if line.startswith("bl ") and "turkey_panic" not in line:
            window = lines[n:n + 12]
            assert any(re.match(r"ldr w\d+, \[x28(, #0)?\]$", w) for w in window), window
            break
    else:
        pytest.fail("no ordinary call in the program")


@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
def test_the_compilers_own_source_assembles():
    """The scale test: 3,073 functions, 16 KB frames, 45,097 safepoints.

    Every immediate range this backend can get wrong is a function of frame
    size, and `boot`'s frames are two orders of magnitude bigger than the
    corpus's.
    """
    main = REPO_ROOT / "src" / "Main.gob"
    text = bootc.boot_each("asm", [main], _split)[main]
    assert "// skipped " not in text
    result = _assembles(text)
    assert result.returncode == 0, result.stderr[:4000]


NEGATED_BRANCHES = """
fun total(xs : Array Int, n : Int) -> Int {
    var sum = 0
    for var i = 0; i < n; i = i + 1 { sum = sum + xs[i] }
    sum
}

fun report(xs : Array Int, n : Int) -> Unit {
    if n == 77777 { return report(xs, n - 1) }
    if !(total(xs, n) < 3) { print("big") } else { print("small") }
}

fun main() -> Unit {
    let xs = [1, 2, 3, 4]
    print(total(xs, 4))
    report(xs, 2)
}
"""


def test_a_branch_on_a_negated_comparison_reads_the_flags():
    """`!(a < b)` is one `b.ge`.

    The `not` is the opposite `cset`, which the emitter folds into the branch,
    so no comparison is materialized and negated on the way to it. `total`'s
    bounds check, `!(i < length)`, materializes nothing either.

    A call to itself keeps `report` from being inlined into `main`.
    """
    result = lang.dump("asm", NEGATED_BRANCHES)
    assert result.code == 0, result.stderr
    functions = _functions(result.stdout)
    for name in ("Main#total", "Main#report"):
        body = functions[name]
        assert not [line for line in body if line.startswith(("cset", "eor"))], body
    report = functions["Main#report"]
    assert any(line.startswith("b.ge ") for line in report), report
    assert lang.output(NEGATED_BRANCHES) == "10\nbig\n"


BOUNDS_CHECK = """
fun at(xs : Array Int, i : Int) -> Int {
    if i == 77777 { return at(xs, i - 1) }
    xs[i]
}

fun main() { print(at([1, 2, 3], 2)) }
"""


def test_a_bounds_check_is_a_compare_and_branch_per_half():
    """`i < 0 || !(i < length)`, where nothing proves `i >= 0`: each half is
    a `cmp` and a conditional branch to the panic, no `i1` is made for the
    join, and the load carries no second check."""
    result = lang.dump("asm", BOUNDS_CHECK)
    assert result.code == 0, result.stderr
    body = _functions(result.stdout)["Main#at"]
    assert not [line for line in body if line.startswith("cset")], body
    assert len([line for line in body if line.startswith("cmp ")]) == 3, body
    below = [line.split()[1] for line in body if line.startswith("b.lt ")]
    above = [line.split()[1] for line in body if line.startswith("b.ge ")]
    assert len(below) == 1 and below == above, body
    assert not [line for line in body if line.startswith("b.hs ")], body
    assert lang.output(BOUNDS_CHECK) == "3\n"


ELEMENT_ACCESSES = """
fun moveInt(xs : Array Int, from : Int, to : Int) -> Unit { xs[to] = xs[from] }
fun moveFloat(xs : Array Float, from : Int, to : Int) -> Unit { xs[to] = xs[from] }
fun moveChar(xs : Array Char, from : Int, to : Int) -> Unit { xs[to] = xs[from] }
fun moveByte(xs : Array Byte, from : Int, to : Int) -> Unit { xs[to] = xs[from] }

fun main() -> Unit {
    let ints = [1, 2, 3]
    moveInt(ints, 2, 0)
    let floats = [1.5, 2.5]
    moveFloat(floats, 1, 0)
    let chars = ['a', 'b']
    moveChar(chars, 1, 0)
    let bytes = [Byte.truncate(7), Byte.truncate(9)]
    moveByte(bytes, 1, 0)
    print(show(ints[0]) + " " + show(floats[0]) + " " + show(chars[0]) + " "
          + show(Byte.toInt(bytes[0])))
}
"""


@pytest.mark.parametrize("function, load, store", [
    ("Main#moveInt", r"ldr x\d+", r"str x\d+"),
    ("Main#moveFloat", r"ldr d\d+", r"str d\d+"),
    ("Main#moveChar", r"ldr w\d+", r"str w\d+"),
    ("Main#moveByte", r"ldrb w\d+", r"strb w\d+"),
])
def test_an_element_access_scales_its_index_itself(function, load, store):
    """`[base, index, lsl #k]`, the shift the access's own size: the index is
    not shifted into a register first, and a byte's is not shifted at all."""
    result = lang.dump("asm", ELEMENT_ACCESSES)
    assert result.code == 0, result.stderr
    body = _functions(result.stdout)[function]
    assert not [line for line in body if line.startswith(("lsl ", "movz "))], body
    shift = {"Main#moveInt": ", lsl #3", "Main#moveFloat": ", lsl #3",
             "Main#moveChar": ", lsl #2", "Main#moveByte": ""}[function]
    element = r", \[x\d+, x\d+" + re.escape(shift) + r"\]$"
    assert any(re.match(load + element, line) for line in body), body
    assert any(re.match(store + element, line) for line in body), body


def test_element_accesses_read_and_write_the_element():
    assert lang.output(ELEMENT_ACCESSES) == "3 2.5 b 9\n"


MOVE = re.compile(r"f?mov [xd]\d+, [xd]\d+$")
GP_FROM_FP = re.compile(r"fmov x\d+, d\d+$")


def _moves_before(body: list[str], n: int) -> int:
    count = 0
    while n > 0 and MOVE.match(body[n - 1]):
        count += 1
        n -= 1
    return count


# An unconditional branch to a local label, on Mach-O or on ELF.
LOCAL_BRANCH = ("b L", "b .L")


def _loop_edge_moves(body: list[str]) -> dict[str, int]:
    """The moves on each edge between blocks of a loop, keyed by where they are.

    A loop runs from a header -- the target of a `b` to a label laid out
    earlier -- to that `b`. The copies into a block's parameters are the moves
    just before a jump to it, or before its label when the block before it
    falls through. Counting every edge inside the loop takes in the latches
    that jump to the block holding the poll, which passes their values on to
    the header: a copy into that block's parameters runs every iteration just
    as a copy into the header's does.
    """
    labels = {line[:-1]: n for n, line in enumerate(body) if line.endswith(":")}
    out: dict[str, int] = {}
    for end, line in enumerate(body):
        if not line.startswith(LOCAL_BRANCH):
            continue
        start = labels.get(line.split()[1], end)
        if start >= end:
            continue
        for n in range(start, end + 1):
            if body[n].startswith(LOCAL_BRANCH) or (n > start and body[n].endswith(":")):
                out[f"{n}: {body[n]}"] = _moves_before(body, n)
    return out


def test_a_loop_carried_value_keeps_its_register_round_the_loop():
    """A loop that updates its variables needs no copies at its back edge:
    each new value is computed into the register the header keeps it in.

    `Main#escape` carries four doubles round the Mandelbrot iteration, with a
    poll on the back edge, and no double goes through a general register.
    `Main#interpret` carries six integers, some across a call, and its arms
    each jump to the block holding the poll, which passes them on to the
    header; neither the arms nor that block copy them. `Main#rotate` passes
    three doubles round in a cycle, which needs a spare register to break, and
    that register is a vector one.
    """
    functions = _functions(_native("loop_carried.gob"))
    escape = functions["Main#escape"]
    for name in ("Main#escape", "Main#rotate"):
        body = functions[name]
        assert not [line for line in body if GP_FROM_FP.match(line)], (name, body)
    for body in (escape, functions["Main#interpret"]):
        edges = _loop_edge_moves(body)
        assert edges, "no loop found"
        assert all(count <= 1 for count in edges.values()), edges
