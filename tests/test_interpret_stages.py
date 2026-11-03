"""`boot interpret --stages`, which names the pass a wrong answer comes from.

Each miscompile here is planted on purpose by `TURKEY_SEED_MISCOMPILE`, which
the compiler reads for no other reason: `opt-add-wraps` makes every reduction
rewrite a trapping `Prim.intAdd` into the wrapping one, and `ssa-add-wraps`
makes the lowering below Core do the same. The program overflows, so a
correct compile panics and a seeded one prints the wrapped sum.

`--stages` builds the program natively too, so these link with the C
compiler. Nothing is cached: the seed is in the environment, which no cache
key holds.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from tests import bootc, lang, toolchain

pytestmark = pytest.mark.skipif(toolchain.missing(), reason="no C compiler")

OVERFLOWS = """\
fun main() {
    var total = Int.maxValue() - 2
    for _ in Int.range(0, 5) { total = total + 1 }
    print(total)
}
"""


def _stages(seed: str | None) -> subprocess.CompletedProcess[str]:
    entry = lang.program(OVERFLOWS)
    env = dict(os.environ, TURKEY_LIB=str(lang.LIB))
    env.pop("TURKEY_SEED_MISCOMPILE", None)
    if seed is not None:
        env["TURKEY_SEED_MISCOMPILE"] = seed
    return subprocess.run(
        toolchain.command(bootc.binary(), "interpret", "--stages", entry.name),
        cwd=entry.parent, env=env, capture_output=True, text=True, timeout=600)


def test_every_stage_agrees_when_nothing_is_seeded():
    ran = _stages(None)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout == "interpret: every stage agrees with the native run\n"


def test_a_reduction_that_drops_the_overflow_trap_is_named():
    ran = _stages("opt-add-wraps")
    assert ran.returncode == 1, ran.stdout + ran.stderr
    assert ran.stdout.startswith(
        "interpret: the run after 'reduce' differs from the run after 'mono', so the "
        "fault is in the first reduction (Opt.reduceProgram)\n")
    assert "'panic: integer overflow in +'" in ran.stdout


def test_a_lowering_that_drops_it_is_below_core_or_in_host_inlining():
    ran = _stages("ssa-add-wraps")
    assert ran.returncode == 1, ran.stdout + ran.stderr
    first = ran.stdout.splitlines()[0]
    assert "every stage agrees, and the native run differs" in first
    assert "below Core" in first
    assert "Or it is in inlining host code" in first
