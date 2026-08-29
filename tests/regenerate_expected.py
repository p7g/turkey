"""Regenerate the golden `.expected` files under tests/programs.

Run from anywhere: `python3 tests/regenerate_expected.py`. Each program is run
exactly the way `tests/test_programs.py` runs it -- same working directory, same
capture -- so the goldens and the runner can never drift apart.

Review the diff before committing: a changed golden is either a fix or a
regression, and only you can tell which.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROGRAMS = ROOT / "tests" / "programs"


def run_program(name: str) -> tuple[str, int]:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run(
        [sys.executable, "-m", "turkey", "run", name],
        cwd=PROGRAMS, env=env, capture_output=True, text=True,
    )
    return result.stdout + result.stderr, result.returncode


def main() -> int:
    for source in sorted(PROGRAMS.glob("*.tl")):
        output, code = run_program(source.name)
        source.with_suffix(".expected").write_text(output, encoding="utf-8")
        expected_failure = source.stem.startswith("err_")
        if expected_failure != (code != 0):
            word = "fail" if expected_failure else "succeed"
            print(f"warning: {source.name} was expected to {word}, exit code {code}")
        print(f"{source.name}: exit {code}, {len(output.splitlines())} line(s)")

    # Only programs that already have a `.types` golden get one regenerated;
    # adding a new one is a deliberate act, not a side effect of running this.
    for golden in sorted(PROGRAMS.glob("*.types")):
        types = subprocess.run(
            [sys.executable, "-m", "turkey", "types", golden.with_suffix(".tl").name],
            cwd=PROGRAMS, env=dict(os.environ, PYTHONPATH=str(ROOT)),
            capture_output=True, text=True,
        )
        golden.write_text(types.stdout + types.stderr, encoding="utf-8")
        print(f"{golden.name}: exit {types.returncode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
