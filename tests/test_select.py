"""`boot/Turkey/Select.gob`: the low IR to arm64 (M28 phase 4).

Until this file existed, selection was checked by a person running `boot asm`
and reading the histogram. That is not a test, and it showed: a call with nine
arguments panicked *inside the compiler* -- `argRegs` indexed at 8 -- and
nothing would have caught it, because no corpus program had a function with
more than eight parameters until `manyargs.gob` was added below.

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
    path.name for path in PROGRAMS.glob("*.gob")
    if not path.name.startswith("err_")
)

# What selection can still refuse. A reason outside this set fails the run: the
# histogram is only a progress signal if a new entry is an event.
#
# Stack arguments were the last gap and are closed; what remains is a guard that
# should never fire -- the convention for them is this compiler's own, and no C
# runtime entry point takes more than four arguments.
KNOWN_REASONS = {
    "a runtime call with stack arguments",
}

# What stops the allocator. With spilling there should be nothing: a value
# with no register goes to memory instead. These are the reasons it can still
# give up, and the count below is exact so that one appearing is an event.
KNOWN_COLOUR_REASONS = {
    "a value with no free register in the general file",
    "a value with no free register in the vector file",
    "a reload or store with no free register in the general file",
    "a reload or store with no free register in the vector file",
    "spilling did not finish in 16 rounds",
}
COLOUR_STOPS = 0


def _split_asm(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "; === ")
    assert set(modules) == {p.name for p in paths}, (
        sorted({p.name for p in paths} - set(modules)))
    return [modules[p.name] for p in paths]


@functools.lru_cache(maxsize=None)
def _all() -> dict[str, str]:
    """`boot asm` over the whole corpus, cached on disk per program.

    One `boot` run fills the cache and every worker of `pytest -n auto` reads
    it; kept only in this process, it was one corpus run per worker.
    """
    paths = [PROGRAMS / name for name in CORPUS]
    texts = bootc.boot_each("asm", paths, _split_asm)
    return {path.name: texts[path] for path in paths}


def _asm(name: str) -> str:
    return _all()[name]


def _counts(text: str) -> tuple[int, int]:
    lines = [line for line in text.splitlines()
             if line.startswith("-- selected ")]
    assert len(lines) == 1, lines
    parts = lines[0].split()
    return int(parts[2]), int(parts[4])


def _coloured(text: str) -> tuple[int, int]:
    lines = [line for line in text.splitlines()
             if line.startswith("-- coloured ")]
    assert len(lines) == 1, lines
    parts = lines[0].split()
    return int(parts[2]), int(parts[4])


def _colour_reasons(text: str) -> dict[str, int]:
    """Colouring's histogram: `-- colour  <count>  <reason>`.

    A different prefix from selection's on purpose (`boot/Main.gob` says why),
    so the two cannot be summed into each other by a parser that matched both.
    """
    out: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("-- colour  "):
            count, _, why = line[len("-- colour  "):].partition("  ")
            out[why.strip()] = int(count)
    return out


def _spilled(text: str) -> dict[str, int]:
    """`-- spilled V values  R into root slots  S spill slots  ...  rounds N`."""
    lines = [line for line in text.splitlines()
             if line.startswith("-- spilled ")]
    assert len(lines) == 1, lines
    words = lines[0].split()
    return {"values": int(words[2]), "root": int(words[4]),
            "slots": int(words[8]), "stores": int(words[11]),
            "reloads": int(words[13]), "rounds": int(words[16])}


def _reasons(text: str) -> dict[str, int]:
    """Selection's histogram: `--   <count>  <reason>`.

    `--   over budget  <function>` shares the prefix and is not a reason; it
    first appeared in the corpus with `pressure.gob`, whose point is to be
    over budget, and crashed this parser.
    """
    out: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("--   ") and not line.startswith("--   over budget"):
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


def test_the_corpus_selects_completely():
    """The ratchet. Everything reaches arm64, stack arguments included."""
    selected = stopped = 0
    for name in CORPUS:
        got, _ = _counts(_asm(name))
        selected += got
        stopped += sum(_reasons(_asm(name)).values())
    assert selected > 1800, selected
    # `manyargs.gob` and `stackargs.gob` stopped here until stack arguments
    # existed; nothing should stop now.
    assert stopped == 0, stopped


def test_a_nine_argument_call_passes_the_ninth_on_the_stack():
    """Both halves of the convention, in the program that first needed it.

    `argRegs` was once indexed with the index that overflowed it, and a
    nine-argument call panicked inside the compiler; then the call was reported
    and stopped; now it selects. The caller stores the overflow into its
    outgoing area and the callee loads it from its incoming one. The callee has
    to recurse, or `opt` inlines the call and folds it away.
    """
    text = _asm("manyargs.gob")
    assert not _reasons(text), _reasons(text)
    _, callee = _function(text, "Main#nine")
    assert any("[incoming 0]" in line for line in callee), callee
    assert any(line.startswith("str ") and "[outgoing 0]" in line
               for line in callee), callee


def test_parameters_are_moved_out_of_the_argument_registers():
    """The callee's side of the calling convention is instructions now.

    The allocator has to see that `x0`-`x7` hold arguments until each is
    copied, so the entry block moves them, in order, and the machine
    function has no `params` of its own.
    """
    text = _asm("stackargs.gob")
    head, body = _function(text, "Main#mixed")
    assert head.startswith("fun @Main#mixed() ->"), head
    # The environment, then `n`, then the first double -- in their own files.
    assert "mov %0, x0" in body and "mov %1, x1" in body, body
    assert "fmov %2, d0" in body and "mov %3, x2" in body, body
    assert any("[incoming 0]" in line for line in body), body[:20]


def test_a_safepoint_stores_its_roots_and_is_mapped():
    """Frame tables, not a shadow stack.

    Before a call that may collect, every root live across it is stored into
    its slot, and the call is followed by the map the frame table is built
    from. Nothing enters or leaves a root frame.
    """
    text = _asm("pressure.gob")
    _, body = _function(text, "Main#strings")
    maps = [n for n, line in enumerate(body) if line.startswith("; safepoint ")]
    assert maps, "no safepoint was mapped"
    for n in maps:
        assert body[n - 1].startswith(("bl ", "blr ")), body[n - 3:n + 1]
    assert any("[root " in line and line.startswith("str ") for line in body)
    assert "turkey_root_enter" not in text and "turkey_root_leave" not in text


@pytest.mark.parametrize("name", CORPUS)
def test_every_selected_function_colours_or_is_counted(name):
    """The colourer's own accounting, which nothing checked until it had a gap.

    Colouring runs over what selection produced, so its denominator is
    selection's numerator -- and a function that neither colours nor appears in
    the histogram is one the emitter would later be handed with no registers.
    """
    text = _asm(name)
    coloured, total = _coloured(text)
    selected, _ = _counts(text)
    assert total == selected, (total, selected)
    stopped = sum(_colour_reasons(text).values())
    assert coloured + stopped == total, (coloured, stopped, total)


@pytest.mark.parametrize("name", CORPUS)
def test_no_unknown_colour_reason_appears(name):
    found = set(_colour_reasons(_asm(name)))
    assert found <= KNOWN_COLOUR_REASONS, sorted(found - KNOWN_COLOUR_REASONS)


def test_the_corpus_colours_completely():
    """The allocation ratchet, which spilling took from 10 to 0.

    Every stop was a function whose values outnumbered the registers free at
    some point -- all of them across a call, in the corpus. Exact rather than
    a bound, so that one coming back is noticed.
    """
    stopped = sum(sum(_colour_reasons(_asm(name)).values()) for name in CORPUS)
    assert stopped == COLOUR_STOPS, stopped


def test_pressure_spills_into_both_kinds_of_slot():
    """`pressure.gob` exists to take every spilling path.

    Thirty-two integers live at once, twelve doubles and twelve pointers live
    across calls. The pointers are rooted, so some spilled value must land in
    a root slot; the integers and doubles are not, so some must need a spill
    slot of their own. A program that spilled nothing, or only one kind, would
    be a program `opt` had simplified out from under the test.
    """
    text = _asm("pressure.gob")
    spilled = _spilled(text)
    assert spilled["values"] > 0, spilled
    assert spilled["root"] > 0, spilled
    assert spilled["slots"] > 0, spilled
    assert spilled["stores"] == spilled["values"], spilled
    assert spilled["reloads"] >= spilled["values"], spilled
    assert not _colour_reasons(text), _colour_reasons(text)


@functools.lru_cache(maxsize=None)
def _boot_asm() -> str:
    """`boot asm boot/Main.gob`: one process, about ninety seconds, so cached.

    The build fingerprint covers all of `boot/`, which is what the output
    depends on besides `Main.gob` itself.
    """
    main = REPO_ROOT / "boot" / "Main.gob"
    return bootc.boot_each("asm", [main], _split_asm)[main]


def test_boot_allocates_completely():
    """The compiler's own source, which is what the spiller is for.

    Before spilling, 249 of `boot`'s functions stopped: 88 values live at once
    in `%module.initialize`, 35 of them untraced, and 84 live across one call
    against ten callee-saved registers. The corpus alone never needed a
    spiller, so this is the test that says it works on the program M29 needs.
    """
    text = _boot_asm()
    assert not _colour_reasons(text), _colour_reasons(text)
    complaints = [line for line in text.splitlines() if "!!" in line]
    assert not complaints, complaints[:20]
    assert set(_reasons(text)) <= KNOWN_REASONS, sorted(_reasons(text))


def _function(text: str, name: str) -> tuple[str, list[str]]:
    """One function's signature line and body lines from a module dump."""
    lines = text.splitlines()
    for at, line in enumerate(lines):
        if line.startswith(f"fun @{name}("):
            body = []
            for inner in lines[at + 1:]:
                if inner.startswith("}"):
                    break
                body.append(inner.strip())
            return line, body
    raise AssertionError(f"{name} is not in the dump")


