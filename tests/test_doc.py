"""`boot doc` over a fixture, against the JSON recorded for it.

`tests/doc/Fixture.gob` declares one of each kind of entity `boot doc`
describes, re-exports from `tests/doc/Shapes.gob`, and places its comments
on each edge of the attachment rule (`src/Turkey/Doc.gob`): a comment above a
field, a constructor, a method and an instance; a block comment; a comment
cut off by a blank line; a trailing comment above a declaration; and the
module comment.

To record new output, run with `TURKEY_BLESS_DOC=1`, and read the diff of
`tests/doc/docs.json` before committing it.
"""

from __future__ import annotations

import json
import os
import subprocess

from tests import bootc, toolchain

FIXTURES = bootc.REPO_ROOT / "tests" / "doc"
GOLDEN = FIXTURES / "docs.json"


def _doc(*files: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(toolchain.command(bootc.binary(), "doc", *files),
                          cwd=bootc.REPO_ROOT, capture_output=True, text=True,
                          timeout=120)


def test_the_fixture_matches_its_golden() -> None:
    result = _doc("tests/doc/Fixture.gob", "tests/doc/Shapes.gob")
    assert result.returncode == 0, result.stderr
    if os.environ.get("TURKEY_BLESS_DOC"):
        GOLDEN.write_text(result.stdout)
    assert result.stdout == GOLDEN.read_text()


def test_the_output_does_not_depend_on_the_order_of_the_files() -> None:
    forwards = _doc("tests/doc/Fixture.gob", "tests/doc/Shapes.gob")
    backwards = _doc("tests/doc/Shapes.gob", "tests/doc/Fixture.gob")
    assert forwards.stdout == backwards.stdout


def test_every_library_function_has_a_type() -> None:
    files = sorted(str(p.relative_to(bootc.REPO_ROOT))
                   for p in (bootc.REPO_ROOT / "lib").rglob("*.gob")
                   if "Turkey" not in p.relative_to(bootc.REPO_ROOT / "lib").parts[:1])
    result = _doc(*files)
    assert result.returncode == 0, result.stderr
    modules = json.loads(result.stdout)["modules"]
    assert len(modules) == len(files)
    untyped = [(m["name"], item["name"]) for m in modules for item in m["items"]
               if item["kind"] in ("function", "value") and item["type"] is None]
    assert untyped == []


def test_modules_outside_the_library_must_share_a_directory() -> None:
    result = _doc("tests/doc/Fixture.gob", "tests/programs/adt.gob")
    assert result.returncode == 2
    assert "document each directory separately" in result.stderr
