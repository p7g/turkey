"""Golden-file conformance runner.

For every `tests/programs/NAME.tl` there is a `NAME.expected` holding the
combined stdout+stderr (in that order) of running the program. Each program is
executed in a subprocess with its cwd set to `tests/programs`, so error messages
that quote the source file do so by its bare name.

A program may also be a *directory*: `tests/programs/NAME/` whose entry module
is `Main.tl` and whose golden is `Main.expected` beside it (M11a). It is run
from inside that directory, so its imports resolve against it and its
diagnostics quote bare file names the same way.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROGRAMS_DIR = TESTS_DIR / "programs"
REPO_ROOT = TESTS_DIR.parent

PROGRAMS = sorted(PROGRAMS_DIR.glob("*.tl"))
# A multi-file program: a directory with a `Main.tl` in it.
BUNDLES = sorted(p / "Main.tl" for p in PROGRAMS_DIR.iterdir()
                 if p.is_dir() and (p / "Main.tl").is_file())


def _id(program: Path) -> str:
    """A bundle is named by its directory; a single file, by its stem."""
    return program.parent.name if program.name == "Main.tl" else program.stem


def _run(args: list[str], cwd: Path = PROGRAMS_DIR) -> subprocess.CompletedProcess[str]:
    # Invoked exactly as tests/regenerate_expected.py invokes it, so the runner
    # and the goldens cannot drift: same argv, same cwd, PYTHONPATH overwritten
    # (not appended) to the repo root, stdout+stderr captured and concatenated
    # in that order.
    return subprocess.run(
        [sys.executable, "-m", "turkey", *args],
        cwd=cwd,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
        text=True,
    )


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
    result = _run(["run", program.name], cwd=program.parent)
    actual = result.stdout + result.stderr

    assert actual == expected, _diff_message(expected, actual, result.returncode)

    if _id(program).startswith("err_"):
        assert result.returncode != 0, (
            f"{program.name} is an err_ program but exited 0"
        )
    else:
        assert result.returncode == 0, (
            f"{program.name} exited {result.returncode}, expected 0"
        )


SIGNATURES = sorted(PROGRAMS_DIR.glob("*.types"))


@pytest.mark.parametrize("golden", SIGNATURES, ids=[p.stem for p in SIGNATURES])
def test_types_command(golden: Path) -> None:
    """`NAME.types` pins what `turkey types NAME.tl` prints.

    A program only needs one when its inferred signatures are the point --
    which now includes any program whose functions carry a predicate context,
    since that is where a change in the solver would show up first.
    """
    expected = golden.read_text()
    result = _run(["types", golden.with_suffix(".tl").name])
    actual = result.stdout + result.stderr
    assert actual == expected, _diff_message(expected, actual, result.returncode)
    assert result.returncode == 0
