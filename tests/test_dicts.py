"""Dictionary passing: the programs it makes run.

`dicts.gob` is the golden that runs; this file is the rest of the programs that
only run if dictionaries are passed right -- a method known only by its result
type, a default body per instance, a recursive type that must not build
dictionaries forever. The *shape* of the evidence -- a superclass selected
rather than a second dictionary passed -- is `dicts.core`'s to pin.
"""
from __future__ import annotations

import pytest

from tests.lang import check, execute as run
from tests.lang import CompileError

SHOW = """
class Display a {
    fun display(a) -> String
}

instance Display Int {
    fun display(n) = Int.toString(n)
}

instance Display Bool {
    fun display(b) = if b { "true" } else { "false" }
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
"""

ORD = """
class Egal a {
    fun egal(a, a) -> Bool
}

class Rank a : Egal a {
    fun underEq(a, a) -> Bool
}

instance Egal Int {
    fun egal(x, y) = x == y
}

instance Rank Int {
    fun underEq(x, y) = x <= y
}
"""


def output(src: str, capsys) -> list[str]:
    run(src)
    return capsys.readouterr().out.splitlines()


def fails(src: str) -> str:
    with pytest.raises(CompileError) as exc:
        check(src)
    return exc.value.message


# -- what the evidence is -----------------------------------------------------


# -- what abstracts over it ---------------------------------------------------


# -- what it makes run --------------------------------------------------------


def test_two_instances_of_one_class_dispatch_apart(capsys):
    src = SHOW + """
fun main() {
    print(display(1))
    print(display(True))
}
"""
    assert output(src, capsys) == ["1", "true"]


def test_a_method_known_only_by_its_result_type(capsys):
    """`empty()` has nothing at the call site to dispatch on but the type.

    It is the exit criterion of this milestone, and the reason evidence is
    passed rather than recovered from an argument.
    """
    src = """
class Default a {
    fun default() -> a
}

instance Default Int {
    fun default() = 7
}

instance Default String {
    fun default() = "-"
}

fun main() {
    print(Int.toString(default()))
    -- `print` is itself constrained now, so the result type has to be said
    -- somewhere; the point is that nothing at the call site says it.
    let s : String = default()
    print(s)
}
"""
    assert output(src, capsys) == ["7", "-"]


def test_a_default_body_runs_against_each_instance(capsys):
    """One elaboration, many instances: the class's dictionary is rebound."""
    src = """
class Twice a {
    fun join(a, a) -> a
    fun twice(x : a) -> a = join(x, x)
}

instance Twice Int {
    fun join(x, y) = x + y
}

instance Twice String {
    fun join(x, y) = x + y
}

fun main() {
    print(Int.toString(twice(21)))
    print(twice("ab"))
}
"""
    assert output(src, capsys) == ["42", "abab"]


def test_a_method_with_its_own_context_takes_it_per_call(capsys):
    """`foldMap[Monoid m]`: the class dictionary is selected, `Monoid m` passed."""
    src = SHOW + """
class Foldable t {
    fun each[Display a](t a) -> String
}

instance Foldable Array {
    fun each(xs) {
        var s = ""
        for x in xs {
            s = s + display(x)
        }
        return s
    }
}

fun main() {
    print(each([1, 2, 3]))
    print(each([True, False]))
}
"""
    assert output(src, capsys) == ["123", "truefalse"]


def test_a_recursive_type_does_not_build_dictionaries_forever(capsys):
    """`Display (Array Rose)` needs `Display Rose`, which needs it back.

    The dictionary is registered before its methods are built, so the cycle
    closes on the object already under construction instead of descending
    again.
    """
    src = SHOW + """
type Rose = Leaf(Int) | Node(Array Rose)

instance Display Rose {
    fun display(t) = match t {
        Leaf(n) -> display(n)
        Node(kids) -> "(" + display(kids) + ")"
    }
}

fun main() {
    print(display(Node([Leaf(1), Node([Leaf(2)]), Leaf(3)])))
}
"""
    assert output(src, capsys) == ["(1(2)3)"]


def test_a_let_bound_to_a_method_takes_its_dictionary(capsys):
    """The dictionaries have to arrive before there is a function at all."""
    src = SHOW + """
fun main() {
    let s = display
    print(s(1))
    print(s(True))
}
"""
    assert output(src, capsys) == ["1", "true"]


def test_a_local_function_may_have_its_own_context(capsys):
    src = SHOW + """
fun outer[Display a](x : a) -> String {
    fun inner(y) = display(y) + display(y)
    return inner(x)
}

fun main() {
    print(outer(3))
}
"""
    assert output(src, capsys) == ["33"]


def test_a_superclass_method_runs_through_the_selection(capsys):
    src = ORD + """
fun same[Rank a](x : a, y : a) -> Bool = egal(x, y)

fun main() {
    if same(2, 2) { print("yes") } else { print("no") }
}
"""
    assert output(src, capsys) == ["yes"]


def test_an_empty_instance_takes_every_default(capsys):
    src = """
class Greet a {
    fun name(a) -> String
    fun hello(x : a) -> String = "hi " + name(x)
}

instance Greet Int {
    fun name(n) = Int.toString(n)
}

fun main() {
    print(hello(5))
}
"""
    assert output(src, capsys) == ["hi 5"]


def test_a_missing_instance_is_still_reported_before_any_of_this():
    """Elaboration never reports a missing instance: solving already did.

    The two would otherwise disagree about which one speaks, and the message
    that names the type is the one worth keeping.
    """
    assert "no instance for 'Display Char'" in fails(
        SHOW + "fun main() { print(display('c')) }"
    )
