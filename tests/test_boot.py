"""The bootstrap compiler, diffed against the implementation it is porting.

`boot/` is a Turkey compiler written in Turkey (plan.txt item 9), whose
modules are `Turkey.*`. Each of its
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

The M21 milestone is the slow one, and knowingly: it loads a whole module graph
per entry program, so the library is parsed once per program on both sides. The
Python side does that in under three seconds and `boot` takes minutes, because
`boot` is a Turkey program running on generated Python. That ratio is the thing
M26 removes rather than a reason to shrink the corpus.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from turkey.astdump import dump as dump_ast
from turkey.driver import desugared
from turkey.lexer import tokenize
from turkey.parser import parse

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_MAIN = REPO_ROOT / "boot" / "Main.tl"


def _corpus() -> list[Path]:
    """Every Turkey file in the repository, in a stable order."""
    found = sorted(
        set(REPO_ROOT.glob("tests/programs/**/*.tl"))
        | set(REPO_ROOT.glob("lib/**/*.tl"))
        | set(REPO_ROOT.glob("boot/**/*.tl"))
    )
    assert found, "no Turkey source found to compare against"
    return found


CORPUS = _corpus()


def _entries() -> list[Path]:
    """The corpus files that can be an *entry* module.

    A library module resolves its own imports against its own directory, so
    `lib/Data/Array.tl` alone is not a program and neither implementation can
    load it as one. Every library module is still covered by the milestone
    below, as an import of every program that is here.
    """
    found = sorted(
        p for p in REPO_ROOT.glob("tests/programs/**/*.tl")
        if "err_" not in str(p))
    found.append(BOOT_MAIN)
    return found


ENTRIES = _entries()


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


def _python_ast(paths: list[Path]) -> str:
    # `known` is empty, matching what `turkey ast` passes: design.md 7's
    # alias-vs-data question is decided by a pre-pass over the one file.
    return "".join(
        dump_ast(parse(p.read_text(encoding="utf-8"))) for p in paths)


def _first_difference(actual: str, expected: str, what: str) -> None:
    if actual == expected:
        return
    actual_lines = actual.split("\n")
    expected_lines = expected.split("\n")
    for i, (got, want) in enumerate(zip(actual_lines, expected_lines)):
        if got != want:
            context = "\n".join(expected_lines[max(0, i - 3):i])
            pytest.fail(
                f"{what}: first difference at line {i + 1}:\n{context}\n"
                f"  python: {want!r}\n  boot:   {got!r}")
    pytest.fail(
        f"{what}: dumps differ in length: python {len(expected_lines)} lines, "
        f"boot {len(actual_lines)}")


@pytest.fixture(scope="module")
def boot_tokens() -> str:
    return _boot("tokens", *(str(p) for p in CORPUS))


@pytest.fixture(scope="module")
def boot_ast() -> str:
    return _boot("ast", *(str(p) for p in CORPUS))


@pytest.fixture(scope="module")
def boot_desugar() -> str:
    return _boot("desugar", *(str(p.relative_to(REPO_ROOT)) for p in ENTRIES))


def _python_desugar(paths: list[Path]) -> str:
    out: list[str] = []
    for path in paths:
        relative = str(path.relative_to(REPO_ROOT))
        src = path.read_text(encoding="utf-8")
        for module in desugared(src, relative, [path.parent]):
            out.append(f"module {module.name}\n")
            out.append(dump_ast(module.program))
    return "".join(out)


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
    _first_difference(boot_tokens, _python_tokens(CORPUS), "tokens")


def test_boot_parses_the_corpus_exactly_as_python_does(boot_ast: str) -> None:
    """M20: the two parsers build the same tree, spans included.

    Spans are in the dump on purpose. A parser that builds the right shape from
    the wrong token reports every later error in the wrong place, and this is
    the only stage where that is cheap to catch.

    It also covers the two speculations Python does by catching a `ParseError`
    and `boot` does by scanning tokens -- whether a method is a signature, and
    whether a `for` is the `in` form. The standard library is full of class
    signatures and of both loop forms, so agreement over the corpus is what
    says the two readings coincide.
    """
    _first_difference(boot_ast, _python_ast(CORPUS), "ast")


def test_boot_resolves_and_desugars_the_corpus_exactly_as_python_does(
        boot_desugar: str) -> None:
    """M21: module loading, name resolution and the `?`/`do` lowering.

    Every module of every program, not just the entry one: what resolution does
    to a module depends on what it imported, so a dump of the entry alone would
    check the interesting half of the stage against nothing. Between them these
    programs pull in the whole of `lib/`, and `boot/Main.tl` pulls in the whole
    of `boot/`.

    The tree is the M20 dump, so what this adds over that milestone is exactly
    what the two passes changed: every name is internal, every bracket is an
    `Index` call, and every `?` is a `bind` -- including the lifted loops, whose
    generated `%loop`/`%fell`/`%k` binders are in the dump and therefore pin the
    two implementations to the same *numbering*, not merely the same shape.
    """
    _first_difference(boot_desugar, _python_desugar(ENTRIES), "desugar")


def test_the_desugar_corpus_exercises_the_hard_cases() -> None:
    """The milestone is only worth its runtime if the shapes are in it."""
    dumped = _python_desugar(ENTRIES)
    assert "%k1" in dumped, "no `?` was lowered to a bind"
    assert "%loop" in dumped, "no loop containing a `?` was lifted"
    assert "%fell" in dumped, "no flow-mode sequencing point"
    assert "%seq" in dumped, "no `for ... in` was expanded to its cursor form"
    assert "Prelude#Fall" in dumped, "no control transfer became a value"
    assert "\n  eindex" not in dumped, "a bracket survived desugaring"


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
