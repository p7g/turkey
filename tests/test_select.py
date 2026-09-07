"""`boot/Turkey/Select.tl`: the low IR to arm64 (M28 phase 4).

Until this file existed, selection was checked by a person running `boot asm`
and reading the histogram. That is not a test, and it showed: a call with nine
arguments panicked *inside the compiler* -- `argRegs` indexed at 8 -- and
nothing would have caught it, because no corpus program had a function with
more than eight parameters until `manyargs.tl` was added below.

Three things are asserted, and they are deliberately not "the output is this
text". There is no oracle for arm64 the way `test_boot` has one for everything
above Core, so a golden here would only assert that selection still does what
it did:

* **The machine graph verifies.** `Turkey.Ssa.verify` runs over the selected
  functions, and it is the real check -- selection creates blocks now, so a
  terminator naming the wrong one, a value defined twice, or a jump whose
  arguments do not match its target's parameters are all mistakes it can make.
  Every one of those has actually happened, and each was found this way.
* **Everything selects, or says why.** The histogram is the coverage ratchet:
  a program's functions are either selected or counted under a named reason.
* **The reasons are the ones we know about.** A *new* reason appearing is the
  signal worth failing on, which is what keeps a silent regression from hiding
  behind "well, something always stops".

`assembles` is the fourth check and the sharpest available: `as` is an
instruction-by-instruction oracle for the printer, and later for the encoder.
It is skipped where there is no assembler.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import bootc

REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = REPO_ROOT / "tests" / "programs"

# The corpus, minus the programs whose point is to fail to typecheck.
CORPUS = sorted(
    path.name for path in PROGRAMS.glob("*.tl")
    if not path.name.startswith("err_")
)

# What selection is known not to reach yet. A reason outside this set fails the
# run: the histogram is only a progress signal if a new entry is an event.
#
# Stack arguments are the whole of it. AAPCS64 passes the ninth argument and
# beyond on the stack, which needs a frame this backend does not lay out --
# the prologue that would spill them is the same pass that has still to assign
# a register to anything.
KNOWN_REASONS = {
    "a call with more than eight arguments in one register file, which would "
    "need stack arguments",
}


@functools.lru_cache(maxsize=None)
def _all() -> dict[str, str]:
    """`boot asm` over the whole corpus, in one run of a compiled `boot`."""
    text = bootc.boot("asm", *(str(PROGRAMS / name) for name in CORPUS))
    modules = bootc.split_before(text, "; === ")
    assert set(modules) == set(CORPUS), sorted(set(CORPUS) - set(modules))
    return modules


def _asm(name: str) -> str:
    return _all()[name]


def _counts(text: str) -> tuple[int, int]:
    lines = [line for line in text.splitlines()
             if line.startswith("-- selected ")]
    assert len(lines) == 1, lines
    parts = lines[0].split()
    return int(parts[2]), int(parts[4])


def _reasons(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("--   "):
            count, _, why = line[5:].strip().partition("  ")
            out[why.strip()] = int(count)
    return out


@pytest.mark.parametrize("name", CORPUS)
def test_the_machine_graph_verifies(name):
    """The CFG is still a CFG after selection.

    Selection splits blocks -- an overflow check is a branch and a panic block
    -- and it is the only pass that does. The bugs this has caught are all of
    one shape: a value defined twice, by `movz`/`movk`, by `adrp`/`add`, and by
    a call's pseudo-instruction naming the destination the result move also
    names.
    """
    complaints = [line for line in _asm(name).splitlines() if "!!" in line]
    assert not complaints, complaints


@pytest.mark.parametrize("name", CORPUS)
def test_every_function_selects_or_is_counted(name):
    selected, total = _counts(_asm(name))
    stopped = sum(_reasons(_asm(name)).values())
    assert selected + stopped == total, (
        f"{total - selected} functions did not select but "
        f"{stopped} were reported")


@pytest.mark.parametrize("name", CORPUS)
def test_no_unknown_reason_appears(name):
    found = set(_reasons(_asm(name)))
    assert found <= KNOWN_REASONS, sorted(found - KNOWN_REASONS)


def test_the_corpus_selects_apart_from_the_known_gap():
    """The ratchet. Everything but stack arguments reaches arm64."""
    selected = stopped = 0
    for name in CORPUS:
        got, _ = _counts(_asm(name))
        selected += got
        stopped += sum(_reasons(_asm(name)).values())
    assert selected > 1800, selected
    # `manyargs.tl` is the only program with a function over the eight-argument
    # limit, and it stops two: the recursive call and the one in `main`.
    assert stopped == 2, stopped


def test_a_nine_argument_call_is_reported_and_does_not_crash():
    """The regression test for the panic that started this file.

    `argRegs` was indexed with the index that overflowed it and only then was
    the bounds check asked, so `boot` died with "array index out of bounds:
    read at index 8, length 8" instead of reporting the call.

    It takes a *recursive* callee to reach: with constant arguments `opt`
    inlines the call and folds it away, and the first attempt at this program
    selected every function and proved nothing.
    """
    reasons = _reasons(_asm("manyargs.tl"))
    assert reasons, "the nine-argument call was not reported at all"
    assert set(reasons) <= KNOWN_REASONS, sorted(reasons)


def test_turkey_symbols_are_quoted():
    """`#` is ARM assembly's immediate prefix.

    `bl _Main#main` is "unexpected token in argument list" and not a call, so
    every Turkey-derived symbol is quoted. The runtime's own C identifiers are
    quoted too -- one rule beats two and a test for which applies.
    """
    text = _asm("adt.tl")
    calls = [line.strip() for line in text.splitlines()
             if line.strip().startswith("bl ")]
    assert calls
    unquoted = [line for line in calls if not line.startswith('bl "')]
    assert not unquoted, unquoted


def test_a_double_lives_in_the_vector_file():
    """Float arithmetic selects to `f`-prefixed mnemonics, not integer ones."""
    text = _asm("operators.tl")
    assert "fadd " in text
    assert "fcmp " in text


@pytest.mark.skipif(shutil.which("as") is None, reason="no assembler")
@pytest.mark.parametrize("name", ["adt.tl", "operators.tl"])
def test_the_printed_instructions_assemble(name):
    """`as` as an instruction-by-instruction oracle.

    This is the argument for keeping a printer at all rather than going
    straight to bytes: the assembler already knows every encoding and every
    immediate range, so the text stage is checkable in a way an encoder written
    first would not have been.

    Only the instruction lines, and only the ones with no virtual register
    left in them -- there is no allocator yet, so `mov %3, x0` is not
    something `as` can be asked about. What that leaves is the physical-register
    traffic around calls, which is where the calling convention lives.
    """
    lines = []
    for line in _asm(name).splitlines():
        text = line.strip()
        if not text or text.startswith((";", "fun ", "}", "--", "global ")):
            continue
        if text.endswith(":") or text.startswith(("ret ", "branch ", "jump ")):
            continue
        if "%" in text:
            continue
        lines.append("\t" + text)
    assert lines, "nothing physical-only to assemble"
    source = "\t.globl _t\n_t:\n" + "\n".join(lines) + "\n"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "t.s"
        path.write_text(source)
        result = subprocess.run(
            ["as", "-arch", "arm64", "-o", str(Path(directory) / "t.o"),
             str(path)],
            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
