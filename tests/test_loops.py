"""The four loop forms, lowered into one (`plan.txt` item 7, M16a).

`tests/programs/loops.opt` is the readable version. What is here is the
properties a golden shows but does not state, and the question a golden cannot
ask at all: whether the loop forms are *gone from the IR* rather than merely
absent from the programs that happen to be in the suite.

That is the change M16a made. M15e collapsed the loops in a Core-to-Core pass
after monomorphization, so nothing that ran held one -- but `turkey/lower.py`
still emitted them, `coretc.py` and `eval.py` still had to know them, and the
pass was partial by construction, with a fallback that left a binding alone.
Now the lowering builds join points directly and there is no node to leave: a
term that names its control target by where it sits cannot be written.

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


@pytest.mark.parametrize("src,answer", [
    (WHILE, "10\n"), (LOOP, "25\n"), (FORC, "3\n"), (FORIN, "Some(4)\n")],
    ids=["while", "loop", "for-c", "for-in"])
def test_each_loop_form_becomes_a_join_and_still_answers(src, answer):
    checked, program = optimized(src)
    # In the *lowering's* own output, not just after the optimizer: this is
    # what "lowered, not collapsed" means, and the earlier the claim holds the
    # fewer passes had to know what a loop was.
    assert count(checked.core, CJoin) > 0
    assert count(program, CJoin) > 0
    assert output(src) == answer


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


def test_a_transfer_inside_a_call_argument_keeps_evaluation_order():
    """`bf.tl` writes `Array.push(ops, match c { ']' -> break, _ -> ... })`.

    A transfer in an operand cannot simply be hoisted out: the arguments
    beside it would then be evaluated after it, and the evaluator is strict
    and left to right. `lower.anf` binds every operand up to the last
    transferring one, in order, which is A-normal form arrived at because the
    order has to be preserved rather than because the form is wanted.

    Asserted by what it prints, because getting it wrong reorders effects
    rather than producing a term anything would reject.
    """
    src = """
fun main() {
    var log = []
    var i = 0
    loop {
        i = i + 1
        Array.push(log, if i > 2 { break } else { i })
    }
    print(log)
}
"""
    assert output(src) == "[1, 2]\n"


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


def test_every_program_lowers_to_joins_with_nothing_declined():
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
    """
    jumped = 0
    for source in sorted(PROGRAMS.glob("*.tl")):
        if source.name.startswith("err_"):
            continue  # these are the programs that are supposed to be rejected
        checked = check(source.read_text(), str(source), [source.parent])
        jumped += count(checked.core, CJump)
    assert jumped > 0, "no program in the suite lowered a transfer at all"


def test_a_program_with_loops_lowers_to_joins_and_jumps():
    """The positive form of the same claim, on the fixture built for it."""
    source = PROGRAMS / "loops.tl"
    checked = check(source.read_text(), str(source), [source.parent])
    for stage in (checked.core, checked.mono, checked.opt):
        assert count(stage, CJoin) > 0
        assert count(stage, CJump) > 0
