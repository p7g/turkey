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

Every example is compiled by `boot` and run as a native program, through
`tests.lang`: what a reader who copies it will execute, and the implementation
that stays when the Python one goes (TIX-94).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests import lang

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


@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e.id)
def test_example(example: Example) -> None:
    modules = example.modules or None
    if example.mode == "check":
        lang.check(example.src, modules)
    elif example.mode == "error":
        message = lang.fails(example.src, modules)
        assert example.arg in message, message
    elif example.mode == "run":
        assert lang.output(example.src, modules) == example.output
    elif example.mode == "panic":
        result = lang.panics(example.src, modules)
        assert example.arg in lang.panic_message(result), result.stderr
        if example.output is not None:
            assert result.stdout == example.output


def test_there_are_examples() -> None:
    assert EXAMPLES


def test_unmarked_fence_is_rejected(tmp_path) -> None:
    doc = tmp_path / "bad.md"
    doc.write_text("# Bad\n\n```kotlin\nfun main() { }\n```\n")
    with pytest.raises(Malformed):
        extract(doc)
