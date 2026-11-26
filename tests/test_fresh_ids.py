"""`src/Turkey/Types.gob`: the ids of new type variables, kind variables and
skolems are distinct even when several workers make them at once.

The passes that run on several workers make these cells -- the Core checker
instantiates a builtin's scheme at every use -- and two binders of one scheme
that share an id are substituted as one. `src/FreshCases.gob` makes many of
each from many tasks and prints how many ids were handed out twice.
"""

from pathlib import Path

from tests import lang

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "src" / "FreshCases.gob"


def test_ids_made_on_several_workers_are_distinct() -> None:
    # The suite runs programs on one worker unless a test says otherwise, and
    # one worker cannot interleave two increments.
    result = lang.run(DRIVER, cwd=REPO_ROOT, env={"TURKEY_WORKERS": "8"})
    assert result.code == 0, result.stderr
    assert result.stdout.splitlines() == [
        "type variables repeated: 0",
        "kind variables repeated: 0",
        "skolems repeated: 0",
    ]
