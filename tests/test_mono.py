"""Monomorphization: what it specializes, and where it gives up (M14a).

`driver.check` runs the pass and then runs `coretc` over its output on every
compile, so every golden in the suite already asserts that specializing a
program leaves it well typed. That is the broad half and it is covered
elsewhere.

What is left for this file is the half a golden cannot see:

* that a *use* became a copy, and the copy's dictionary is a name rather than
  an application to be performed;
* that the per-request dictionary rebuild `plan.txt` item 6 names is actually
  gone, rather than merely renamed;
* that a program monomorphization cannot finish still compiles and still runs,
  and that it does so *silently* -- the cap is a budget, not a diagnostic.
"""

from __future__ import annotations

from pathlib import Path

from turkey import core
from turkey.core import CApp, CBind, CProgram, CRecord, CTyApp, CVar
from turkey.driver import check
from turkey.mono import MAX_SPECIALIZATIONS

# A class with a context'd instance, which is the shape the collapse is about:
# `Semigroup (Array a)` is built *from* a `Semigroup a`, so before this pass
# every mention of it is a call.
SEMI = """
class Semigroup a {
    fun combine(a, a) -> a
}

instance Semigroup Int {
    fun combine(x, y) = x + y
}

instance Semigroup String {
    fun combine(x, y) = x + y
}

instance Semigroup (Array a) : Semigroup a {
    fun combine(xs, ys) = xs
}

fun twice[Semigroup a](x : a) -> a = combine(x, x)
"""


def named(program: CProgram, name: str) -> CBind:
    for bind in program.dicts + program.binds:
        if bind.name.endswith(name):
            return bind
    raise AssertionError(f"no binding named '{name}'")


def names(program: CProgram) -> list[str]:
    return [b.name for b in program.dicts + program.binds]


def walk(e):
    """Every node under one. The same shape `tests/test_core.py` walks with."""
    if e is None:
        return
    yield e
    for attr in ("fn", "body", "value", "target", "index", "cond", "then",
                 "otherwise", "scrutinee", "rest"):
        yield from walk(getattr(e, attr, None))
    for attr in ("args", "elems", "params"):
        for item in getattr(e, attr, []) or []:
            if isinstance(item, core.CExpr):
                yield from walk(item)
    for _, value in getattr(e, "fields", []) or []:
        yield from walk(value)
    for alt in getattr(e, "alts", []) or []:
        yield from walk(alt.body)
    for bind in getattr(e, "binds", []) or []:
        yield from walk(bind.value)


def nodes(program: CProgram, name: str):
    return list(walk(named(program, name).value))


# -- what a use site becomes -------------------------------------------------


def test_a_use_at_two_types_becomes_two_copies():
    checked = check(SEMI + """
fun main() {
    print(Int.toString(twice(2)))
    print(twice("a"))
}
""")
    made = names(checked.mono)
    assert "Main#twice@Int" in made
    assert "Main#twice@String" in made
    # And the generic is gone, because after M14c nothing reaches it. It is
    # kept when something does: see the capped call site, further down.
    assert "Main#twice" not in made


def test_a_copy_takes_no_type_application():
    checked = check(SEMI + """
fun main() { print(Int.toString(twice(2))) }
""")
    body = nodes(checked.mono, "Main#main")
    applied = [n for n in body if isinstance(n, CTyApp)
               and isinstance(n.fn, CVar) and n.fn.name.startswith("Main#")]
    assert applied == [], "a named binding is still being type-applied"
    called = [n.name for n in body if isinstance(n, CVar)]
    assert "Main#twice@Int" in called


def test_the_same_type_twice_is_one_copy():
    checked = check(SEMI + """
fun main() {
    print(Int.toString(twice(2)))
    print(Int.toString(twice(3)))
}
""")
    assert [n for n in names(checked.mono) if n == "Main#twice@Int"] == \
        ["Main#twice@Int"]


# -- the per-request dictionary rebuild --------------------------------------


