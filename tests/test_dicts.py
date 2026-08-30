"""Dictionary passing: the evidence, and the programs it makes run.

`dicts.tl` is the golden that runs; this file is what a golden cannot show --
the *shape* of the evidence that produced the output. The two questions are
separate on purpose: whether a program prints the right thing, and whether it
got there by selecting a superclass rather than by passing a second dictionary.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from turkey import ast
from turkey.driver import check, run
from turkey.errors import TurkeyError
from turkey.evidence import FromDict, FromInstance

SHOW = """
class Show a {
    fun show(a) -> String
}

instance Show Int {
    fun show(n) = Int.toString(n)
}

instance Show Bool {
    fun show(b) = if b { "true" } else { "false" }
}

instance [Show a] Show (Array a) {
    fun show(xs) {
        var s = ""
        for x in xs {
            s = s ++ show(x)
        }
        return s
    }
}
"""

ORD = """
class Eq a {
    fun eq(a, a) -> Bool
}

class Ord a : Eq a {
    fun lte(a, a) -> Bool
}

instance Eq Int {
    fun eq(x, y) = x == y
}

instance Ord Int {
    fun lte(x, y) = x <= y
}
"""


def output(src: str, capsys) -> list[str]:
    run(src)
    return capsys.readouterr().out.splitlines()


def fails(src: str) -> str:
    with pytest.raises(TurkeyError) as exc:
        check(src)
    return exc.value.message


def _decl(checked, name: str) -> ast.FunDecl:
    for item in checked.program.decls:
        if isinstance(item, ast.SFun) and item.decl.name == name:
            return item.decl
    raise AssertionError(f"no top-level fun '{name}'")


def uses(checked, fn: str) -> dict[str, list]:
    """The use sites inside one top-level function, by name, in source order.

    Scoped to a single body because a method's name occurs inside the instance
    that defines it as well as at every call, and the two are not the same
    question.
    """
    found: dict[str, list] = {}
    seen: set[int] = set()

    def walk(node) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not isinstance(node, ast.Node) or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, ast.EVar) and node.use is not None:
            found.setdefault(node.name, []).append(node.use)
        for f in fields(node):
            walk(getattr(node, f.name))

    walk(_decl(checked, fn).body)
    return found


def params_of(checked, name: str) -> list[str]:
    """The dictionary parameters the binding of `name` gained."""
    decl = _decl(checked, name)
    return decl.dicts.params if decl.dicts else []


# -- what the evidence is -----------------------------------------------------


def test_a_known_type_resolves_to_its_instance():
    checked = check(SHOW + "fun main() { print(show(1)) }")
    (evidence,) = uses(checked, "main")["show"][0].evidence
    assert isinstance(evidence, FromInstance)
    assert evidence.inst.con == "Int"
    assert evidence.args == []


def test_an_unknown_type_resolves_to_the_dictionary_in_scope():
    """Inside a constrained function the evidence is a *parameter*, not a choice.

    This is the whole reason resolution waits for solving: at generation time
    `show(x)` looks exactly the same either way.
    """
    checked = check(SHOW + "fun twice[Show a](x : a) -> String = show(x) ++ show(x)")
    first, second = uses(checked, "twice")["show"]
    assert isinstance(first.evidence[0], FromDict)
    # Both occurrences take the same dictionary, and it is the one the
    # declaration abstracted over.
    assert first.evidence[0].name == second.evidence[0].name
    assert params_of(checked, "twice") == [first.evidence[0].name]


def test_an_instance_context_is_applied_to_the_dictionary_it_needs():
    checked = check(SHOW + "fun main() { print(show([1, 2])) }")
    (evidence,) = uses(checked, "main")["show"][0].evidence
    assert isinstance(evidence, FromInstance)
    assert evidence.inst.con == "Array"
    (arg,) = evidence.args
    assert isinstance(arg, FromInstance) and arg.inst.con == "Int"


def test_a_nested_instance_nests_its_evidence():
    checked = check(SHOW + "fun main() { print(show([[1], [2]])) }")
    (outer,) = uses(checked, "main")["show"][0].evidence
    (middle,) = outer.args
    (inner,) = middle.args
    assert [e.inst.con for e in (outer, middle, inner)] == ["Array", "Array", "Int"]


def test_a_superclass_is_selected_rather_than_passed():
    """`[Ord a]` gives `eq` for free -- one dictionary, walked into.

    A `Monoid` dictionary carrying its `Semigroup` is what makes a superclass
    an implication rather than a second obligation on the caller.
    """
    checked = check(ORD + "fun same[Ord a](x : a, y : a) -> Bool = eq(x, y)")
    (evidence,) = uses(checked, "same")["eq"][0].evidence
    assert isinstance(evidence, FromDict)
    assert evidence.path == ("Eq",)
    assert params_of(checked, "same") == [evidence.name]


# -- what abstracts over it ---------------------------------------------------


def test_only_class_predicates_become_parameters():
    """A `HasField` is discharged by a declaration lookup and leaves nothing.

    `OneOf` is the same: both are erased, and only a class predicate survives
    into the running program.
    """
    checked = check("fun get(r) = r.x\nfun add(a) = a + 1")
    assert params_of(checked, "get") == []
    assert params_of(checked, "add") == []


def test_a_function_gains_one_parameter_per_retained_predicate():
    src = SHOW + ORD + """