def test_the_float_primitives_select_without_a_call():
    """`Prim.floatBits`, `floatFromBits`, `floatIsNaN` and `floatFitsInt`.

    None has a runtime entry point, and no corpus program reached one until
    `float_bits.gob` -- so selection's ratchet was green while three functions
    in `boot`'s own source stopped at "the runtime function Prim.floatBits",
    and the same program then found `Prim.floatIsNaN` missing from *both*
    backends (FINDINGS 87).

    Checked in the library wrappers, whose entry moves say which file each
    operand is in: virtual registers print as `%n`, so `fmov %2, %1` alone
    cannot say which way the bits went, but `fmov %1, d0` before it can.
    """
    text = _asm("float_bits.gob")
    assert not _reasons(text), _reasons(text)

    _, body = _function(text, "Data.Float#bits")
    assert "fmov %1, d0" in body and "fmov %2, %1" in body, body

    _, body = _function(text, "Data.Float#fromBits")
    assert "mov %1, x1" in body and "fmov %2, %1" in body, body

    _, body = _function(text, "Data.Float#isNaN")
    assert "fcmp %1, %1" in body and any(
        line.startswith("cset ") and line.endswith(", vs") for line in body), body

    _, body = _function(text, "Data.Float#truncate")
    assert sum(line.startswith("fcmp ") for line in body) >= 2, body
    assert not any(line.startswith("bl ") and "float_fits" in line
                   for line in body), body

    # And inlined at a use, which is where `opt` would have folded a constant.
    _, body = _function(text, "Main#patternsSurvive")
    assert any(line.startswith("fmov ") for line in body), body


def test_turkey_symbols_are_quoted():
    """`#` is ARM assembly's immediate prefix.

    `bl _Main#main` is "unexpected token in argument list" and not a call, so
    every Turkey-derived symbol is quoted. The runtime's own C identifiers are
    quoted too -- one rule beats two and a test for which applies.
    """
    text = _asm("adt.gob")
    calls = [line.strip() for line in text.splitlines()
             if line.strip().startswith("bl ")]
    assert calls
    unquoted = [line for line in calls if not line.startswith('bl "')]
    assert not unquoted, unquoted


def test_a_double_lives_in_the_vector_file():
    """Float arithmetic selects to `f`-prefixed mnemonics, not integer ones."""
    text = _asm("operators.gob")
    assert "fadd " in text
    assert "fcmp " in text


@pytest.mark.skipif(shutil.which("as") is None, reason="no assembler")
@pytest.mark.parametrize("name", ["adt.gob", "operators.gob",
                                  "float_bits.gob"])
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


def test_typed_record_stores_select_direct_memory_writes():
    text = _asm("record_stores.gob")
    assert "_turkey_object_set" not in text
    assert "str " in text
