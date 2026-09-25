"""`boot native`: the machine IR as assembly the system assembler accepts.

The oracle here is `as`, and it is a much sharper one than `test_select`'s.
That test could only assemble the lines with no virtual register left in them --
there was no allocator, so most of the output was not assembly at all. This
assembles *whole functions*: the prologue, every instruction with the registers
the allocator chose, the branches, the parallel copies on the edges, the frame
offsets and the epilogue. An immediate out of range, a register spelled for the
wrong file, an `stp` whose offset does not reach -- `as` knows every one of
those, and a printer written against a manual does not.

What it does not check is behaviour: the module's data and the entry sequence
are the next slice, so nothing here links or runs yet. `tests/test_native.py`
remains the executing oracle until arm64 has one.
"""

from __future__ import annotations

import functools
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import bootc, toolchain

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
    texts = bootc.boot_each("native", paths, _split)
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
    the first line of a body is its first instruction.
    """
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(".globl "):
            current = stripped[len(".globl "):].strip('"').lstrip("_")
            out[current] = []
        elif stripped.startswith("."):
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
# contain `%` too -- `%module.initialize`, `%default.Std.Classes#Eq…` -- and
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


def test_a_call_is_followed_by_the_panic_check():
    """`turkey_panic` sets a flag and returns, so every call tests it.

    The flag is the runtime's, so its address comes through the global offset
    table rather than a page-relative `add` -- this module does not define it.
    """
    # Quoted, like every symbol this backend writes: `#` is the assembler's
    # immediate prefix, so one quoting rule covers Turkey names and C ones.
    text = _native("adt.gob")
    flag = f'"{toolchain.c_symbol("turkey_has_panicked")}"'
    # Mach-O and ELF spell a GOT relocation differently.
    if toolchain.target() == "arm64-linux":
        page, offset = f":got:{flag}", f":got_lo12:{flag}"
    else:
        page, offset = f"{flag}@GOTPAGE", f"{flag}@GOTPAGEOFF"
    assert page in text
    assert offset in text
    lines = [line.strip() for line in text.splitlines()]
    for n, line in enumerate(lines):
        if line.startswith("bl ") and "turkey_panic" not in line:
            window = lines[n:n + 12]
            assert any(w.startswith("adrp") and page in w for w in window), window
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
    text = bootc.boot_each("native", [main], _split)[main]
    assert "// skipped " not in text
    result = _assembles(text)
    assert result.returncode == 0, result.stderr[:4000]
