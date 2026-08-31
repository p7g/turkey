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
* that a program monomorphization cannot finish still compiles, still runs, and
  says so.
"""

from __future__ import annotations

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

instance [Semigroup a] Semigroup (Array a) {
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
                 "otherwise", "scrutinee", "seq", "iter_fn", "next_fn",
                 "init", "step"):
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
    # And the generic survives, because nothing in this pass deletes anything.
    assert "Main#twice" in made


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
    built = named(checked.mono, "%inst.Semigroup.Array@Int")
    assert isinstance(built.value, CRecord)
    assert built.binders == []
    # Nothing is applying the generic instance any more.
    for bind in checked.mono.dicts + checked.mono.binds:
        if bind.binders:
            continue  # a generic binding is carried through untouched
        for node in walk(bind.value):
            if isinstance(node, CApp) and isinstance(node.fn, CTyApp):
                assert not (isinstance(node.fn.fn, CVar)
                            and node.fn.fn.name == "%inst.Semigroup.Array"), (
                    f"'{bind.name}' still builds a dictionary per request")


def test_a_recursive_instance_names_itself():
    """`Display (Array Rose)` needs `Display Rose`, which needs it back.

    The collapse has to close the cycle on the binding it is in the middle of
    making, rather than asking for another one -- which is what recording the
    name before building the body is for.
    """
    checked = check("""
class Display a {
    fun display(a) -> String
}

instance Display Int {
    fun display(n) = Int.toString(n)
}

instance [Display a] Display (Array a) {
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
""")
    built = named(checked.mono, "%inst.Display.Array@Rose")
    assert isinstance(built.value, CRecord)
    inner = [n.name for n in walk(built.value) if isinstance(n, CVar)]
    assert "%inst.Display.Array@Rose" in inner, "the cycle did not close"
    # Named by the *resolved* constructor -- `Main#Rose` -- where the copy's
    # own name carries the bare one, since `_mangle` is for reading.
    assert "%inst.Display.Main#Rose" in inner, "the element dictionary went missing"


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


def test_giving_up_is_said_out_loud():
    checked = check(POLYREC)
    assert any("depth" in w and "left polymorphic" in w
               for w in checked.warnings), checked.warnings


def test_the_capped_program_still_runs(capsys):
    from turkey.driver import run
    run(POLYREC)
    assert capsys.readouterr().out.splitlines() == ["3"]
