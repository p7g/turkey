"""Join points in Core: the nodes, the rule, what runs them, and what finds them.

`plan.txt` item 7. A join point is a `let`-bound continuation that is only ever
jumped to -- every mention saturated, every mention in tail position, none of
them escaping -- which is what lets a backend compile it as a label instead of
a closure (Maurer, Downen, Ariola and Peyton Jones, *Compiling without
continuations*, PLDI 2017).

Two halves, in the order they were built. **M15a** is the IR and the rule, and
nothing produced a join point when it landed -- so no golden moved, and the
only thing that could say the nodes existed was a test building the terms by
hand and asking the checker about them. As in `tests/test_core.py`, the
interesting assertions there are about terms that must be **rejected**: a
checker exercised only on correct input is asserted to accept, which is the
half that cannot fail.

**M15b** is `turkey/joins.py`, the pass that finds them. Its own evidence is
mostly `tests/programs/joins.opt`, which shows which local function became a
label and which stayed a closure; what is here is the small cases and the
negative ones, where being wrong would be a miscompilation rather than a missed
optimization.

The restriction is the reason these are separate nodes rather than a flag on
`CLetRec` with `CApp` for the jumps. A walker taught nothing about `CJoin`
raises; a walker taught nothing about a flag quietly treats a join point as a
closure and is right, which is the shape of trust item 5 exists to remove.
"""

from __future__ import annotations

import pytest

from turkey import coretc
from turkey.core import (
    CApp, CCon, CIf, CJoin, CJump, CLam, CLet, CLit, CParam, CPrim, CVar,
)
from turkey.builtins import initial_values
from turkey.driver import check
from turkey.eval import Evaluator
from turkey.types import BOOL, TBottom, TCon, TFun

INT = TCon("Int")

SRC = """
fun probe() -> Int = 0
fun main() { print(probe()) }
"""


def built(body):
    """`probe`'s body replaced by a hand-built term of type Int.

    Going through the real pipeline rather than assembling a `CProgram` from
    nothing: `main`, `print` and the Prelude have to be there for the term to
    be checkable and runnable at all, and a fixture that reuses the lowering
    for everything but the one node under test cannot drift away from what the
    lowering actually emits.
    """
    checked = check(SRC)
    bind = _named(checked.core, "Main#probe")
    assert isinstance(bind.value, CLam) and not bind.value.params
    bind.value.body = body
    return checked, checked.core


def _named(program, name):
    for bind in program.dicts + program.binds:
        if bind.name == name:
            return bind
    raise AssertionError(f"no binding named '{name}'")


def accept(checked, program) -> None:
    coretc.check_program(program, checked.decls, checked.classes,
                         coretc.globals_of(checked.env))


def reject(checked, program) -> str:
    with pytest.raises(coretc.CoreError) as exc:
        accept(checked, program)
    return exc.value.message


def evaluate(checked, program) -> None:
    Evaluator(checked.decls, initial_values()).run(program, checked.main)


def var(name: str, ty=INT) -> CVar:
    return CVar(ty, None, name)


def jump(name: str, *args) -> CJump:
    return CJump(TBottom(), None, name, list(args))


def prim(name: str, ret, *args) -> CApp:
    return CApp(ret, None,
                CPrim(TFun([a.ty for a in args], ret), None, name), list(args))


def lit(n: int) -> CLit:
    return CLit(INT, None, "Int", n)


def true() -> CCon:
    """`True` is a constructor, not a literal: `Bool` is an ordinary sum,
    declared in the Prelude and qualified by resolution like anything else."""
    return CCon(BOOL, None, "Data.Bool#True")


# -- what a well-formed join is ----------------------------------------------


def test_a_join_and_a_jump_check_and_run(capsys):
    """`join j(x) = x in jump j(42)`.

    The rest jumps immediately, so what `probe` answers is the join's body --
    which is the whole claim about what a jump means.
    """
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"), jump("%j", lit(42)))
    )
    accept(checked, program)
    evaluate(checked, program)
    assert capsys.readouterr().out == "42\n"


def test_a_branch_that_jumps_joins_with_one_that_does_not(capsys):
    """Bottom absorbs, so an `if` with a jump in one arm still has a type.

    The same slackness `coretc._join` already grants `return`, and the reason
    `_check_CJump` has nothing to add to it: a jump is exactly that kind of
    thing.
    """
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)],
              var("x"),
              CIf(INT, None, true(),
                  jump("%j", lit(7)), lit(9)))
    )
    accept(checked, program)
    evaluate(checked, program)
    assert capsys.readouterr().out == "7\n"


