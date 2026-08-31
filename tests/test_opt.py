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
from turkey.core import (
    CApp, CBind, CExpr, CMatch, CPrim, CProgram, CTyApp, CVar,
)
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


def test_a_body_that_used_to_hold_a_return_is_inlined_now():
    """The debt M15e settles, written as the test that used to assert it.

    `return` named its target by where it was, so a body holding one could not
    be moved: dropped into a caller it returned from the caller, and `polyrec.tl`
    printed one line of four. The pass refused to inline any such body, which
    was one function in eight across the suite.

    `turkey/lower.py` makes that `return` a jump to a `%ret` join in the
    function's own body, and a join point travels with the term it is in. So
    the refusal is gone and the call is inlined. `opt._transfers` still
    guards, but it asks a sharper question now -- whether a jump names a join
    bound *outside* the term being moved -- and on a whole function body the
    answer is no.
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
    assert calls_to(named(program, "Main#main"), "Main#guard") == 0


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


def test_a_match_on_a_constructor_of_effects_keeps_the_effects():
    """Selecting a branch discards the arms not taken -- which the `match`
    would not have run anyway -- and the constructor application itself, which
    it would have. So the arguments are bound in order, including the ones the
    pattern ignores. `Some(noisy())` still prints.

    This started life as a test that the rule *declined* on a scrutinee that
    was not a value. Declining was the safe first answer and the wrong one: it
    also declined on `Some(n / 2)`, which is every `?` over `Option` there is.
    """
    from turkey.driver import run
    import io
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        run("""
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
    assert out.getvalue() == "ran\n1\n"


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


# -- case-of-case (M15d) -----------------------------------------------------


def test_a_match_on_an_if_is_pushed_into_its_branches():
    """`match (if c { Some(v) } else { None }) { ... }`.

    Both branches are constructors, so both collapse and nothing is left to
    jump to -- the join point is built and then dropped, which is the case
    worth having a test for, since the golden shows only its absence.
    """
    program = optimized("""
fun maybe(n : Int) -> Option Int = if n > 0 { Some(n) } else { None }
fun classify(n : Int) -> Int =
    match maybe(n) {
        Some(x) -> x
        None -> 0
    }
fun main() { print(classify(3)) }
""")
    assert matches(named(program, "Main#classify")) == 0


def test_a_question_chain_becomes_nested_ifs():
    """The milestone, stated as a property rather than shown as a golden.

    `tests/programs/question_opt.opt` is the readable version. What is asserted
    here is what it means: after the passes, a two-`?` function over `Option`
    contains no call, no lambda and no `match` -- the intermediate `Option` is
    never built, and what remains is the nested `if` a C programmer writes by
    hand. No pass involved knows what a monad is.
    """
    from turkey.core import CLam

    program = optimized("""
fun half(n : Int) -> Option Int =
    if n % 2 == 0 { Some(n / 2) } else { None }
fun quarter(n : Int) -> Option Int {
    let a = half(n)?
    let b = half(a)?
    Some(b)
}
fun main() { print(quarter(8)) }
""")
    quarter = named(program, "Main#quarter")
    assert matches(quarter) == 0
    assert sum(1 for n in nodes(quarter.value) if isinstance(n, CLam)) == 1, \
        "only the function's own lambda: the continuation is gone"
    assert calls_to(quarter, "Main#half") == 0


def test_a_lifted_loop_is_monomorphic_and_the_flow_residue_is_not_its_fault():
    """`question_control.tl` keeps all of its `Flow` constructors. Why, exactly.

    The old answer written here was wrong, and worth recording as wrong: it
    said item 6's specializer left the `bind` polymorphic. Every top-level
    binding in that program is monomorphic. What was polymorphic was a
    *local* one -- the recursive helper `desugar._loop` lifts a loop into --
    generalized over its `Flow`'s `Brk` slot, which is uninhabited by
    construction for every lifted loop, so nothing could ever solve it. Eight
    loops, eight `forall`s, every call site `%loopN[c, Option Int]`: ground in
    the slot that matters and open in the one that cannot be.

    `ast.FunDecl.monomorphic` settles that, and this asserts it: no lifted
    loop is generalized any more.

    It was not the whole cause. The `Flow` traffic is a chain of `bind` and
    `pure` calls, and every one of those is a `CTyApp` over a method's own
    `forall a b` -- which is not polymorphism specialization removes, because
    a method *is* polymorphic in its own variables no matter how ground its
    dictionary is. `opt._Reducer` declines those on purpose ("a binding with
    `binders` is a `CTyLam` and its call sites are type applications, which is
    M14's business and not this one's"), so the bodies that would collapse the
    `Flow` matches are never inlined. That is the next thing to change, and it
    is a change to what this pass will instantiate rather than to what the
    lifting produces.
    """
    from pathlib import Path
    from turkey.core import CLetRec

    root = Path(__file__).parent / "programs"
    checked = check((root / "question_control.tl").read_text(),
                    str(root / "question_control.tl"), [root])
    lifted = [b for bind in checked.core.binds
              for n in nodes(bind.value) if isinstance(n, CLetRec)
              for b in n.binds if b.name.startswith("%loop")]
    assert lifted, "the fixture should still lift its loops"
    for bind in lifted:
        assert not bind.binders, (
            f"{bind.name} is generalized again; its `Brk` slot is dead by "
            f"construction, so the quantifier can never be solved")

    generic = sum(
        1 for bind in checked.opt.binds for n in nodes(bind.value)
        if isinstance(n, CApp) and isinstance(n.fn, CTyApp))
    assert generic > 0, (
        "if this is zero, inlining now reaches through a type application and "
        "the Flow traffic should be collapsing -- check, and rewrite this test")
