"""`Turkey.Highlight` over the fixtures in `tests/highlight/`, against the HTML
recorded beside each.

`classes.gob` holds one of each token and comment group; `unicode.gob` puts
multi-byte characters before tokens on the same line, where a column counted
in bytes would land in the wrong place; each `broken-*.gob` stops lexing part
way, and everything from there on must come out as plain escaped text.

The highlighter is a compiler module, so a driver beside a `Turkey` link to
`src/Turkey/` imports it, as `boot` itself does.

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
def highlighted(tmp_path_factory) -> dict[Path, str]:
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
                         text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    outputs = ran.stdout.split("\0")
    assert outputs[-1] == ""
    return dict(zip(CASES, outputs[:-1], strict=True))


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.stem)
def test_the_fixture_matches_its_golden(case: Path, highlighted) -> None:
    golden = case.with_suffix(".html")
    if os.environ.get("TURKEY_BLESS_HIGHLIGHT"):
        golden.write_text(highlighted[case], encoding="utf-8")
    assert highlighted[case] == golden.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.stem)
def test_the_text_is_reproduced_exactly(case: Path, highlighted) -> None:
    text = html.unescape(re.sub(r"<[^>]*>", "", highlighted[case]))
    assert text == case.read_text(encoding="utf-8")

