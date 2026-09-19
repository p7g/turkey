"""Which test modules need the Python compiler, kept honest.

`conftest.PYTHON_ONLY` is both what `--without-python-compiler` leaves out and
the list of what goes with `turkey/` (TIX-96). Both uses fail quietly if it
drifts: a behavioral module that grows a `turkey` import would be skipped by
nothing and fail only under the flag, and a Python-only module missing from
the list would be deleted by nobody. So the list is checked against the
imports.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.conftest import PYTHON_ONLY, PYTHON_ONLY_SUPPORT, TESTS


# `python -m turkey` in a subprocess, spelled so this file does not match it.
_SUBPROCESS = '"-m", ' + '"turkey"'


def _needs_python(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if _SUBPROCESS in source:
        return True
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.split(".")[0] == "turkey":
            return True
        if isinstance(node, ast.Import) and any(
                alias.name.split(".")[0] == "turkey" for alias in node.names):
            return True
    return False


def test_every_module_that_needs_the_python_compiler_is_listed():
    needing = {path.name for path in TESTS.glob("*.py") if _needs_python(path)}
    # `bootc` builds `boot` with the Python compiler when `$TURKEY_BOOT` does
    # not name one; that is the build, which TIX-95 replaces, not a test.
    needing.discard("bootc.py")
    assert needing <= PYTHON_ONLY | PYTHON_ONLY_SUPPORT, sorted(
        needing - PYTHON_ONLY - PYTHON_ONLY_SUPPORT)


def test_every_listed_module_exists():
    present = {path.name for path in TESTS.glob("*.py")}
    assert PYTHON_ONLY | PYTHON_ONLY_SUPPORT <= present, sorted(
        (PYTHON_ONLY | PYTHON_ONLY_SUPPORT) - present)
