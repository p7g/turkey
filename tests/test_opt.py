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
    CApp, CBind, CCon, CExpr, CJoin, CLit, CMatch, CPrim, CProgram, CTyApp,
    CVar,
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


def test_an_inlined_body_blames_the_call_site_but_arguments_keep_theirs():
    checked = check(
        "fun bump(x : Int) -> Int = x + 1\n"
        "fun main() { print(bump(41)) }\n",
        "inline_test.tl",
    )
    literals = {n.value: n.span for n in nodes(
        named(checked.opt, "Main#main").value) if isinstance(n, CLit)}
    # `1` came from the callee, so any error in the copied residual points at
    # `bump(41)`.  The caller-supplied `41` retains its more precise location.
    assert (literals[1].line, literals[1].col) == (2, 20)
    assert (literals[41].line, literals[41].col) == (2, 25)


def test_a_large_function_is_inlined_only_when_the_call_site_makes_it_small(
        capsys):
    """The large-inline cost model measures the specialized residual.

    The generic body is deliberately over the ordinary limit because its
    ``None`` arm performs fifteen writes. At ``Some(7)`` that cold arm vanishes,
    leaving a tiny residual worth admitting; at an unknown argument the call
    remains, so the policy cannot become a disguised higher blanket limit.
    """
    from turkey.driver import run

    cold = "\n".join(f'        print("cold {i}")' for i in range(15))
    src = f"""
fun choose(o : Option Int) -> Int = match o {{
    Some(x) -> x
    None -> {{
{cold}
        0
    }}
}}
fun mystery(n : Int) -> Option Int =
    if n == 0 {{ None }} else {{ mystery(n - 1) }}
fun main() {{
    print(choose(Some(7)))
    print(choose(mystery(0)))
}}
"""
    program = optimized(src)
    assert calls_to(named(program, "Main#main"), "Main#choose") == 1

    run(src)
    expected = "7\n" + "".join(f"cold {i}\n" for i in range(15)) + "0\n"
    assert capsys.readouterr().out == expected


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


def test_a_pattern_does_not_capture_a_free_name_in_the_scrutinee():
    """A pattern scopes over its arm, not over the match scrutinee.

    The old capture guard treated both as one scope and blocked the
    producer/consumer rewrite whenever an outer name was reused by a pattern.
    That is the ordinary spelling emitted by an inlined `bind`.
    """
    program = optimized("""
fun classify(x : Int) -> Int {
    let o = if x > 0 { Some(x) } else { None }
    match o { Some(x) -> x; None -> 0 }
}
fun main() { print(classify(3)) }
""")
    assert matches(named(program, "Main#classify")) == 0


def test_a_known_constructor_specializes_a_shared_join_by_tag():
    """The `Flow` tag reaches the continuation without copying it per edge.

    `clamp` creates both `Ret` and `Fall` edges to the continuation produced by
    `bind`. Join discovery makes that continuation explicit; the second local
    reduction specializes its shared match by tag. The surviving Fall variant
    is still a join, but its body no longer scrutinizes the incoming Flow.
    """
    program = optimized("""
fun clamp(o : Option Int) -> Option Int {
    let x = o?
    if x > 3 { return None }
    Some(x * 2)
}
fun main() { print(clamp(Some(2))) }
""")
    clamp = named(program, "Main#clamp")
    joins = [n for n in nodes(clamp.value)
             if isinstance(n, CJoin) and not n.recursive]
    assert joins, "the shared Fall continuation should remain a join"
    assert all(not any(isinstance(n, CMatch) for n in nodes(join.body))
               for join in joins), (
        "a specialized join body should already know its incoming Flow tag")


def test_a_recursive_join_result_is_consumed_inside_the_loop(capsys):
    """A recursive join is a loop breaker, not a barrier to its result match.

    The outer match is fused into the join's real exits while the back edge
    remains a jump.  Twenty thousand iterations pins both the result and the
    fact that the transformation did not turn the loop into Python recursion.
    """
    from turkey.driver import run

    src = """
fun finish(o : Option Int) -> Option Int {
    var i = 0
    while i < 20000 {
        let n = o?
        i = i + 1
    }
    Some(i)
}
fun main() { print(finish(Some(1))) }
"""
    program = optimized(src)
    finish = named(program, "Main#finish")
    recursive = [n for n in nodes(finish.value)
                 if isinstance(n, CJoin) and n.recursive]
    assert recursive
    assert all("Flow" not in str(n.ty) for n in recursive)

    run(src)
    assert capsys.readouterr().out == "Some(20000)\n"


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


