"""A small differential check, for iterating between milestones.

The full harness runs `boot` over every entry program for every stage, which is
minutes per stage while `boot` is a Turkey program on generated Python. This is
the same comparison over a handful of programs chosen for coverage, so that a
refactor can be checked in seconds and the full run saved for a boundary.

    python3 tests/quick_diff.py [stage ...]
        default: desugar decls deps classes types
"""
import subprocess
import sys

FILES = [
    "tests/programs/adt.tl",
    "tests/programs/classes.tl",
    "tests/programs/families.tl",
    "tests/programs/question_control.tl",
]
STAGES = sys.argv[1:] or ["desugar", "decls", "deps", "classes",
                          "types", "core"]

failed = False
for stage in STAGES:
    boot = subprocess.run(
        ["python3", "-m", "turkey", "run", "boot/Main.tl", "--", stage, *FILES],
        capture_output=True, text=True)
    if boot.returncode != 0:
        print(f"{stage}: boot exited {boot.returncode}\n{boot.stderr[-2000:]}")
        failed = True
        continue
    out, err = [], []
    for f in FILES:
        r = subprocess.run(["python3", "-m", "turkey", stage, f],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"{stage}: python exited {r.returncode} on {f}\n{r.stderr[-2000:]}")
            failed = True
        out.append(r.stdout)
        err.append(r.stderr)
    want = "".join(out) + "".join(err)
    got = boot.stdout + boot.stderr
    if want == got:
        print(f"{stage}: match ({len(want.splitlines())} lines)")
        continue
    failed = True
    a, b = want.split("\n"), got.split("\n")
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            print(f"{stage}: first difference at line {i + 1}")
            print("  python:", repr(x))
            print("  boot:  ", repr(y))
            break
    else:
        print(f"{stage}: lengths differ, python {len(a)} boot {len(b)}")
sys.exit(1 if failed else 0)
