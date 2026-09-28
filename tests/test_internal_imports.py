"""The library's `Turkey.*` modules are the implementation, not a library.

A program outside the shipped `lib/` may not import one: their interfaces are
raw pointers and C signatures nothing checks. Decided by where the imported
file came from, so the compiler's own `Turkey.*` modules, beside its entry,
are untouched -- which the whole suite checks by building `boot` at all.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain


def _check(entry: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """`boot check`, without the hook the suite sets for the corpus."""
    environment = {k: v for k, v in os.environ.items()
                   if k != bootc.INTERNAL_IMPORTS}
    environment.update(env)
    return subprocess.run(toolchain.command(bootc.binary(), "check", str(entry)),
                          cwd=bootc.REPO_ROOT, env=environment,
                          capture_output=True, text=True)


@pytest.fixture
def importer(tmp_path: Path) -> Path:
    entry = tmp_path / "main.gob"
    entry.write_text("import Turkey.Memory as Memory\n"
                     "fun main() { print(Memory.trailingZeros(8)) }\n",
                     encoding="utf-8")
    return entry


def test_a_program_may_not_import_the_implementation(importer):
    result = _check(importer)
    assert result.returncode != 0
    assert ("'Turkey.Memory' is part of the implementation, and only the "
            "standard library may import it") in result.stderr


def test_the_test_hook_lets_a_named_module_in(importer):
    assert _check(importer, **{bootc.INTERNAL_IMPORTS: "Turkey.Memory"}
                  ).returncode == 0
    assert _check(importer, **{bootc.INTERNAL_IMPORTS: "Turkey.Heap"}
                  ).returncode != 0


def test_a_program_s_own_turkey_module_is_its_own(tmp_path):
    """A module beside the entry is the program's, whatever it is called."""
    (tmp_path / "Turkey").mkdir()
    (tmp_path / "Turkey" / "Mine.gob").write_text(
        "module Turkey.Mine (answer)\nfun answer() -> Int = 42\n",
        encoding="utf-8")
    entry = tmp_path / "main.gob"
    entry.write_text("import Turkey.Mine as Mine\n"
                     "fun main() { print(Mine.answer()) }\n", encoding="utf-8")
    result = _check(entry)
    assert result.returncode == 0, result.stderr
