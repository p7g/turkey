"""Golden-file conformance runner.

For every `tests/programs/NAME.gob` there is a `NAME.expected` holding the
combined stdout+stderr (in that order) of compiling and running the program:
the compile's warnings and diagnostics, then what the program printed, then a
panic if it had one. Each program is compiled by `boot` from its own directory
and run there (see `tests.lang`), so error messages that quote the source file
do so by its bare name.

A program may also be a *directory*: `tests/programs/NAME/` whose entry module
is `Main.gob` and whose golden is `Main.expected` beside it (M11a). It is
compiled from inside that directory, so its imports resolve against it and its
diagnostics quote bare file names the same way.

These files are the language's recorded behavior, checked against the one
implementation that is kept. They used to be checked against the Python one,
and could be regenerated without much thought because a second compiler had to
agree with them (`test_boot`); nothing does now. Regenerate with
`tests/regenerate_expected.py`, and read every changed line of the diff.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests import lang

TESTS_DIR = Path(__file__).resolve().parent
PROGRAMS_DIR = TESTS_DIR / "programs"

PROGRAMS = sorted(PROGRAMS_DIR.glob("*.gob"))
# A multi-file program: a directory with a `Main.gob` in it.
BUNDLES = sorted(p / "Main.gob" for p in PROGRAMS_DIR.iterdir()
                 if p.is_dir() and (p / "Main.gob").is_file())

# Programs whose golden ends in a panic trace (`  at f (file:line:col)`), which
# `boot`'s backend does not produce yet: its binaries print the panic and no
# frames (TIX-114). They are checked with the trace left out, and the whole
# golden is kept as a strict xfail below, which starts passing -- and failing
# the run -- when the trace arrives.
NO_TRACE_YET = {"err_out_of_bounds", "err_string_boundary",
                "err_uninitialized_read"}

pytestmark = pytest.mark.skipif(shutil.which("cc") is None,
                                reason="no C compiler")


def _id(program: Path) -> str:
    """A bundle is named by its directory; a single file, by its stem."""
    return program.parent.name if program.name == "Main.gob" else program.stem


def conformance(program: Path) -> tuple[str, int]:
    """What `turkey run` printed for `program`, stdout then stderr, and its
    status. Shared with `tests/regenerate_expected.py`, so the runner and the
    goldens cannot drift apart."""
    try:
        result = lang.run(program)
    except lang.CompileError as error:
        return error.rendered, error.code
    return result.stdout + result.stderr, result.code


def _without_trace(text: str) -> str:
    return "".join(line for line in text.splitlines(keepends=True)
                   if not line.startswith("  at "))


def _diff_message(expected: str, actual: str, code: int) -> str:
    return (
        f"output mismatch (exit code {code})\n"
        f"--- expected ({len(expected)} bytes) ---\n{expected}\n"
        f"--- actual ({len(actual)} bytes) ---\n{actual}\n"
        f"--- end ---"
    )


@pytest.mark.parametrize("program", PROGRAMS + BUNDLES,
                         ids=[_id(p) for p in PROGRAMS + BUNDLES])
def test_program_conformance(program: Path) -> None:
    expected = program.with_suffix(".expected").read_text()
    actual, code = conformance(program)
    if _id(program) in NO_TRACE_YET:
        expected = _without_trace(expected)

    assert actual == expected, _diff_message(expected, actual, code)

    if _id(program).startswith("err_"):
        assert code != 0, f"{program.name} is an err_ program but exited 0"
    else:
        assert code == 0, f"{program.name} exited {code}, expected 0"


@pytest.mark.xfail(strict=True, reason="TIX-114: no panic trace from boot yet")
@pytest.mark.parametrize("name", sorted(NO_TRACE_YET))
def test_panic_trace(name: str) -> None:
    program = PROGRAMS_DIR / f"{name}.gob"
    actual, _ = conformance(program)
    assert actual == program.with_suffix(".expected").read_text()


def _dump(command: str, golden: Path) -> None:
    expected = golden.read_text()
    result = lang.dump(command, golden.with_suffix(".gob"))
    actual = result.stdout + result.stderr
    assert actual == expected, _diff_message(expected, actual, result.code)
    assert result.code == 0


SIGNATURES = sorted(PROGRAMS_DIR.glob("*.types"))


@pytest.mark.parametrize("golden", SIGNATURES, ids=[p.stem for p in SIGNATURES])
def test_types_command(golden: Path) -> None:
    """`NAME.types` pins what `boot types NAME.gob` prints.

    A program only needs one when its inferred signatures are the point --
    which now includes any program whose functions carry a predicate context,
    since that is where a change in the solver would show up first.
    """
    _dump("types", golden)


CORE = sorted(PROGRAMS_DIR.glob("*.core"))


@pytest.mark.parametrize("golden", CORE, ids=[p.stem for p in CORE])
def test_core_command(golden: Path) -> None:
    """`NAME.core` pins what `boot core NAME.gob` prints (M13b).

    A `.expected` cannot see any of this. Whether a method was reached by
    selecting a superclass or by taking a second dictionary, whether an
    instance was applied at the right types, whether a `var` became a cell --
    all of it produces the same output when it is right *and* when it is
    subtly wrong, which is why the elaboration went unchecked for as long as
    it did. These files are the elaboration written down.

    Only a few programs have one, chosen for what they show rather than for
    coverage: every program is checked by `Coretc` on every compile anyway.
    """
    _dump("core", golden)


OPT = sorted(PROGRAMS_DIR.glob("*.opt"))


@pytest.mark.parametrize("golden", OPT, ids=[p.stem for p in OPT])
def test_opt_command(golden: Path) -> None:
    """`NAME.opt` pins what `boot opt NAME.gob` prints (M15b).

    The third of the trio, and the one that shows an *analysis* rather than a
    translation: which local function became a label and which stayed a
    closure. The `.expected` beside it is the other half of the claim, since a
    pass that only writes a fact down must not change any answer.
    """
    _dump("opt", golden)


MONO = sorted(PROGRAMS_DIR.glob("*.mono"))


@pytest.mark.parametrize("golden", MONO, ids=[p.stem for p in MONO])
def test_mono_command(golden: Path) -> None:
    """`NAME.mono` pins what `boot mono NAME.gob` prints (M14a).

    Beside the `.core` golden rather than instead of it, because the pair is
    the point: the same program before and after specialization, so a reader
    can see which `[t]` became a copy, which dictionary stopped being rebuilt,
    and which type application survived because it is a method's own.
    """
    _dump("mono", golden)
