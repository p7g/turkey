"""Golden-file conformance runner.

For every `tests/programs/NAME.tl` there is a `NAME.expected` holding the
combined stdout+stderr (in that order) of running the program. Each program is
executed in a subprocess with its cwd set to `tests/programs`, so error messages
that quote the source file do so by its bare name.
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


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    # Invoked exactly as tests/regenerate_expected.py invokes it, so the runner
    # and the goldens cannot drift: same argv, same cwd, PYTHONPATH overwritten
    # (not appended) to the repo root, stdout+stderr captured and concatenated
    # in that order.
    return subprocess.run(
        [sys.executable, "-m", "turkey", *args],
        cwd=PROGRAMS_DIR,
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


@pytest.mark.parametrize("program", PROGRAMS, ids=[p.stem for p in PROGRAMS])
def test_program_conformance(program: Path) -> None:
    expected = program.with_suffix(".expected").read_text()
    result = _run(["run", program.name])
    actual = result.stdout + result.stderr

    assert actual == expected, _diff_message(expected, actual, result.returncode)

    if program.stem.startswith("err_"):
        assert result.returncode != 0, (
            f"{program.name} is an err_ program but exited 0"
        )
    else:
        assert result.returncode == 0, (
            f"{program.name} exited {result.returncode}, expected 0"
        )


def test_types_command_matches_stack_types() -> None:
    expected = (PROGRAMS_DIR / "stack.types").read_text()
    result = _run(["types", "stack.tl"])
    actual = result.stdout + result.stderr
    assert actual == expected, _diff_message(expected, actual, result.returncode)
    assert result.returncode == 0
