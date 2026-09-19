"""Regenerate the golden `.expected` files under tests/programs.

Run from the repository root: `python3 -m tests.regenerate_expected`. Each
program is compiled and run exactly the way `tests/test_programs.py` does it --
the same function -- so the goldens and the runner can never drift apart.

Review the diff before committing: a changed golden is either a fix or a
regression, and only you can tell which. Nothing else checks these files now;
there is no second compiler that has to agree with them.
"""

from __future__ import annotations

import pathlib

from tests import lang
from tests.test_programs import NO_TRACE_YET, PROGRAMS_DIR, conformance

PROGRAMS = PROGRAMS_DIR


def sources() -> list[pathlib.Path]:
    """Every program: a single `.gob` file, or a directory's `Main.gob` (M11a)."""
    bundles = [p / "Main.gob" for p in sorted(PROGRAMS.iterdir())
               if p.is_dir() and (p / "Main.gob").is_file()]
    return sorted(PROGRAMS.glob("*.gob")) + bundles


def name_of(source: pathlib.Path) -> str:
    return source.parent.name if source.name == "Main.gob" else source.stem


def main() -> int:
    for source in sources():
        output, code = conformance(source)
        golden = source.with_suffix(".expected")
        if name_of(source) in NO_TRACE_YET:
            # `boot` prints no panic trace yet (TIX-114), and writing what it
            # does print would delete the trace the golden records.
            print(f"{name_of(source)}: kept, since boot prints no trace yet")
            continue
        golden.write_text(output, encoding="utf-8")
        expected_failure = name_of(source).startswith("err_")
        if expected_failure != (code != 0):
            word = "fail" if expected_failure else "succeed"
            print(f"warning: {name_of(source)} was expected to {word}, exit {code}")
        print(f"{name_of(source)}: exit {code}, {len(output.splitlines())} line(s)")

    # Only programs that already have a `.types`, `.core`, `.mono` or `.opt`
    # get one regenerated; adding a new one is a deliberate act, not a side
    # effect of running this.
    for suffix, command in ((".types", "types"), (".core", "core"),
                            (".mono", "mono"), (".opt", "opt")):
        for golden in sorted(PROGRAMS.glob(f"*{suffix}")):
            result = lang.dump(command, golden.with_suffix(".gob"))
            golden.write_text(result.stdout + result.stderr, encoding="utf-8")
            print(f"{golden.name}: exit {result.code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