def test_type_applied_methods_inline_and_the_remaining_flow_is_tracked():
    """The first obstruction in `question_control.tl`, and what lies past it.

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

    The `Flow` traffic is a chain of type-applied `bind` and `pure` methods.
    M16c instantiates the small Option and Either method bodies in place,
    including at the non-ground phantom `Brk` type above: local type beta is
    not monomorphization and needs no groundness restriction. M16e fuses the
    recursive loop result with its consumer. M16f's call-site cost model still
    leaves Array's `bind`: although its reduced body is within the speculative
    ceiling, none of its specialized residuals shrinks under the ordinary
    limit. That is a measured refusal, rather than a reason to make the blanket
    limit larger.

    No residual at all now, where this asserted eight. Two things moved it, and
    both were the same bug seen from different ends: a ground call site that
    monomorphization could not see was ground.

    First, `_Rewriter.value` had no `CBind` case, so a `CBind` fell through to
    `return v`. The one field holding one is `CLetRec.binds`, reached whenever
    `_rw_CLetRec` finds no polymorphic member and defers to `generic` -- which
    is every lifted loop, since a loop body is not generalized. Every loop body
    in every program went through the pass untouched.

    Second, `desugar._unflow` left the `b` of `Flow a b r` open. Nothing
    constructs a `Brk` at a do-context boundary, so no constraint ever reached
    that slot, it generalized, and every call site downstream read
    `bind[Int, Flow(.., ?b)]` -- ground in the slot that matters and open in the
    one that cannot be. `mono` specializes only at ground instantiations, so one
    uninhabited slot kept `bind` generic. Annotating the arm's payload `Unit`
    closes it.

    The size this costs, measured on this fixture: opt bindings 112 -> 158,
    emitted IR 52208 -> 57679 lines (+10.5%), warm backend compile 889 -> 983ms
    (+10.6%). `monads.tl` and `dicts.tl` are unchanged on all three. That is the
    trade this milestone is making on purpose: a generic body reached through a
    generic dictionary is compiled at `BOXED` and read through runtime layout
    checks, and removing it is what lets field access become a load.

    The `Flow` count below is unmoved at 27 by the second change and was taken
    there by the first, which is the direction that wants no defending: a loop
    body whose `bind` and `pure` are specialized has six fewer
    `Fall`/`Brk`/`Cont`/`Ret` records to allocate.
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

    typed = [n.fn.fn.name
             for bind in checked.opt.binds for n in nodes(bind.value)
             if isinstance(n, CApp) and isinstance(n.fn, CTyApp)
             and isinstance(n.fn.fn, CVar)]
    assert "%inst.Std.Classes#Monad.Data.Option.Type#Option#bind" not in typed
    assert "%inst.Std.Classes#Applicative.Data.Option.Type#Option#pure" not in typed
    assert "%inst.Std.Classes#Monad.Data.Either#Either@String#bind" not in typed
    assert "%inst.Std.Classes#Applicative.Data.Either#Either@String#pure" not in typed
    assert typed.count(
        "%inst.Std.Classes#Monad.Data.Array#Array#bind") == 0, (
        "Array bind should have no type-applied residual left; if this moves, "
        "check both generated-code size and warm backend time")

    flow = sum(
        1 for bind in checked.opt.binds for n in nodes(bind.value)
        if isinstance(n, CCon)
        and n.name in {"Prelude#Fall", "Prelude#Brk",
                       "Prelude#Cont", "Prelude#Ret"})
    assert flow == 27, (
        "M16e must retain the nine-construction reduction from fusing recursive "
        "loop result boundaries")


def test_a_for_loop_does_not_build_the_option_its_iterator_returns():
    """The `Some` per element, and the `None` per loop, are both reduced away.

    `Iterator.next` returns an `Option`, so a `for` loop allocated one object
    per element for a value the loop takes apart on the very next line. It
    survived because inlining `next` cost more than the ordinary budget, and
    because the constructor is not the inlined body's result but the argument
    of a `jump` inside the join its early `return` left behind.

    Two rules together: the scrutinee discount pays for the inline, and
    `case_of_join` moves the match next to those jumps for `specialize_join`
    and case-of-known-constructor to finish. What is asserted is the outcome
    the loop is for -- no `Option` is built anywhere in it.
    """
    program = optimized("""
fun total(xs : Array Int) -> Int {
    var sum = 0
    for x in xs { sum = sum + x }
    sum
}
fun main() { print(total([1, 2, 3])) }
""")
    total = named(program, "Main#total")
    built = [n for n in nodes(total.value)
             if isinstance(n, CCon) and n.name.startswith("Data.Option.Type#")]
    assert not built, f"the loop still builds {[n.name for n in built]}"
    calls = [n for n in nodes(total.value)
             if isinstance(n, CApp) and isinstance(n.fn, CVar)
             and "#next" in n.fn.name]
    assert not calls, "the iterator step should have been inlined"
