"""Loop lowering as the Python Core nodes show it.

Internal: split from `test_loops.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import fields
from pathlib import Path

import pytest

from turkey import core
from turkey.core import CAlt, CBind, CExpr, CJoin, CJump, CLet, CProgram
from turkey.driver import check, run

PROGRAMS = Path(__file__).parent / "programs"

# The nodes M16a deleted. Named as strings because the point of the test is
# that there is nothing left to import.
GONE = ("CWhile", "CLoop", "CForC", "CForIn", "CReturn", "CBreak", "CContinue")


def optimized(src: str):
    checked = check(src)
    return checked, checked.opt


def nodes(e):
    if isinstance(e, (CExpr, CBind, CAlt)):
        yield e
        for f in fields(e):
            yield from nodes(getattr(e, f.name))
    elif isinstance(e, (list, tuple)):
        for x in e:
            yield from nodes(x)


def count(program: CProgram, kinds) -> int:
    return sum(1 for bind in program.dicts + program.binds
               for n in nodes(bind.value) if isinstance(n, kinds))


def output(src: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        run(src)
    return out.getvalue()


# -- each of the four --------------------------------------------------------


WHILE = """
fun total(n : Int) -> Int {
    var t = 0
    var i = 0
    while i < n { t = t + i; i = i + 1 }
    t
}
fun main() { print(total(5)) }
"""

LOOP = """
fun firstOver(limit : Int) -> Int {
    var i = 0
    loop {
        i = i + 1
        if i * i > limit { break i * i }
    }
}
fun main() { print(firstOver(20)) }
"""

FORC = """
fun odds(n : Int) -> Int {
    var seen = 0
    for var i = 0; i < n; i = i + 1 {
        if i % 2 == 0 { continue }
        seen = seen + 1
    }
    seen
}
fun main() { print(odds(7)) }
"""

FORIN = """
fun firstEven(xs : Array Int) -> Option Int {
    for x in xs {
        if x % 2 == 0 { return Some(x) }
    }
    None
}
fun main() { print(firstEven([1, 3, 4, 7])) }
"""


def test_a_for_in_loops_cursor_is_made_explicit_before_anything_checks_it():
    """design.md 6.5's elaboration, which `core.py` left as a note that "a
    later pass should" perform.

    The lowering is that pass now, and *that* is the improvement over M15e
    rather than the elaboration itself. `driver.check` runs
    `coretc.check_program` on the lowering's output, so the cursor binding and
    the `Option` match that reads `next`'s answer are checked. When the same
    elaboration happened after monomorphization, the Core checker had already
    accepted the un-elaborated node and never saw what replaced it.
    """
    checked, _ = optimized(FORIN)
    lets = [n.name for bind in checked.core.binds for n in nodes(bind.value)
            if isinstance(n, CLet)]
    assert any(name.startswith("%cu") for name in lets), (
        "the cursor `iter` answers should be an ordinary binding now")


# -- the question a golden cannot ask ----------------------------------------


def test_the_loop_nodes_are_gone_from_the_ir():
    """The whole of M16a, as one assertion.

    M15e could only say "no program in the suite reaches the evaluator with a
    loop node left", which is a claim about the suite. This is a claim about
    the IR: there is no `CWhile` to construct, so a pass cannot emit one by
    accident and a later milestone cannot quietly reintroduce the fallback
    that M15e's partiality needed.
    """
    for name in GONE:
        assert not hasattr(core, name), f"core.{name} is back"
        assert name not in core.__all__


@pytest.mark.parametrize(
    "source",
    sorted(p for p in PROGRAMS.glob("*.gob") if not p.name.startswith("err_")),
    ids=lambda p: p.stem)
def test_every_program_lowers_to_joins_with_nothing_declined(source):
    """`loops.collapse` was partial by construction -- a shape its rules did
    not cover left the binding alone rather than being guessed at -- and
    `Checked.unlooped` counted how partial. The count is gone because the
    fallback is: there is no loop node to leave behind, so a shape with no
    rule is an error at compile time rather than a term that quietly still
    holds a loop.

    So what this asserts is that no program in the suite hits one: a shape
    `lower.anf` has no rule for raises `Unsupported`, and compiling every
    program is how that is noticed. `driver.check` also runs
    `coretc.check_program` on all three stages, so a jump this pass put
    outside a tail position fails here too, on every program, on every run.

    One item per program, so `pytest -n auto` spreads them; the corpus-wide
    "something lowered a transfer" guard is the next test, on the fixture
    built to have one.
    """
    check(source.read_text(), str(source), [source.parent])


def test_a_program_with_loops_lowers_to_joins_and_jumps():
    """The positive form of the same claim, on the fixture built for it."""
    source = PROGRAMS / "loops.gob"
    checked = check(source.read_text(), str(source), [source.parent])
    for stage in (checked.core, checked.mono, checked.opt):
        assert count(stage, CJoin) > 0
        assert count(stage, CJump) > 0
