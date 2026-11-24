"""`turkey fmt`, the formatter in `src/Turkey/Format.gob`.

The style is the pairs in `tests/fmt/`: each `NAME.gob` and `NAME.expected.gob`,
which is exactly what `turkey fmt` must make of it. The corpus -- `lib/`,
`src/`, `examples/` and every example in `docs/ref/` -- checks the formatter's
safety net instead: each file's output must parse to the same tree, hold the
same comments and format to itself, or the formatter refuses it with an
internal error. The corpus is never a reference for the style.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

ROOT = bootc.REPO_ROOT
GOLDENS = ROOT / "tests" / "fmt"
PAIRS = sorted(p.name[: -len(".expected.gob")] for p in GOLDENS.glob("*.expected.gob"))


def fmt(*argv: str, cwd: Path = ROOT, **variables: str) -> subprocess.CompletedProcess:
    return subprocess.run(toolchain.command(bootc.binary(), "fmt", *argv), cwd=cwd,
                          env=dict(os.environ, **variables), capture_output=True, text=True,
                          timeout=600)


@pytest.mark.parametrize("name", PAIRS)
def test_golden(tmp_path: Path, name: str) -> None:
    target = tmp_path / f"{name}.gob"
    shutil.copy(GOLDENS / f"{name}.gob", target)
    result = fmt(str(target))
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == (GOLDENS / f"{name}.expected.gob").read_bytes()


def test_every_expected_output_is_already_formatted() -> None:
    expected = [str(GOLDENS / f"{name}.expected.gob") for name in PAIRS]
    result = fmt("--check", *expected)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_check_names_the_files_it_would_change_and_writes_nothing(tmp_path: Path) -> None:
    messy = tmp_path / "messy.gob"
    tidy = tmp_path / "tidy.gob"
    messy.write_text("fun main() { print(1) ; print(2) }\n")
    tidy.write_text("fun main() {\n\tprint(1)\n}\n")
    result = fmt("--check", "messy.gob", "tidy.gob", cwd=tmp_path)
    assert (result.returncode, result.stdout) == (1, "messy.gob\n"), result.stderr
    assert messy.read_bytes() == b"fun main() { print(1) ; print(2) }\n"


def test_a_file_that_does_not_parse_is_reported_and_the_rest_are_done(tmp_path: Path) -> None:
    (tmp_path / "a.gob").write_text("fun main() { print(1) ; print(2) }\n")
    (tmp_path / "broken.gob").write_text("fun main( {\n")
    (tmp_path / "b.gob").write_text("fun main() {print(3)}\n")
    result = fmt("a.gob", "broken.gob", "b.gob", cwd=tmp_path)
    assert result.returncode == 3
    assert result.stderr.startswith("broken.gob:1:11: parse error: ")
    assert (tmp_path / "a.gob").read_bytes() == b"fun main() {\n\tprint(1)\n\tprint(2)\n}\n"
    assert (tmp_path / "broken.gob").read_bytes() == b"fun main( {\n"
    assert (tmp_path / "b.gob").read_bytes() == b"fun main() { print(3) }\n"
    # Under `--check` the status says the file is broken, not that it would
    # change.
    result = fmt("--check", "broken.gob", cwd=tmp_path)
    assert result.returncode == 3


def test_a_refused_file_is_an_internal_error_and_the_rest_are_done(tmp_path: Path) -> None:
    (tmp_path / "a.gob").write_text("fun main() {print(1)}\n")
    (tmp_path / "b.gob").write_text("fun main() {print(2)}\n")
    result = fmt("a.gob", "b.gob", cwd=tmp_path, TURKEY_TEST_FMT="refuse")
    assert result.returncode == 2
    assert result.stderr == (
        "a.gob: internal error: the formatter refuses this file: refused for a test\n"
        "b.gob: internal error: the formatter refuses this file: refused for a test\n")
    assert (tmp_path / "a.gob").read_bytes() == b"fun main() {print(1)}\n"
    # Formatted text that does not parse is the formatter's fault too.
    result = fmt("--check", "a.gob", cwd=tmp_path, TURKEY_TEST_FMT="garble")
    assert result.returncode == 2
    assert result.stderr.startswith(
        "a.gob: internal error: the formatter refuses this file: the formatted text does not parse: ")


def test_a_line_ending_in_a_directive_keeps_its_code(tmp_path: Path) -> None:
    # The directive covers its line. The long call would otherwise be broken
    # across lines, and the two-line `let` joined onto one.
    source = (
        "fun main() {\n"
        "    report(diagnostics, \"unexpected token while parsing the arms of a match\", span, Severity.Error, hint) --@expect(unreachable-code)\n"
        "    let x = add(1,\n"
        "        2) --@expect(unreachable-code)\n"
        "}\n"
    )
    target = tmp_path / "directives.gob"
    target.write_text(source)
    result = fmt(str(target))
    assert result.returncode == 0, result.stderr
    assert target.read_text() == (
        "fun main() {\n"
        "\treport(diagnostics, \"unexpected token while parsing the arms of a match\", span, Severity.Error, hint) --@expect(unreachable-code)\n"
        "\tlet x = add(1,\n"
        "        2) --@expect(unreachable-code)\n"
        "}\n"
    )


def _fences() -> list[tuple[str, str, bool]]:
    """Every `kotlin` example in `docs/ref/`: its name, its source, and
    whether the compiler is meant to reject it."""
    out = []
    for chapter in sorted((ROOT / "docs" / "ref").glob("*.md")):
        lines = chapter.read_text().split("\n")
        i = 0
        while i + 1 < len(lines):
            directive = re.match(r"\s*<!--\s*(.*?)\s*-->\s*$", lines[i])
            if directive and lines[i + 1].strip() == "```kotlin":
                j = i + 2
                while lines[j].strip() != "```":
                    j += 1
                out.append((f"{chapter.stem}_{i + 2}", "\n".join(lines[i + 2:j]) + "\n",
                            directive.group(1).startswith("error")))
                i = j
            i += 1
    return out


def test_the_corpus_formats_safely(tmp_path: Path) -> None:
    files = sorted(str(p.relative_to(ROOT)) for top in ("lib", "src", "examples")
                   for p in (ROOT / top).rglob("*.gob"))
    accepted = []
    rejected = []
    for name, source, error in _fences():
        path = tmp_path / f"{name}.gob"
        path.write_text(source)
        (rejected if error else accepted).append(str(path))
    assert len(files) > 100 and len(accepted) > 100
    # `--check` writes nothing; it exits 1 because the corpus is not formatted.
    result = fmt("--check", *files, *accepted)
    assert result.returncode in (0, 1) and result.stderr == "", result.stderr
    # An example the compiler rejects may not even parse, which ends a run, so
    # each is formatted alone.
    for path in rejected:
        result = fmt("--check", path)
        assert "internal error" not in result.stderr, (path, result.stderr)


# Inputs the formatter once refused or could not format twice the same way.
# Each must format, and its output must be formatted already.
AWKWARD = {
    "comment_in_type": "fun f() -> (Int,\n    -- c\n    Int) {\n    (1, 2)\n}\n",
    "comment_after_rest": "import Data.Vec (..) -- c\n",
    "two_end_comments_let": "fun main() {\n    let s = -- a\n        \"x\" -- b\n}\n",
    "two_end_comments_arm": "fun f(t : Option Int) -> Int = match t {\n    Some(x) -> -- a\n        x -- b\n    None -> 0\n}\n",
    "two_end_comments_signature": "fun f(x : Int) -- a\n        -> Int { -- b\n    x\n}\n",
    "block_comment_before_arguments": "fun main() { let y = f {- c -} (1) }\n",
    "block_comment_in_arguments": "fun main() { foo(1, {- c -} 2) }\n",
    "block_comment_in_chain": "fun main() { let x = 1 {- c -} + 2 }\n",
    "block_comments_around_else": "fun main() {\n    if {- c -} a {\n        b()\n    } {- d -} else {- e -} {\n        c()\n    }\n}\n",
    "block_comment_after_fun": "fun {- c -} f() = 1\n",
    "directive_after_broken_signature": "fun f(a : Int,\n      b : Int) -> Int { --@expect(unreachable-code)\n    1\n}\n",
    "directive_after_long_signature": "fun f(aaaaaaaaaaaaaaaaaaaa : Int, bbbbbbbbbbbbbbbbbbbbbbbbbb : Int, cccccccccccccccccccc : Int) -> Int { --@expect(unreachable-code)\n    1\n}\n",
    "directive_after_statements": "fun main() {\n    let a = 1; let b = 2 --@expect(unreachable-code)\n    print(a + b)\n}\n",
    "directive_after_block": "fun main() {\n    let a = { print(1); 2 } --@expect(unreachable-code)\n    print(a)\n}\n",
}


@pytest.mark.parametrize("name", sorted(AWKWARD))
def test_awkward_input_formats(tmp_path: Path, name: str) -> None:
    target = tmp_path / f"{name}.gob"
    target.write_text(AWKWARD[name])
    result = fmt(str(target))
    assert (result.returncode, result.stderr) == (0, "")
    result = fmt("--check", str(target))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_a_directive_line_keeps_its_code(tmp_path: Path) -> None:
    target = tmp_path / "directives.gob"
    target.write_text(AWKWARD["directive_after_statements"] + AWKWARD["directive_after_broken_signature"])
    assert fmt(str(target)).returncode == 0
    lines = target.read_text().split("\n")
    assert "\tlet a = 1; let b = 2 --@expect(unreachable-code)" in lines
    assert "      b : Int) -> Int { --@expect(unreachable-code)" in lines


def test_parentheses_and_literals_stay_as_written(tmp_path: Path) -> None:
    source = (
        "fun f(x : (Int)) -> Option (Vec Int) = None\n"
        "fun g(pair : (Int, Int)) -> Int {\n"
        "\tlet (a, _) = pair\n"
        "\tlet (b) = a\n"
        "\tif let ((x, _) | (_, x)) = pair { x } else { 0x10 + 1_000 + b }\n"
        "}\n"
        "let s = \"\\u{41}\"\n"
        "let h = \\Iter.map(_, g)\n"
    )
    target = tmp_path / "written.gob"
    target.write_text(source)
    result = fmt("--check", str(target))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
