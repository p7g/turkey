"""`Turkey.Highlight` over the fixtures in `tests/highlight/`, against the HTML
recorded beside each.

`classes.gob` holds one of each token and comment group; `unicode.gob` puts
multi-byte characters before tokens on the same line, where a column counted
in bytes would land in the wrong place; `crlf.gob`, `tab.gob`,
`no-newline.gob` and `empty.gob` are edge cases of the text between tokens;
each `broken-*.gob` stops lexing part way, and everything from there on must
come out as plain escaped text.

Everything is compared as bytes, so a carriage return the highlighter drops or
adds is a failure rather than something newline translation hides.

The highlighter is a compiler module, so a driver beside a `Turkey` link to
`src/Turkey/` imports it, as `turkey` itself does.

`turkey highlight` is the same module behind a command, checked against the
same goldens, along with its escaping and its command line.

To record new output, run with `TURKEY_BLESS_HIGHLIGHT=1`, and read the diff
of `tests/highlight/*.html` before committing it.
"""

from __future__ import annotations

import html
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

FIXTURES = bootc.REPO_ROOT / "tests" / "highlight"
CASES = sorted(FIXTURES.glob("*.gob"))

DRIVER = """\
import System.Env as Env
import System.File as File
import Turkey.Highlight as Highlight

fun main() {
    for path in Env.args() {
        match File.readText(path) {
            Right(text) -> write(Highlight.html(text) + "\\0")
            Left(_) -> error("cannot read " + path)
        }
    }
}
"""


@pytest.fixture(scope="module")
def highlighted(tmp_path_factory) -> dict[Path, bytes]:
    """Each fixture's HTML, from one build and one run of the driver."""
    work = tmp_path_factory.mktemp("highlight")
    (work / "Turkey").symlink_to(bootc.REPO_ROOT / "src" / "Turkey")
    (work / "Main.gob").write_text(DRIVER, encoding="utf-8")
    driver = work / "highlight"
    built = subprocess.run(
        toolchain.command(bootc.binary(), "build", "Main.gob", "-o", str(driver)),
        cwd=work, capture_output=True, text=True, timeout=600,
        env=dict(os.environ, TURKEY_LIB=str(bootc.REPO_ROOT / "lib")))
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(driver), *map(str, CASES)], capture_output=True,
                         timeout=60)
    assert ran.returncode == 0, ran.stderr.decode(errors="replace")
    outputs = ran.stdout.split(b"\0")
    assert outputs[-1] == b""
    return dict(zip(CASES, outputs[:-1], strict=True))


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.stem)
def test_the_fixture_matches_its_golden(case: Path, highlighted) -> None:
    golden = case.with_suffix(".html")
    if os.environ.get("TURKEY_BLESS_HIGHLIGHT"):
        golden.write_bytes(highlighted[case])
    assert highlighted[case] == golden.read_bytes()


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.stem)
def test_the_text_is_reproduced_exactly(case: Path, highlighted) -> None:
    text = html.unescape(re.sub(r"<[^>]*>", "", highlighted[case].decode("utf-8")))
    assert text.encode("utf-8") == case.read_bytes()



def _highlight(*args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(toolchain.command(bootc.binary(), "highlight", *args),
                          input=stdin, capture_output=True, timeout=60)


def test_the_command_writes_a_file_as_the_module_does() -> None:
    """`turkey highlight FILE` writes exactly what `Highlight.html` returns."""
    case = FIXTURES / "classes.gob"
    result = _highlight(str(case))
    assert result.returncode == 0, result.stderr
    assert result.stdout == case.with_suffix(".html").read_bytes()


def test_the_command_reads_standard_input_for_a_dash() -> None:
    case = FIXTURES / "crlf.gob"
    result = _highlight("-", stdin=case.read_bytes())
    assert result.returncode == 0, result.stderr
    assert result.stdout == case.with_suffix(".html").read_bytes()


def test_the_command_escapes_markup_in_every_group() -> None:
    """`<`, `>`, `&` and `"` come out as references wherever they stand, so
    the playground can put the output in the page as HTML."""
    source = 'let s = "<b> & \\"q\\""  -- <i>&amp;</i>\nlet t = a <&> b\n{- </span> -}\n'
    result = _highlight("-", stdin=source.encode())
    assert result.returncode == 0, result.stderr
    out = result.stdout.decode()
    # Every `<` that is left starts one of the highlighter's own tags.
    assert re.findall(r"<(?!span class=\"[a-z-]+\">|/span>)", out) == []
    assert '"' not in re.sub(r'<span class="[a-z-]+">', "", out)
    assert html.unescape(re.sub(r"<[^>]*>", "", out)) == source


@pytest.mark.parametrize("args", [[], ["a.gob", "b.gob"]], ids=["none", "two"])
def test_the_command_takes_one_file(args: list[str]) -> None:
    result = _highlight(*args)
    assert result.returncode == 2
    assert result.stderr == b"turkey: usage: turkey highlight FILE\n       turkey highlight -\n"
    assert result.stdout == b""


def test_the_command_fails_on_text_that_is_not_utf8() -> None:
    result = _highlight("-", stdin=b"\xff\n")
    assert result.returncode == 2
    assert result.stderr == b"turkey: standard input is not valid UTF-8\n"
