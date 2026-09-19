"""The suite without the Python compiler: `pytest --without-python-compiler`.

The behavioral tests say what Turkey does, and ask `boot` (`tests/lang.py`).
The modules below test the Python implementation itself, or compare `boot`
against it, and go when it goes (TIX-96). This list is that deletion list, and
it is also what `--without-python-compiler` leaves out: under the flag,
importing `turkey` from any other module is an error rather than a quiet
success, so a behavioral test that still leans on the Python cannot pass by
accident. `boot` must then come from `$TURKEY_BOOT`, since building it takes
the Python compiler until the seed exists (TIX-95).
"""

from __future__ import annotations

import importlib.abc
import os
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

#: Test modules of the Python implementation, or of `boot` against it.
PYTHON_ONLY = frozenset({
    # The Python compiler's own passes and data structures.
    "test_backend_ir.py", "test_backend_lower.py", "test_core.py",
    "test_desugar.py", "test_existential_layout.py", "test_joins.py",
    "test_layout.py", "test_lexer.py", "test_llvmgen.py", "test_mono.py",
    "test_opt.py", "test_parser.py", "test_pygen.py", "test_typed_ast.py",
    "test_types.py",
    # The internal halves of modules whose behavioral tests run through `boot`.
    "test_classes_py.py", "test_dicts_py.py", "test_families_py.py",
    "test_giblets_py.py", "test_kinds_py.py", "test_loops_py.py",
    "test_modules_py.py", "test_numeric_py.py", "test_operators_py.py",
    "test_primitives_py.py", "test_projection_py.py", "test_records_py.py",
    "test_typed_py.py",
    # Python oracles: the stage-by-stage differential and the naive inferencer
    # whose answers `infer_corpus.json` keeps.
    "test_boot.py", "test_infer_reference.py",
})

#: Modules in `tests/` that are not test modules and go too.
PYTHON_ONLY_SUPPORT = frozenset({"reference.py", "quick_diff.py"})


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--without-python-compiler", action="store_true",
        help="run only the tests that do not need turkey/, with it unimportable")


class _NoPythonCompiler(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "turkey" or name.startswith("turkey."):
            raise ImportError(
                f"'{name}' is the Python compiler, which this run is without "
                "(--without-python-compiler)")
        return None


def pytest_configure(config: pytest.Config) -> None:
    if not config.getoption("--without-python-compiler"):
        return
    if not os.environ.get("TURKEY_BOOT"):
        raise pytest.UsageError(
            "--without-python-compiler needs $TURKEY_BOOT: building boot "
            "takes the Python compiler")
    sys.meta_path.insert(0, _NoPythonCompiler())


def pytest_ignore_collect(collection_path: Path, config: pytest.Config):
    if (config.getoption("--without-python-compiler")
            and collection_path.name in PYTHON_ONLY):
        return True
    return None