def test_a_contexted_instance_becomes_a_record_not_a_call():
    """`plan.txt` item 6's second deliverable, asserted directly.

    `%inst.Semigroup.Array[Int](%inst.Semigroup.Int)` allocates a record every
    time it is evaluated. Afterwards there is a binding whose *value* is the
    record, and the use site is its name.
    """
    checked = check(SEMI + """
fun main() { print(Int.toString(twice([1, 2])[0])) }
""")
    built = named(checked.mono, "%inst.Main#Semigroup.Data.Array#Array@Int")
    assert isinstance(built.value, CRecord)
    assert built.binders == []
    # Nothing is applying the generic instance any more.
    for bind in checked.mono.dicts + checked.mono.binds:
        if bind.binders:
            continue  # a generic binding is carried through untouched
        for node in walk(bind.value):
            if isinstance(node, CApp) and isinstance(node.fn, CTyApp):
                assert not (isinstance(node.fn.fn, CVar)
                            and node.fn.fn.name ==
                            "%inst.Main#Semigroup.Data.Array#Array"), (
                    f"'{bind.name}' still builds a dictionary per request")


# `Display (Array Rose)` needs `Display Rose`, which needs it back: the two
# ground dictionaries are a cycle. Used both for what the pass builds and,
# below, for whether the evaluator can then build it.
RECURSIVE_INSTANCE = """
class Display a {
    fun display(a) -> String
}

instance Display Int {
    fun display(n) = Int.toString(n)
}

instance Display (Array a) : Display a {
    fun display(xs) {
        var s = ""
        for x in xs {
            s = s + display(x)
        }
        return s
    }
}

type Rose = Leaf(Int) | Node(Array Rose)

instance Display Rose {
    fun display(t) = match t {
        Leaf(n) -> display(n)
        Node(kids) -> "(" + display(kids) + ")"
    }
}

fun main() { print(display(Node([Leaf(1), Node([Leaf(2)]), Leaf(3)]))) }
"""


def test_a_recursive_instance_names_itself():
    """`Display (Array Rose)` needs `Display Rose`, which needs it back.

    The collapse has to close the cycle on the binding it is in the middle of
    making, rather than asking for another one -- which is what recording the
    name before building the body is for.
    """
    checked = check(RECURSIVE_INSTANCE)
    built = named(
        checked.mono, "%inst.Main#Display.Data.Array#Array@Rose")
    assert isinstance(built.value, CRecord)
    inner = [n.name for n in walk(built.value) if isinstance(n, CVar)]
    assert "%inst.Main#Display.Data.Array#Array@Rose" in inner, \
        "the cycle did not close"
    # Named by the *resolved* constructor -- `Main#Rose` -- where the copy's
    # own name carries the bare one, since `_mangle` is for reading. And named
    # through the method M14c hoisted out of it rather than through a
    # projection, which is that milestone's whole claim.
    assert "%inst.Main#Display.Main#Rose#display" in inner, \
        "the element method went missing"


def test_a_dictionary_argument_that_is_not_a_top_level_name_is_left_alone():
    """A local `let` bound to a dictionary is not something the collapse may
    hand to a top-level binding: the name is not in scope out there.

    The lowering writes exactly such a `let` -- an instance method opens with
    its own dictionary bound locally -- so this is the case above from the
    other side, and it is why the collapse resolves aliases instead of
    trusting that a `CVar` is a global.
    """
    checked = check(SEMI + """
fun main() { print(Int.toString(twice([1, 2])[0])) }
""")
    # Whatever the collapse did, every dictionary a ground binding hands to a
    # top-level record must itself be a top-level name.
    tops = {b.name for b in checked.mono.dicts}
    for bind in checked.mono.dicts:
        if bind.binders or not isinstance(bind.value, CRecord):
            continue
        for node in walk(bind.value):
            if isinstance(node, CVar) and node.name.startswith("%inst.") \
                    and "@" in node.name:
                assert node.name in tops


# -- where it gives up -------------------------------------------------------

POLYREC = """
type Pair a = Pair(a, a)

fun depth(x : a, n : Int) -> Int {
    if n <= 0 { return 0 }
    return 1 + depth(Pair(x, x), n - 1)
}

fun main() { print(Int.toString(depth(1, 3))) }
"""



# Polymorphic recursion with a dictionary in it: the cap refuses to copy
# `grow`, so the generic binding survives *and* still has a parameter to
# project its `combine` out of.
CAPPED_DICT = """
type Pair a = Pair(a, a)

class Semigroup a { fun combine(a, a) -> a }

instance Semigroup Int { fun combine(x, y) = x + y }

instance Semigroup (Pair a) : Semigroup a {
    fun combine(x, y) = x
}

fun grow[Semigroup a](x : a, n : Int) -> a {
    if n <= 0 { return combine(x, x) }
    grow(Pair(x, x), n - 1)
    return combine(x, x)
}

fun main() { print(Int.toString(grow(1, 3))) }
"""

