"""Every golden program, run by the interpreter rather than compiled.

`boot interpret` must print what `tests/programs/` records and exit as it
says, at every Core stage it can start from (`--after`): the interpreter is
an oracle only where it agrees with the goldens. A program it cannot run is
listed in `tests/interpret_skips.txt` with the reason, and nowhere else:

* `ffi`: it calls C, or the runtime, directly, which only compiled code
  can (the interpreter exits 98);
* `stack`: interpreting it needs more stack than the interpreter allows
  itself (it exits 99);
* `timing`: the interpreter's slowdown changes what it prints.

A listed program that starts passing fails here too, so that the list is
kept as short as it is true.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests import bootc

REPO_ROOT = bootc.REPO_ROOT
PROGRAMS = REPO_ROOT / "tests" / "programs"
SKIPS = REPO_ROOT / "tests" / "interpret_skips.txt"
STAGES = ["core", "mono", "reduce", "joins", "opt"]
REASONS = {"ffi": 98, "stack": 99, "timing": None}

# What the harness lets a test program in the library's own tree import
# (`tests/harness/Toolchain.gob`).
INTERNAL = "Turkey.Libc,Turkey.Memory,Turkey.Ptr"


def _cases() -> list[tuple[str, Path, str, Path]]:
    out = []
    for path in sorted(PROGRAMS.iterdir()):
        if path.is_dir() and (path / "Main.gob").exists():
            out.append((path.name, path, "Main.gob", path / "Main.expected"))
        elif path.suffix == ".gob":
            out.append((path.stem, PROGRAMS, path.name, PROGRAMS / f"{path.stem}.expected"))
    return out


def _skips() -> dict[tuple[str, str], str]:
    """(program, stage) -> reason. A line is `program reason [stage...]`;
    with no stages it holds at every stage."""
    out = {}
    for line in SKIPS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, reason, *stages = line.split()
        assert reason in REASONS, f"{name}: unknown reason {reason!r}"
        for stage in stages or STAGES:
            out[(name, stage)] = reason
    return out


def _directives(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if line.startswith("--@"):
            name, _, value = line[3:].strip().partition(":")
            out[name.strip()] = value.strip()
    return out


def _without_trace(text: str) -> str:
    """A golden's panic is compared without the lines of its trace."""
    return "".join(line for line in text.splitlines(keepends=True)
                   if not line.startswith("  at "))


CASES = _cases()


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_the_interpreter_agrees_with_the_golden(case, stage):
    name, directory, entry, golden = case
    source = (directory / entry).read_text(encoding="utf-8")
    directives = _directives(source)
    if directives.get("requires") in ("cc", "as", "linux"):
        pytest.skip(f"requires {directives['requires']}")
    reason = _skips().get((name, stage))
    ran = subprocess.run(
        [str(bootc.binary()), "interpret", "--after", stage, entry],
        cwd=directory, capture_output=True, stdin=subprocess.DEVNULL, timeout=300,
        env=dict(os.environ, TURKEY_LIB=str(REPO_ROOT / "lib"),
                 TURKEY_TEST_INTERNAL_IMPORTS=INTERNAL))
    printed = ran.stdout.decode(errors="replace") + ran.stderr.decode(errors="replace")
    expected = golden.read_text(encoding="utf-8") if golden.exists() else ""
    if "error" in directives:
        agrees = ran.returncode != 0 and printed == expected
    elif "panic" in directives or "\npanic: " in "\n" + expected:
        agrees = (ran.returncode not in (0, 98, 99)
                  and _without_trace(printed) == _without_trace(expected))
    else:
        agrees = (ran.returncode == int(directives.get("exit", "0"))
                  and printed == expected)
    if reason is not None:
        assert not agrees, f"{name} is listed as '{reason}' but agrees; unlist it"
        status = REASONS[reason]
        if status is not None:
            assert ran.returncode == status, printed[-2000:]
        pytest.skip(f"interpreter: {reason}")
    assert agrees, f"exited {ran.returncode}\n{printed[-4000:]}"
