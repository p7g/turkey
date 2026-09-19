"""Every example in the language reference, compiled and run.

`docs/ref/*.md` marks each Turkey example with a directive in an HTML comment
on the line before its ```kotlin fence (see docs/ref/README.md):

    <!-- run -->          a whole program; the next ```text fence is its stdout
    <!-- check -->        must compile
    <!-- error: TEXT -->  must be rejected, with TEXT in the message
    <!-- panic: TEXT -->  must compile and then panic, with TEXT in the message;
                          an optional ```text fence is the stdout before it
    <!-- module: F.gob -->  another file of the next example's program

A ```kotlin fence without a directive is itself a failure: the reference
promises that its examples are real, and an unmarked one is a promise nobody
checks.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from turkey.driver import check, run
from turkey.errors import TurkeyError, TurkeyPanic

REPO_ROOT = Path(__file__).resolve().parent.parent
REF_DIR = REPO_ROOT / "docs" / "ref"

DIRECTIVE = re.compile(r"^<!--\s*(run|check|error|panic|module)\s*(?::\s*(.*?))?\s*-->$")
FENCE = re.compile(r"^```(\S*)\s*$")


@dataclass
class Example:
    file: str
    line: int
    mode: str
    arg: str
    src: str
    output: str | None = None
    modules: dict[str, str] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.file}:{self.line}"


class Malformed(Exception):
    pass


def _fence(lines: list[str], i: int) -> tuple[str, str, int]:
    """The fence opening at or after line `i` (skipping blanks): its
    language, its body, and the index just past its closing line."""
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not (m := FENCE.match(lines[i])):
        raise Malformed(f"line {i + 1}: expected a code fence")
    body = []
    j = i + 1
    while j < len(lines) and lines[j].rstrip() != "```":
        body.append(lines[j])
        j += 1
    if j >= len(lines):
        raise Malformed(f"line {i + 1}: unterminated fence")
    return m.group(1), "\n".join(body) + "\n", j + 1


def _next_is_text(lines: list[str], i: int) -> bool:
    while i < len(lines) and not lines[i].strip():
        i += 1
    return i < len(lines) and lines[i].rstrip() == "```text"


def extract(path: Path) -> list[Example]:
    lines = path.read_text().splitlines()
    examples: list[Example] = []
    modules: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if (d := DIRECTIVE.match(line)):
            mode, arg = d.group(1), d.group(2) or ""
            lang, src, i = _fence(lines, i + 1)
            if lang != "kotlin":
                raise Malformed(f"{path.name}:{i}: a directive must precede a kotlin fence")
            if mode == "module":
                if not arg:
                    raise Malformed(f"{path.name}:{i}: `module` needs a file name")
                modules[arg] = src
                continue
            example = Example(path.name, i, mode, arg, src, modules=modules)
            modules = {}
            if mode in ("error", "panic") and not arg:
                raise Malformed(f"{example.id}: `{mode}` needs an argument")
            if mode == "run" or (mode == "panic" and _next_is_text(lines, i)):
                if not _next_is_text(lines, i):
                    raise Malformed(f"{example.id}: `{mode}` needs a text fence")
                _, example.output, i = _fence(lines, i)
            examples.append(example)
            continue
        if (m := FENCE.match(line)):
            if m.group(1) == "kotlin":
                raise Malformed(f"{path.name}:{i + 1}: kotlin fence with no directive")
            _, _, i = _fence(lines, i)
            continue
        i += 1
    if modules:
        raise Malformed(f"{path.name}: `module` blocks with no example after them")
    return examples


def _collect() -> list[Example]:
    out: list[Example] = []
    for path in sorted(REF_DIR.glob("*.md")):
        out.extend(extract(path))
    return out


EXAMPLES = _collect()


def _program(example: Example, tmp_path: Path) -> tuple[str, str]:
    """Source and file name: a single example runs as `<input>`, one with
    modules is written out beside them so imports resolve."""
    if not example.modules:
        return example.src, "<input>"
    for name, src in example.modules.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src)
    main = tmp_path / "Main.gob"
    main.write_text(example.src)
    return example.src, str(main)


def _check(src: str, filename: str) -> None:
    if filename == "<input>":
        check(src)
    else:
        check(src, filename, [Path(filename).parent])


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e.id)
def test_example(example: Example, tmp_path, capsys) -> None:
    src, filename = _program(example, tmp_path)
    if example.mode == "check":
        _check(src, filename)
    elif example.mode == "error":
        with pytest.raises(TurkeyError) as exc:
            _check(src, filename)
        assert example.arg in exc.value.message, exc.value.message
    elif example.mode == "run":
        run(src, filename)
        assert capsys.readouterr().out == example.output
    elif example.mode == "panic":
        with pytest.raises(TurkeyPanic) as exc:
            run(src, filename)
        assert example.arg in exc.value.message, exc.value.message
        if example.output is not None:
            assert capsys.readouterr().out == example.output


RUNNABLE = [e for e in EXAMPLES if e.mode in ("run", "panic")]


@pytest.mark.parametrize("example", RUNNABLE, ids=lambda e: e.id)
def test_example_native(example: Example, tmp_path) -> None:
    """The same program through `turkey run`, whose default backend is the
    native one: what a reader who copies the example will actually execute."""
    if shutil.which("cc") is None:
        pytest.skip("no C compiler")
    _program(example, tmp_path)
    main = tmp_path / "Main.gob"
    if not main.exists():
        main.write_text(example.src)
    result = subprocess.run(
        [sys.executable, "-m", "turkey", "run", "Main.gob"],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
        text=True,
    )
    if example.mode == "run":
        assert result.returncode == 0, result.stderr
        assert result.stdout == example.output
    else:
        assert result.returncode != 0
        assert "panic: " in result.stderr and example.arg in result.stderr, result.stderr
        if example.output is not None:
            assert result.stdout == example.output


def test_there_are_examples() -> None:
    assert EXAMPLES


def test_unmarked_fence_is_rejected(tmp_path) -> None:
    doc = tmp_path / "bad.md"
    doc.write_text("# Bad\n\n```kotlin\nfun main() { }\n```\n")
    with pytest.raises(Malformed):
        extract(doc)
