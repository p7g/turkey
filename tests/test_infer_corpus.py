"""Generalization, over a frozen corpus of generated programs.

Each case is a small program -- lets, functions, lambdas, calls, tuples,
records sharing a field -- and the signatures a naive checker gave it: one
that applies an explicit substitution and generalizes by scanning the
environment, which is the obvious reading of the rule. The real checker uses
Remy's levels instead, an optimization of exactly that scan, so the two must
agree; a disagreement means the level bookkeeping is wrong, which shows up as a
program wrongly accepted rather than as a crash.

The cases were generated from seeded RNGs by `tests/test_infer_reference.py`,
with the naive checker in `tests/reference.py` answering, and written to
`tests/infer_corpus.json` once, while that checker and the Python compiler
both agreed on every one (TIX-94). The generator and the naive checker are
Python and go with the Python compiler; the corpus stays, and is what `boot`
is held to. `expected` is `null` for a program the naive checker rejected.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests import lang

CORPUS = json.loads(
    (Path(__file__).parent / "infer_corpus.json").read_text(encoding="utf-8"))
PRELUDE: str = CORPUS["prelude"]
CASES: list[dict] = CORPUS["cases"]

pytestmark = pytest.mark.skipif(shutil.which("cc") is None,
                                reason="no C compiler")


@pytest.mark.parametrize("case", CASES, ids=[f"seed{c['seed']}" for c in CASES])
def test_levels_agree_with_scanning_the_environment(case: dict) -> None:
    src = PRELUDE + case["source"]
    expected = case["expected"]
    try:
        actual = lang.types(src)
    except lang.CompileError as error:
        assert expected is None, (
            f"boot rejected a program the naive checker accepted:\n\n{src}\n"
            f"{error.rendered}")
        return
    assert expected is not None, (
        f"boot accepted a program the naive checker rejected:\n\n{src}\n{actual}")
    assert [(name, actual.get(name)) for name, _ in expected] == \
        [tuple(pair) for pair in expected], src


def test_the_corpus_is_not_vacuous() -> None:
    """Guards the test above from passing vacuously: enough programs are
    accepted, and enough of those carry a field demand that survived
    generalization -- the rule the two checkers implement differently, and so
    the only one this corpus can catch a bug in."""
    accepted = [c for c in CASES if c["expected"] is not None]
    assert len(CASES) >= 200
    assert len(accepted) >= 50
    assert sum(any("HasField" in scheme for _, scheme in c["expected"])
               for c in accepted) >= 20