def test_polymorphic_recursion_terminates_at_the_cap():
    checked = check(POLYREC)
    copies = [n for n in names(checked.mono) if n.startswith("Main#depth@")]
    assert len(copies) == MAX_SPECIALIZATIONS


def test_the_capped_call_site_still_names_the_generic_binding():
    """The whole point of the cap: what it refuses is a *copy*, not the call.

    So the generic binding has to survive the pass, and the deepest copy has to
    go on type-applying it exactly as it did before.
    """
    checked = check(POLYREC)
    assert "Main#depth" in names(checked.mono)
    copies = [b for b in checked.mono.binds
              if b.name.startswith("Main#depth@")]
    deepest = max(copies, key=lambda b: len(b.name))
    applied = [n for n in walk(deepest.value)
               if isinstance(n, CTyApp) and isinstance(n.fn, CVar)]
    assert [n.fn.name for n in applied] == ["Main#depth"]


def test_giving_up_is_silent():
    """The cap is a budget, not a diagnostic.

    Turkey does not promise that a program is monomorphized -- layout-keyed
    sharing is the fallback, and how much of it a backend recovers is the
    backend's business. A program that stays polymorphic is not a program with
    something wrong with it, so there is nothing to say about it.
    """
    checked = check(POLYREC)
    assert not any("polymorphic" in w for w in checked.warnings), \
        checked.warnings


def test_the_capped_program_still_runs(capsys):
    from turkey.driver import run
    run(POLYREC)
    assert capsys.readouterr().out.splitlines() == ["3"]


# -- the specialized program is the one that runs (M14b) ---------------------
#
# `driver.run` evaluates `checked.mono`, so every golden in `tests/programs`
# is already a differential test of the pass: same source, same `.expected`,
# a different Core underneath. The two cases below are the ones the plan
# singled out to confirm rather than assume, because neither is visible in a
# golden's output when it goes wrong -- one crashes, the other silently
# prints a smaller number.


def test_a_recursive_instance_closes_its_cycle_at_run_time(capsys):
    """The structural test above says the binding names itself. This says the
    evaluator can then build it.

    A ground self-referential dictionary is a cycle among `program.dicts`, and
    what resolves it is `Evaluator.run`'s two-pass loop: bind every dictionary
    to an empty record first, fill the fields second. Before specialization
    the cycle was not there to close -- the recursive mention was an
    application to be performed later.
    """
    from turkey.driver import run

    run(RECURSIVE_INSTANCE)
    assert capsys.readouterr().out.splitlines() == ["(1(2)3)"]


def test_specializing_a_binding_does_not_duplicate_a_cell(capsys):
    """Copying a binding must copy code, never state.

    It holds because only a *generalized* binding is copied and, under the
    value restriction (design.md 4.4), a generalized right-hand side is a
    syntactic value -- so a binding that owns a mutable cell is monomorphic
    and is carried through as itself. `counter` below is reached from two
    specializations of `bump`; if each copy had got its own, this prints 1.
    """
    from turkey.driver import run

    run(SEMI + """
type Cell = Cell { n : Int }

let counter = Cell { n = 0 }

fun bump[Semigroup a](x : a) -> a {
    counter.n = counter.n + 1
    combine(x, x)
}

fun main() {
    bump(1)
    bump("a")
    print(Int.toString(counter.n))
}
""")
    assert capsys.readouterr().out.splitlines() == ["2"]


# -- a known dictionary's method is a name (M14c) ----------------------------
#
# After M14a a method selection is a projection out of a record whose
# definition is right there, and then an indirect call. Coherence says the
# field's value is decided, so the projection has a name: these are the tests
# that it got one. None of it shows in a golden's output -- the same program
# runs, through one less indirection.


def dictionaries(program: CProgram) -> list[str]:
    return [b.name for b in program.dicts if isinstance(b.value, CRecord)]


def test_a_method_of_a_known_dictionary_becomes_a_binding():
    checked = check(SEMI + """
fun main() { print(Int.toString(twice(2))) }
""")
    assert "%inst.Main#Semigroup.Int#combine" in names(checked.mono)
    body = nodes(checked.mono, "Main#twice@Int")
    assert not [n for n in body if isinstance(n, core.CField)], \
        "a projection survived"
    called = [n.fn.name for n in body
              if isinstance(n, CApp) and isinstance(n.fn, CVar)]
    assert "%inst.Main#Semigroup.Int#combine" in called