def test_a_recursive_join_is_a_loop_and_not_a_stack(capsys):
    """Twenty thousand jumps, which is well past Python's recursion limit.

    A join point consumes the frame it leaves rather than stacking one, and
    that is the property worth a test rather than an assertion: an evaluator
    that ran a jump as a call would pass every other test in this file and
    fail this one with a `RecursionError`.
    """
    body = CIf(
        INT, None,
        prim("Prim.intLt", BOOL, var("i"), lit(20000)),
        jump("%loop", prim("Prim.intAdd", INT, var("i"), lit(1))),
        var("i"),
    )
    checked, program = built(
        CJoin(INT, None, "%loop", [CParam("i", INT)], body,
              jump("%loop", lit(0)), True)
    )
    accept(checked, program)
    evaluate(checked, program)
    assert capsys.readouterr().out == "20000\n"


def test_a_jump_may_sit_under_a_let_and_a_match():
    """A `let`'s body is a tail position; its value is not."""
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"),
              CLet(INT, None, "%seq", INT, lit(1), jump("%j", lit(3))))
    )
    accept(checked, program)


# -- and what it is not ------------------------------------------------------


def test_the_checker_rejects_a_jump_with_the_wrong_arity():
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"),
              jump("%j", lit(1), lit(2)))
    )
    assert "2 arguments for 1 parameters" in reject(checked, program)


def test_the_checker_rejects_a_jump_with_the_wrong_argument_type():
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"),
              jump("%j", true()))
    )
    message = reject(checked, program)
    assert "argument 1 of the jump" in message and "Int" in message


def test_the_checker_rejects_a_jump_to_a_name_no_join_bound():
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"),
              jump("%elsewhere", lit(1)))
    )
    assert "'%elsewhere' is not a join point in scope" in reject(checked, program)


def test_the_checker_rejects_a_jump_out_of_tail_position():
    """The one the milestone turns on.

    Tail position is never tested for. The join scope is passed down only into
    tail positions, so a jump in an argument -- here, to `id` -- fails to find
    a name that is lexically right there. The message says both halves, since
    "no such join" and "not in tail position" are worth telling apart.
    """
    ident = CLam(TFun([INT], INT), None, [CParam("y", INT)], var("y"))
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"),
              CApp(INT, None, ident, [jump("%j", lit(1))]))
    )
    message = reject(checked, program)
    assert "'%j' is not a join point in scope here" in message
    assert "tail position" in message


def test_the_checker_rejects_a_jump_from_inside_a_lambda():
    """A lambda body is out of tail position, and this is why it must be.

    A join point is a label in the frame that binds it. A closure that outlives
    that frame and jumps into it is exactly the term a label cannot compile,
    and it is also what makes the evaluator's catch-by-name safe: no jump can
    reach a `CJoin` that is not lexically above it.
    """
    escaping = CLam(TFun([], INT), None, [], jump("%j", lit(1)))
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"),
              CApp(INT, None, escaping, []))
    )
    assert "not a join point in scope here" in reject(checked, program)


def test_the_checker_rejects_a_self_jump_in_a_join_that_is_not_recursive():
    """`recursive` is a claim about the term, so it is checked like one."""
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], jump("%j", var("x")),
              jump("%j", lit(1)))
    )
    assert "not a join point in scope here" in reject(checked, program)


def test_a_recursive_join_may_jump_to_itself():
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)],
              CIf(INT, None, true(),
                  var("x"), jump("%j", var("x"))),
              jump("%j", lit(1)), True)
    )
    accept(checked, program)


def test_the_checker_rejects_a_jump_that_claims_to_yield():
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"),
              CJump(INT, None, "%j", [lit(1)]))
    )
    assert "it never yields to its context" in reject(checked, program)


def test_the_checker_rejects_a_body_that_answers_the_wrong_type():
    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)],
              true(), jump("%j", lit(1)))
    )
    assert "the join point '%j'" in reject(checked, program)


# -- the walkers that already existed ----------------------------------------


def test_specialization_copies_a_join_point(capsys):
    """`mono`'s rewriter is reflective over the dataclass, and this says so.

    It walks `fields(e)` rather than naming one case per node, precisely so a
    node added later needs no edit there -- but "needs no edit" is a claim, and
    a claim about a walker is worth a term that goes through it. `probe` is
    ground, so the rewriter that copies it has an empty substitution and only
    has to preserve the shape.
    """
    from turkey import mono

    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"), jump("%j", lit(5)))
    )
    out = mono.monomorphize(program, checked.decls, checked.classes, checked.main)
    copied = _named(out, "Main#probe").value
    assert isinstance(copied.body, CJoin) and isinstance(copied.body.rest, CJump)
    accept(checked, out)
    evaluate(checked, out)
    assert capsys.readouterr().out == "5\n"


def test_the_printer_renders_a_join_and_a_jump():
    from turkey.core import show_bind

    checked, program = built(
        CJoin(INT, None, "%j", [CParam("x", INT)], var("x"), jump("%j", lit(5)))
    )
    text = show_bind(_named(program, "Main#probe"))
    assert "join %j(x : Int) : Int = {" in text
    assert "jump %j(5)" in text


