"""The four loop forms, collapsed into one (`plan.txt` item 7, M15e).

`tests/programs/loops.opt` is the readable version, and `loops.mono` beside it
is the same program with the loops still in it. What is here is the properties
that a golden shows but does not state, and the two questions a golden cannot
ask at all: whether the pass is *total* on the suite, and whether the thing it
was built to unlock actually unlocked.

The programs run their loops as well as being inspected, because the whole
milestone is a semantics-preserving rewrite of control flow -- and a rewrite of
control flow that is checked only by reading is one nobody checked.
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import fields
from pathlib import Path

import pytest

from turkey.core import (
    CAlt, CBind, CBreak, CContinue, CExpr, CForC, CForIn, CJoin, CLoop,
    CProgram, CReturn, CWhile,
)
from turkey.driver import check, run

PROGRAMS = Path(__file__).parent / "programs"

# What the pass exists to remove.
GONE = (CWhile, CLoop, CForC, CForIn, CReturn, CBreak, CContinue)


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


@pytest.mark.parametrize("src,answer", [
    (WHILE, "10\n"), (LOOP, "25\n"), (FORC, "3\n"), (FORIN, "Some(4)\n")],
    ids=["while", "loop", "for-c", "for-in"])
def test_each_loop_form_becomes_a_join_and_still_answers(src, answer):
    _, program = optimized(src)
    assert count(program, GONE) == 0
    assert count(program, CJoin) > 0
    assert output(src) == answer


def test_a_for_in_loops_cursor_is_made_explicit():
    """design.md 6.5's elaboration, which `core.py` left as a note that "a
    later pass should" perform. This is that pass, and the cursor binding it
    introduces is the evidence: before it, the `Iterator` protocol lived in the
    evaluator's `_eval_CForIn` and nowhere a checker could see it."""
    from turkey.core import CLet

    _, program = optimized(FORIN)
    lets = [n.name for bind in program.binds for n in nodes(bind.value)
            if isinstance(n, CLet)]
    assert any(name.startswith("%cu") for name in lets), (
        "the cursor `iter` answers should be an ordinary binding now")


def test_a_continue_in_a_c_style_for_still_runs_the_step():
    """The one place the four forms genuinely differ.

    `continue` in a C-style `for` runs the step before the test, so the step is
    a join of its own and `continue` names that one rather than the loop's. Get
    it wrong and `odds` loops forever on the first even number, which is why
    this asserts the answer and not the shape.
    """
    assert output(FORC) == "3\n"


def test_a_break_carries_its_value_as_a_jump_argument():
    """Which is why a loop's join takes a parameter at all."""
    assert output(LOOP) == "25\n"


def test_a_return_out_of_a_loop_leaves_the_function():
    """Two joins deep: the `return` is in the body of a loop join, and jumps
    to the function's own. Join scopes nest, so a tail position inside the
    inner one can still name the outer -- which is the property that made this
    possible without relaxing M15a's rule."""
    assert output(FORIN) == "Some(4)\n"
    assert output(FORIN.replace("[1, 3, 4, 7]", "[1, 3, 5]")) == "None\n"


# -- the two questions a golden cannot ask -----------------------------------


def test_the_pass_is_total_on_the_suite():
    """`loops.collapse` is partial by construction -- a shape its rules do not
    cover leaves the binding alone rather than being guessed at -- and this is
    the number that says how partial. It is zero, and it is asserted rather
    than believed because "no program hits the fallback" is a claim that goes
    stale silently.

    It was not always zero. `bf.tl` writes `Array.push(ops, match c { ... })`
    with a `break` and a `continue` among the arms, which is a transfer inside
    a call argument -- and the other arguments have to be bound around it to
    stay in evaluation order. `loops._operands` is the answer, and this is what
    would notice if a new shape arrived without one.
    """
    for source in sorted(PROGRAMS.glob("*.tl")):
        if source.name.startswith("err_"):
            continue  # these are the programs that are supposed to be rejected
        checked = check(source.read_text(), str(source), [source.parent])
        assert checked.unlooped == 0, f"{source.name} has an unconverted binding"
        assert count(checked.opt, GONE) == 0, f"{source.name} kept a loop node"


def test_the_optimized_core_of_every_program_has_no_loop_nodes_left():
    """The same claim from the other side, and the one that says the collapse
    is a collapse: `turkey mono` still prints four loop forms, and `turkey opt`
    prints none."""
    source = PROGRAMS / "loops.tl"
    checked = check(source.read_text(), str(source), [source.parent])
    assert count(checked.mono, GONE) > 0, "the fixture should have loops in it"
    assert count(checked.opt, GONE) == 0
