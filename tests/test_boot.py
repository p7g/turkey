"""The bootstrap compiler, diffed against the implementation it is porting.

`boot/` is a Turkey compiler written in Turkey (plan.txt item 9). Each of its
milestones is checked the same way: run it over every Turkey file in the repo
and compare its output, byte for byte, against the Python implementation's.

The corpus is the whole repository -- the conformance programs, the standard
library, and `boot/` itself. Its own source is the point rather than a
flourish: a compiler that cannot read itself cannot be bootstrapped, and the
library is by some way the largest Turkey program there is.

Cost is why `boot` is handed the entire corpus in one invocation. Starting it
means compiling it, which is about two seconds; per-file that would be minutes,
and once per suite it is once. The Python side needs no subprocess at all,
being an ordinary function call.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from turkey.lexer import tokenize

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_MAIN = REPO_ROOT / "boot" / "Main.tl"


def _corpus() -> list[Path]:
    """Every Turkey file in the repository, in a stable order."""
    found = sorted(
        set(REPO_ROOT.glob("tests/programs/**/*.tl"))
        | set(REPO_ROOT.glob("turkey/lib/**/*.tl"))
        | set(REPO_ROOT.glob("boot/**/*.tl"))
    )
    assert found, "no Turkey source found to compare against"
    return found


CORPUS = _corpus()


def _boot(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "turkey", "run", str(BOOT_MAIN), "--", *args],
        cwd=REPO_ROOT,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"boot exited {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def _python_tokens(paths: list[Path]) -> str:
    out: list[str] = []
    for path in paths:
        for token in tokenize(path.read_text(encoding="utf-8"), str(path)):
            out.append(token.canonical())
            out.append("\n")
    return "".join(out)


@pytest.fixture(scope="module")
def boot_tokens() -> str:
    return _boot("tokens", *(str(p) for p in CORPUS))


def test_the_corpus_is_the_whole_repository() -> None:
    # A shrinking corpus would silently weaken every test below it.
    names = {p.name for p in CORPUS}
    assert "Main.tl" in names, "boot's own source must be in the corpus"
    assert "String.tl" in names, "the standard library must be in the corpus"
    assert len(CORPUS) > 60


def test_boot_lexes_the_corpus_exactly_as_python_does(boot_tokens: str) -> None:
    """M19: the two lexers agree on every token of every file.

    Both sides print the canonical dump of `turkey/lexer.py`, which is
    deliberately not Python's `repr`: the float spelling is PRIMITIVES.md 3.3's
    and the escapes are design.md 2.1's, so neither implementation can be right
    only by being the host.
    """
    expected = _python_tokens(CORPUS)
    if boot_tokens != expected:
        # Report the first line that differs; the whole dump is far too large.
        actual_lines = boot_tokens.split("\n")
        expected_lines = expected.split("\n")
        for i, (got, want) in enumerate(zip(actual_lines, expected_lines)):
            if got != want:
                context = "\n".join(expected_lines[max(0, i - 3):i])
                pytest.fail(
                    f"first difference at line {i + 1}:\n{context}\n"
                    f"  python: {want!r}\n  boot:   {got!r}")
        pytest.fail(
            f"dumps differ in length: python {len(expected_lines)} lines, "
            f"boot {len(actual_lines)}")


def test_boot_reports_a_missing_file(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "turkey", "run", str(BOOT_MAIN),
         "--", "tokens", str(tmp_path / "absent.tl")],
        cwd=REPO_ROOT,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "cannot read" in result.stderr