fun both[Show a, Ord a](x : a, y : a) -> String {
    if lte(x, y) { return show(x) }
    return show(y)
}
"""
    assert len(params_of(check(src), "both")) == 2


def test_an_unconstrained_function_has_no_parameters():
    assert params_of(check("fun identity(x) = x"), "identity") == []


def test_a_mutually_recursive_group_shares_its_context():
    """One member's body may call another's, so the context cannot be per name.

    A per-name split would leave the call inside `even` needing a dictionary
    that `even`'s own signature never promised. Haskell 98 shares a group's
    context for exactly this reason.
    """
    src = SHOW + """
fun even(x, n) {
    if n == 0 { return show(x) }
    return odd(x, n - 1)
}

fun odd(x, n) = even(x, n - 1)
"""
    checked = check(src)
    assert params_of(checked, "even") == params_of(checked, "odd")
    assert len(params_of(checked, "even")) == 1


# -- what it makes run --------------------------------------------------------


def test_two_instances_of_one_class_dispatch_apart(capsys):
    src = SHOW + """
fun main() {
    print(show(1))
    print(show(true))
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
    print(default())
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
    fun join(x, y) = x ++ y
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
    fun each[Show a](t a) -> String
}

instance Foldable Array {
    fun each(xs) {
        var s = ""
        for x in xs {
            s = s ++ show(x)
        }
        return s
    }
}

fun main() {
    print(each([1, 2, 3]))
    print(each([true, false]))
}
"""
    assert output(src, capsys) == ["123", "truefalse"]


def test_a_recursive_type_does_not_build_dictionaries_forever(capsys):
    """`Show (Array Rose)` needs `Show Rose`, which needs it back.

    The dictionary is registered before its methods are built, so the cycle
    closes on the object already under construction instead of descending
    again.
    """
    src = SHOW + """
type Rose = Leaf(Int) | Node(Array Rose)

instance Show Rose {
    fun show(t) = match t {
        Leaf(n) -> show(n)
        Node(kids) -> "(" ++ show(kids) ++ ")"
    }
}

fun main() {
    print(show(Node([Leaf(1), Node([Leaf(2)]), Leaf(3)])))
}
"""
    assert output(src, capsys) == ["(1(2)3)"]


def test_a_let_bound_to_a_method_takes_its_dictionary(capsys):
    """The dictionaries have to arrive before there is a function at all."""
    src = SHOW + """
fun main() {
    let s = show
    print(s(1))
    print(s(true))
}
"""
    assert output(src, capsys) == ["1", "true"]


def test_a_local_function_may_have_its_own_context(capsys):
    src = SHOW + """
fun outer[Show a](x : a) -> String {
    fun inner(y) = show(y) ++ show(y)
    return inner(x)
}

fun main() {
    print(outer(3))
}
"""
    assert output(src, capsys) == ["33"]


def test_a_superclass_method_runs_through_the_selection(capsys):
    src = ORD + """
fun same[Ord a](x : a, y : a) -> Bool = eq(x, y)

fun main() {
    if same(2, 2) { print("yes") } else { print("no") }
}
"""
    assert output(src, capsys) == ["yes"]


def test_an_empty_instance_takes_every_default(capsys):
    src = """
class Greet a {
    fun name(a) -> String
    fun hello(x : a) -> String = "hi " ++ name(x)
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
    assert "no instance for 'Show Char'" in fails(
        SHOW + "fun main() { print(show('c')) }"
    )
