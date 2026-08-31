"""The local reductions (`plan.txt` item 7, M15c).

Inlining, beta, case-of-known-constructor and the dead `let` the other three
leave behind. `tests/test_joins.py` covers the join points these feed;
`tests/programs/joins.opt` is what a reader looks at to see the shape.

What is here is the two kinds of question a golden cannot ask. The first is
whether a reduction *fired* -- a golden shows the answer, not which rule
produced it. The second is whether the ones that must not fire do not, and
those are the ones that matter: every bug found while writing this pass was a
reduction that was correct in general and wrong on a term the suite contained.
Each has a test below, named for what went wrong.
"""

from __future__ import annotations

from dataclasses import fields

from turkey import opt
from turkey.core import CApp, CBind, CExpr, CMatch, CPrim, CProgram, CVar
from turkey.driver import check


def optimized(src: str) -> CProgram:
    return check(src).opt


def named(program: CProgram, name: str) -> CBind:
    for bind in program.dicts + program.binds:
        if bind.name == name:
            return bind
    raise AssertionError(f"no binding named '{name}'")


def nodes(e):
    from turkey.core import CAlt

    if isinstance(e, (CExpr, CBind, CAlt)):
        yield e
        for f in fields(e):
            yield from nodes(getattr(e, f.name))
    elif isinstance(e, (list, tuple)):
        for x in e:
            yield from nodes(x)


def calls_to(bind: CBind, name: str) -> int:
    return sum(1 for n in nodes(bind.value)
               if isinstance(n, CApp) and isinstance(n.fn, CVar)
               and n.fn.name == name)


def matches(bind: CBind) -> int:
    return sum(1 for n in nodes(bind.value) if isinstance(n, CMatch))


# -- the reductions ----------------------------------------------------------


def test_a_small_known_function_is_inlined():
    program = optimized("""
fun addOne(n : Int) -> Int = n + 1
fun main() { print(addOne(4)) }
""")
    assert calls_to(named(program, "Main#main"), "Main#addOne") == 0


def test_a_match_on_a_known_constructor_selects_its_branch():
    """The chain, end to end: `pick(Some(5))` inlines to `match Some(5)`,
    which is where case-of-known-constructor picks the arm and leaves `5`."""
    program = optimized("""
fun pick(o : Option Int) -> Int =
    match o {
        Some(x) -> x
        None -> 0
    }
fun main() { print(pick(Some(5))) }
""")
    main = named(program, "Main#main")
    assert calls_to(main, "Main#pick") == 0
    assert matches(main) == 0, "the match should be gone, not merely moved"


def test_the_dictionary_let_a_devirtualized_method_leaves_is_dropped():
    """M14c turns `d.add(x, 1)` into `%inst.Add.Int#add(x, 1)` and leaves the
    `let %d1.Add` that fetched the dictionary standing with nothing reading
    it. Inlining then drags one to every call site, which is why this rule
    earns its place rather than being tidiness."""
    program = optimized("fun main() { print(1 + 2) }")
    text = str([n.name for n in nodes(named(program, "Main#main").value)
                if isinstance(n, CVar)])
    assert "%d1.Add" not in text


# -- and the reductions that must not fire -----------------------------------


def test_a_body_holding_a_return_is_not_inlined():
    """Found the hard way, on `polyrec.tl`.

    `return` names its target by where it is -- item 7's last step is what
    changes that -- so a body holding one, dropped into a caller, returns from
    the caller. The program printed one line of four and exited cleanly.
    """
    program = optimized("""
fun guard(n : Int) -> Int {
    if n < 0 { return 0 }
    n * 2
}
fun main() {
    print(guard(3))
    print(guard(0 - 1))
}
""")
    assert calls_to(named(program, "Main#main"), "Main#guard") == 2


def test_an_argument_is_not_captured_by_a_loop_variable():
    """Found the hard way, on `monads.tl`.

    `Array`'s `bind` walks its elements in a `for`, so its loop variable is a
    binder -- and `opt._binders_of` did not know that, so the continuation was
    substituted into the scope of the very element it was meant to be applied
    to. `pairs([1,2],[10,20])` came back `[100, 400, 100, 400]` instead of
    `[10, 20, 20, 40]`: `x * y` had become `x * x`.
    """
    from turkey.driver import run
    import io
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        run("""
fun pairs(xs : Array Int, ys : Array Int) -> Array Int =
    bind(xs, fun(x) = bind(ys, fun(y) = pure(x * y)))
fun main() { print(pairs([1, 2], [10, 20])) }
""")
    assert out.getvalue() == "[10, 20, 20, 40]\n"


def test_a_match_on_a_constructor_of_effects_is_left_alone():
    """Selecting a branch discards the arms not taken, and under a wildcard the
    scrutinee itself. `Some(sideEffect())` is a constructor application and is
    not a value, so the rule declines rather than reasoning about effects."""
    program = optimized("""
fun noisy() -> Int {
    print("ran")
    7
}
fun main() {
    let r = match Some(noisy()) {
        Some(_) -> 1
        None -> 0
    }
    print(r)
}
""")
    assert matches(named(program, "Main#main")) == 1


def test_a_let_whose_value_does_work_is_kept_even_if_nothing_reads_it():
    """Which is what every statement in a block is: a `CLet` named `%seq`
    that nothing reads. Dropping those would drop the program."""
    program = optimized('fun main() { print("a"); print("b") }')
    printed = [n for n in nodes(named(program, "Main#main").value)
               if isinstance(n, (CPrim, CVar)) and n.name == "Prim.print"]
    assert len(printed) == 2


# -- termination -------------------------------------------------------------


def test_a_recursive_binding_is_a_loop_breaker():
    checked = check("""
fun countdown(n : Int) -> Int = if n <= 0 { 0 } else { countdown(n - 1) }
fun main() { print(countdown(3)) }
""")
    assert "Main#countdown" in opt.loop_breakers(checked.mono)


def test_a_mutually_recursive_group_breaks_at_one_name_and_always_the_same_one():
    """Arbitrary but stable. `deps.sccs` sorts each component, so the choice is
    the lexicographically first name -- and a `.opt` golden that depends on
    which binding was broken must not move when an unrelated one is added."""
    checked = check("""
fun even(n : Int) -> Bool = if n == 0 { True } else { odd(n - 1) }
fun odd(n : Int) -> Bool = if n == 0 { False } else { even(n - 1) }
fun main() { print(even(4)) }
""")
    broken = opt.loop_breakers(checked.mono) & {"Main#even", "Main#odd"}
    assert broken == {"Main#even"}


def test_mutual_recursion_terminates_and_still_answers():
    """The property the loop breakers exist for. Without one, inlining `even`
    into `odd` into `even` does not stop."""
    from turkey.driver import run
    import io
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        run("""
fun even(n : Int) -> Bool = if n == 0 { True } else { odd(n - 1) }
fun odd(n : Int) -> Bool = if n == 0 { False } else { even(n - 1) }
fun main() { print(even(4)) }
""")
    assert out.getvalue() == "True\n"