def test_a_superclass_chain_collapses_in_one_step():
    """`d.%super.Semigroup.combine` is two projections, and the inner one
    names a top-level dictionary already -- so it is followed rather than
    hoisted, and the pair becomes the one name the outer one resolves to."""
    checked = check("""
class Semigroup a { fun combine(a, a) -> a }
class Monoid a : Semigroup a { fun empty() -> a }
instance Semigroup Int { fun combine(x, y) = x + y }
instance Monoid Int { fun empty() = 0 }
fun squash[Monoid a](xs : Array a) -> a {
    var acc = empty()
    for x in xs { acc = combine(acc, x) }
    return acc
}
fun main() { print(Int.toString(squash([1, 2, 3]))) }
""")
    body = nodes(checked.mono, "Main#squash@Int")
    called = {n.fn.name for n in body
              if isinstance(n, CApp) and isinstance(n.fn, CVar)}
    assert "%inst.Main#Semigroup.Int#combine" in called, sorted(called)
    assert "%inst.Main#Monoid.Int#empty" in called, sorted(called)


def test_a_for_loop_stops_projecting():
    """A `for` loop's `iter` and `next` are ordinary terms, so they are
    rewritten by the same rule as everything else -- which is where every
    `for` loop in the suite was paying a projection.

    They are ordinary applications by the time this sees them: `lower.py`
    turns the loop into a join point and its cursor into two calls and a
    match, so what used to be two fields of a `CForIn` are now two `CApp`s
    like any other. The claim is the same one and it survived the node going
    away, which is the point of asserting it here rather than reading a
    golden.
    """
    checked = check("""
fun main() {
    var total = 0
    for x in [1, 2, 3] { total = total + x }
    print(Int.toString(total))
}
""")
    called = {n.fn.name for n in nodes(checked.mono, "Main#main")
              if isinstance(n, CApp) and isinstance(n.fn, CVar)}
    cursor = {name for name in called if name.endswith(("#iter", "#next"))}
    assert cursor, sorted(called)
    for name in cursor:
        assert "%inst.Std.Classes#Iterator.Data.Array#Array" in name, name


def test_a_dictionary_parameter_is_still_projected_from():
    """The rewrite is about dictionaries this pass can *see*.

    A binding the cap left generic still takes its dictionary as a parameter
    and still projects out of it. That is not a shortcoming: it is what makes
    the capped call site work, and a projection off a parameter is exactly the
    thing coherence says nothing about.
    """
    checked = check(CAPPED_DICT)
    generic = named(checked.mono, "Main#grow")
    assert generic.binders, "the generic binding did not survive"
    assert [n for n in walk(generic.value) if isinstance(n, core.CField)], \
        "a generic body has nothing to project from but its parameter"


# -- and what nothing reaches is dropped (M14c) ------------------------------


def test_a_dictionary_nothing_reaches_is_dropped():
    """The Prelude declares instances a given program never mentions. Before
    this they were all emitted; the specialized program carries the ones it
    names and no others."""
    checked = check(SEMI + """
fun main() { print(twice("a")) }
""")
    kept = dictionaries(checked.mono)
    before = dictionaries(checked.core)
    assert "%inst.Main#Semigroup.String" in kept
    assert "%inst.Main#Semigroup.Int" not in kept
    assert "%inst.Main#Semigroup.Int" in before, \
        "not a claim about the lowering"
    assert len(kept) < len(before)


def test_a_binding_that_could_have_run_is_kept(capsys):
    """Only a binding whose value is a *value* may be dropped, because a
    top-level binding is evaluated for its own sake before `main` is called.
    `noisy` below is reached by nothing and must still print."""
    from turkey.driver import run

    run("""
fun shout() -> Int {
    print("hi")
    return 1
}

let noisy = shout()

fun main() { print("bye") }
""")
    assert capsys.readouterr().out.splitlines() == ["hi", "bye"]


def test_the_specialized_program_is_smaller_than_the_one_it_came_from():
    checked = check(SEMI + """
fun main() { print(Int.toString(twice(2))) }
""")
    assert len(names(checked.mono)) < len(names(checked.core))


# -- and it goes round twice, on one budget (M14d) ---------------------------