# -- discovery (M15b) --------------------------------------------------------


def opt(src: str):
    """A program's optimized Core, and the `Checked` it came from."""
    checked = check(src)
    return checked, checked.opt


def joins_in(program, name: str):
    from dataclasses import fields
    from turkey.core import CAlt, CBind, CExpr

    found = []

    def go(v):
        if isinstance(v, CJoin):
            found.append(v)
        if isinstance(v, (CExpr, CBind, CAlt)):
            for f in fields(v):
                go(getattr(v, f.name))
        elif isinstance(v, (list, tuple)):
            for x in v:
                go(x)

    go(_named(program, name).value)
    return found


def test_a_local_called_only_from_tail_positions_becomes_a_label():
    checked, program = opt("""
fun converge(n : Int) -> Int {
    let tag = fun(x : Int) -> Int { x + 1 }
    if n > 0 { tag(n) } else { tag(0 - n) }
}
fun main() { print(converge(1)) }
""")
    found = joins_in(program, "Main#converge")
    assert len(found) == 1 and not found[0].recursive
    accept(checked, program)


def test_a_tail_recursive_local_becomes_a_recursive_label():
    """A loop, and nothing in the surface program said so.

    The fact is derived from where the calls are. That is the argument for
    doing this in Core instead of in the desugaring: the desugaring can only
    know what the author wrote.
    """
    checked, program = opt("""
fun countdown(n : Int) -> Int {
    fun go(i : Int, acc : Int) -> Int =
        if i <= 0 { acc } else { go(i - 1, acc + i) }
    go(n, 0)
}
fun main() { print(countdown(4)) }
""")
    found = joins_in(program, "Main#countdown")
    assert len(found) == 1 and found[0].recursive
    accept(checked, program)


def test_a_local_that_escapes_stays_a_closure():
    """`twice` calls its argument, so `step` is passed somewhere.

    A label is not something you can pass, and this is the case where being
    wrong would be a miscompilation rather than a missed optimization.
    """
    _, program = opt("""
fun twice(f : fun(Int) -> Int, x : Int) -> Int = f(f(x))
fun escapes(n : Int) -> Int {
    let step = fun(x : Int) -> Int { x + 3 }
    twice(step, n)
}
fun main() { print(escapes(1)) }
""")
    assert not joins_in(program, "Main#escapes")


def test_a_local_called_out_of_tail_position_stays_a_closure():
    """The call is saturated and the name never escapes, and it still does not
    qualify: its value is not the value of the body, so a jump would discard
    the `+ 1` that comes after it."""
    _, program = opt("""
fun offset(n : Int) -> Int {
    let tag = fun(x : Int) -> Int { x * 2 }
    tag(n) + 1
}
fun main() { print(offset(1)) }
""")
    assert not joins_in(program, "Main#offset")


def test_the_optimized_program_is_the_one_that_runs(capsys):
    """M14b's rule, one stage further along: a pass whose output is only ever
    inspected is a pass nothing tests. So `driver.run` evaluates `opt`, and
    every golden in `tests/programs` exercises the join evaluator."""
    from turkey.driver import run

    run("""
fun countdown(n : Int) -> Int {
    fun go(i : Int, acc : Int) -> Int =
        if i <= 0 { acc } else { go(i - 1, acc + i) }
    go(n, 0)
}
fun main() { print(countdown(4)) }
""")
    assert capsys.readouterr().out == "10\n"


def test_the_checker_accepts_a_jump_in_every_position_the_table_calls_a_tail():
    """`core.TAIL_FIELDS` is read by `joins.py` to find tail calls and by
    `coretc.py` to decide what a join scope reaches. This is the third reader,
    and it exists so the table cannot quietly grow an entry the rule does not
    honour -- which would be a pass emitting jumps the checker rejects.
    """
    from turkey.core import TAIL_FIELDS

    assert set(TAIL_FIELDS) == {"CIf", "CLet", "CLetRec", "CJoin", "CMatch"}, (
        "a new entry needs a case below, and a rule in coretc that honours it")

    from turkey import ast
    from turkey.core import CAlt, CBind, CLetRec, CMatch

    inner = jump("%j", lit(4))
    bodies = [
        CIf(INT, None, true(), inner, lit(0)),
        CIf(INT, None, true(), lit(0), inner),
        CLet(INT, None, "%seq", INT, lit(1), inner),
        CLetRec(INT, None,
                [CBind("%r", TFun([], INT), [],
                       CLam(TFun([], INT), None, [], lit(0)))],
                inner),
        CJoin(INT, None, "%k", [], lit(0), inner),
        CMatch(INT, None, lit(1), [CAlt(ast.PWild(None), inner)]),
    ]
    for body in bodies:
        checked, program = built(
            CJoin(INT, None, "%j", [CParam("x", INT)], var("x"), body))
        accept(checked, program)
