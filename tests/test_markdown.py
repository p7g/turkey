"""`src/Turkey/Markdown.gob`: doc-comment Markdown rendered to HTML.

`tests/markdown/cases.txt` holds one case per construct the renderer
supports, and per way text could be mistaken for markup: each starts with a
`=== NAME` line, followed by the doc-comment text. `src/MarkdownCases.gob`
renders each, and prints the HTML and the summary, which
`tests/markdown/expected.txt` records.

To record new output, run with `TURKEY_BLESS_MARKDOWN=1`, and read the diff of
`tests/markdown/expected.txt` before committing it.
"""

import os
from pathlib import Path

from tests import lang

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "src" / "MarkdownCases.gob"
FIXTURES = REPO_ROOT / "tests" / "markdown"
CASES = FIXTURES / "cases.txt"
GOLDEN = FIXTURES / "expected.txt"


def _rendered() -> str:
    return lang.run(DRIVER, args=(str(CASES),), cwd=REPO_ROOT).stdout


def test_the_cases_match_their_golden() -> None:
    actual = _rendered()
    if os.environ.get("TURKEY_BLESS_MARKDOWN"):
        GOLDEN.write_text(actual)
    assert actual == GOLDEN.read_text()


def test_no_raw_html_is_passed_through() -> None:
    # Every `<` the output holds starts a tag the renderer wrote, which has
    # one of the names below. A tag from the input would be one it did not.
    tags = {"p", "br", "em", "strong", "code", "pre", "a", "ul", "ol", "li",
            "h1", "h2", "h3", "h4", "h5", "h6"}
    for chunk in _rendered().split("<")[1:]:
        name = chunk.split(">")[0].split(" ")[0].lstrip("/")
        assert name in tags, chunk[:60]
