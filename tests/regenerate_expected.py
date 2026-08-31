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


def run_program(source: pathlib.Path) -> tuple[str, int]:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    result = subprocess.run(
        [sys.executable, "-m", "turkey", "run", source.name],
        cwd=source.parent, env=env, capture_output=True, text=True,
    )
    return result.stdout + result.stderr, result.returncode


def sources() -> list[pathlib.Path]:
    """Every program: a single `.tl` file, or a directory's `Main.tl` (M11a)."""
    bundles = [p / "Main.tl" for p in sorted(PROGRAMS.iterdir())
               if p.is_dir() and (p / "Main.tl").is_file()]
    return sorted(PROGRAMS.glob("*.tl")) + bundles


def name_of(source: pathlib.Path) -> str:
    return source.parent.name if source.name == "Main.tl" else source.stem


def main() -> int:
    for source in sources():
        output, code = run_program(source)
        source.with_suffix(".expected").write_text(output, encoding="utf-8")
        expected_failure = name_of(source).startswith("err_")
        if expected_failure != (code != 0):
            word = "fail" if expected_failure else "succeed"
            print(f"warning: {name_of(source)} was expected to {word}, exit {code}")
        print(f"{name_of(source)}: exit {code}, {len(output.splitlines())} line(s)")

    # Only programs that already have a `.types`, `.core` or `.mono` golden
    # get one regenerated; adding a new one is a deliberate act, not a side
    # effect of running this.
    for suffix, command in ((".types", "types"), (".core", "core"),
                            (".mono", "mono")):
        for golden in sorted(PROGRAMS.glob(f"*{suffix}")):
            result = subprocess.run(
                [sys.executable, "-m", "turkey", command,
                 golden.with_suffix(".tl").name],
                cwd=PROGRAMS, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                capture_output=True, text=True,
            )
            golden.write_text(result.stdout + result.stderr, encoding="utf-8")
            print(f"{golden.name}: exit {result.returncode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