# A method with a context of its own. `squash` needs a `Monoid a` that its
# *class* does not supply, so `%inst.Container.Array`'s copy of it is a lambda
# over a dictionary -- and hoisting that lambda out of the record is what gives
# the second round something the first could not have seen.
MULTIROUND = """
class Monoid a {
    fun empty() -> a
    fun join(a, a) -> a
}

instance Monoid String {
    fun empty() = ""
    fun join(x, y) = x + y
}

class Container t {
    fun squash[Monoid a](t a) -> a
}

instance Container Array {
    fun squash(xs) {
        var acc = empty()
        for x in xs { acc = join(acc, x) }
        return acc
    }
}

fun main() {
    let xs = ["a", "b"]
    print(squash(xs))
}
"""


def test_a_second_round_specializes_what_the_first_round_made():
    """The claim the milestone exists for.

    `%inst.Container.Array#squash` is a binding the *devirtualizer* created, so
    the specializer had already run when it appeared. Its one call site is at
    ground types with ground evidence, which is exactly the shape the collapse
    handles -- and a second round is what lets the collapse see it.
    """
    checked = check(MULTIROUND)
    made = names(checked.mono)
    hoisted = [n for n in made if n.startswith(
        "%inst.Main#Container.Data.Array#Array#squash")]
    assert hoisted, "the method was never hoisted at all"
    assert any("@" in n for n in hoisted), \
        f"the hoisted method was never specialized: {hoisted}"


def test_the_specialized_method_takes_no_dictionary():
    """And the point of specializing it: the `Monoid` argument is gone, so
    every `join` inside is a direct call to a name."""
    checked = check(MULTIROUND)
    bind = named(
        checked.mono, "%inst.Main#Container.Data.Array#Array#squash@String")
    assert not bind.binders, "a specialized binding has no type binders"
    # The type is the claim: `fun(Array String) -> String` and not
    # `fun(%Dict.Monoid String) -> fun(Array String) -> String`.
    assert "%Dict." not in str(bind.ty), bind.ty
    assert not [n for n in walk(bind.value) if isinstance(n, core.CField)], \
        "the body still projects out of a dictionary"


def test_a_second_round_reuses_the_first_rounds_copies():
    """Round two re-asks for specializations round one already built, because
    it is walking bodies round one wrote. The memo on `_State` is what makes
    the answer the existing copy rather than a byte-identical second one under
    a disambiguated name -- which `fresh` would happily have supplied.

    Against `dicts.tl` rather than `MULTIROUND`, because it has to be a program
    big enough for the second round to re-ask at all: with a per-round memo
    this one grows five duplicate copies (`Data.Array#new@Int~2` and friends)
    and `MULTIROUND` grows none. A `~` in a name is `fresh` disambiguating a
    collision, and after this pass there should be nothing to collide with.
    """
    path = Path(__file__).parent / "programs" / "dicts.tl"
    checked = check(path.read_text(), str(path))
    dups = [n for n in names(checked.mono) if "~" in n]
    assert not dups, f"round two duplicated round one's work: {dups}"


def test_a_third_round_changes_nothing():
    """Two is a number, not a fixed point -- but on a real program it is the
    fixed point, and this is that measurement rather than a hope."""
    from turkey import mono

    checked = check(MULTIROUND)
    saved = mono.ROUNDS
    outs = {}
    try:
        for rounds in (2, 3):
            mono.ROUNDS = rounds
            out = mono.monomorphize(checked.core, checked.decls,
                                    checked.classes, checked.main)
            outs[rounds] = sorted(names(out))
    finally:
        mono.ROUNDS = saved
    assert outs[2] == outs[3]


def test_the_budget_survives_the_round_count():
    """`MAX_SPECIALIZATIONS` bounds the *program*, not the round.

    Run the pipeline five times over the polymorphic recursion the cap was
    written for and there are still thirty-two copies of `Main#depth`. That is
    the property `_State.counts` is there to guarantee: a count kept per round
    would let a binding refused its thirty-third copy collect thirty-two more
    in every round after, and `ROUNDS` is a constant someone may raise.

    Stated as a guarantee rather than as a caught bug, honestly: no program in
    the suite re-asks for a *capped* binding in a later round, because a capped
    call site lives in a generic body and generic bodies are never rewritten.
    The sharing that the suite does force is the memo -- see
    `test_a_second_round_reuses_the_first_rounds_copies`.
    """
    from turkey import mono

    saved = mono.ROUNDS
    try:
        mono.ROUNDS = 5
        checked = check(POLYREC)
    finally:
        mono.ROUNDS = saved
    copies = [n for n in names(checked.mono) if n.startswith("Main#depth@")]
    assert len(copies) == MAX_SPECIALIZATIONS, \
        f"{len(copies)} copies over five rounds, not {MAX_SPECIALIZATIONS}"
