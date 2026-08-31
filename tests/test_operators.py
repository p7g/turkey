"""Operators as class methods, and the prelude that declares them.

`operators.tl` and `iter.tl` are the goldens that run. This file is the part a
golden cannot show: that `+` is *nothing but* a call to `Add.add` -- no table,
no special case, no privilege -- and what follows from that. It also pins the
two boundaries the prelude draws: `Prim.*` is not in the surface language, and
a program may not redefine what the prelude declares.
"""

from __future__ import annotations

import pytest

from turkey import ast
from turkey.driver import check, run
from turkey.errors import TurkeyError, TurkeyPanic
from turkey.evidence import FromDict, FromInstance
from turkey.types import show_scheme

MONEY = """
type Money = Money { cents : Int }

instance Add Money {
    fun add(a, b) = Money { cents = a.cents + b.cents }
}
"""


def output(src: str, capsys) -> list[str]:
    run(src)
    return capsys.readouterr().out.splitlines()


def fails(src: str) -> str:
    with pytest.raises(TurkeyError) as exc:
        check(src)
    return exc.value.message


def scheme(src: str, name: str) -> str:
    checked = check(src)
    return next(show_scheme(s) for n, s in checked.signatures if n == name)



def _short(name: str) -> str:
    """A top-level binding is `Module#name` after resolution (M11a); the tests
    ask for it the way it was written."""
    return name.rpartition("#")[2]

def _uses(checked, fn: str) -> list[ast.EVar]:
    """Every `EVar` inside one top-level function, in source order."""
    from dataclasses import fields

    found: list[ast.EVar] = []
    seen: set[int] = set()

    def walk(node) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not isinstance(node, ast.Node) or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, ast.EVar):
            found.append(node)
        for f in fields(node):
            walk(getattr(node, f.name))

    for item in checked.program.decls:
        if isinstance(item, ast.SFun) and _short(item.decl.name) == fn:
            walk(item.decl.body)
    return found


# -- what an operator is ------------------------------------------------------


def test_an_operator_is_a_use_of_its_method():
    """`+` carries an ordinary `Use`, resolved by the ordinary machinery."""
    checked = check("fun f(x : Int) -> Int = x + 1")
    (use,) = [v.use for v in _uses(checked, "f") if v.name == "add"]
    (evidence,) = use.evidence
    assert isinstance(evidence, FromInstance)
    assert evidence.inst.cls == "Add" and evidence.inst.con == "Int"


def test_an_operator_on_an_open_type_takes_a_dictionary():
    checked = check("fun twice(x) = x + x")
    (use,) = [v.use for v in _uses(checked, "twice") if v.name == "add"]
    assert isinstance(use.evidence[0], FromDict)


def test_addition_generalizes_over_its_class():
    assert scheme("fun twice(x) = x + x", "twice") == "[Add a] fun(a) -> a"


def test_comparison_generalizes_over_ord():
    assert scheme("fun smaller(x, y) = if x < y { x } else { y }",
                  "smaller") == "[Ord a] fun(a, a) -> a"


def test_equality_is_no_longer_int_only():
    assert scheme('fun f() -> Bool = "a" == "b"', "f") == "fun() -> Bool"


def test_unary_minus_is_a_class_too():
    assert scheme("fun flip(x) = -x", "flip") == "[Neg a] fun(a) -> a"


def test_the_operators_are_homogeneous():
    """One class parameter, so no `Add a b`: both operands are one type.

    This is the visible cost of declining MPTCs, and it is stated here rather
    than left to be discovered.
    """
    assert "expected Int, found Float" in fails("fun f(a : Int, b : Float) = a + b")


def test_an_operator_needs_only_an_instance(capsys):
    src = MONEY + """
fun main() {
    let m = Money { cents = 2 } + Money { cents = 3 }
    print(Int.toString(m.cents))
}
"""
    assert output(src, capsys) == ["5"]


def test_a_type_that_adds_need_not_divide():
    """Per-operator classes, as in Rust's `std::ops`, not one omnibus `Num`."""
    assert fails(MONEY + "fun f(a : Money) -> Money = a / a") == \
        "no instance for 'Div Money'"


def test_a_missing_instance_is_reported_as_one():
    assert fails("type P = P(Int)\nfun f(a : P) -> P = a + a") == \
        "no instance for 'Add P'"


def test_the_short_circuiting_operators_are_not_methods():
    """`&&` and `||` cannot be calls: a call evaluates both arguments."""
    assert scheme("fun f(a : Bool) -> Bool = a && error(\"boom\")",
                  "f") == "fun(Bool) -> Bool"
    assert "the left operand of '&&'" in fails("fun f(a : Int) -> Bool = a && a")


# -- literals, still open -----------------------------------------------------


def test_a_literal_sum_still_defaults_to_int(capsys):
    assert output("fun main() { print(Int.toString(1 + 2)) }", capsys) == ["3"]


def test_an_integer_literal_adds_to_a_float(capsys):
    assert output("fun main() { print(Float.toString(1 + 2.5)) }", capsys) == ["3.5"]


def test_an_unannotated_numeric_function_carries_both_predicates():
    # `Num a =>` in all but name: the literal's set and the operator's class,
    # neither of which anything here decides.
    assert scheme("fun inc(x) = x + 1", "inc") == \
        "[OneOf a {Int, Float}, Add a] fun(a) -> a"


def test_int_division_still_truncates_toward_zero(capsys):
    # SPEC-DELTAS.md entry 18, now carried by `instance Div Int`.
    src = "fun main() { print(Int.toString(-7 / 2) + \" \" + Int.toString(-7 % 2)) }"
    assert output(src, capsys) == ["-3 -1"]


def test_division_by_zero_still_panics():
    with pytest.raises(TurkeyPanic, match="division by zero"):
        run("fun main() { print(Int.toString(1 / 0)) }")


# -- the for loop -------------------------------------------------------------


def test_a_for_loop_demands_iterator():
    assert scheme("fun n(xs) -> Int { var k = 0\n for _ in xs { k = k + 1 }\n k }", "n") == \
        "[Iterator a] fun(a) -> Int"


def test_the_loop_variable_is_the_family():
    assert scheme("fun first(xs) { for x in xs { return x }\n error(\"empty\") }",
                  "first") == "[Iterator a] fun(a) -> Item a"


def test_a_user_iterator_runs(capsys):
    src = """
type Two a = Two(a, a)

type TwoCursor = TwoCursor { taken : Int }

instance Iterator (Two a) {
    type Item = a
    type Cursor = TwoCursor

    fun iter(p) = TwoCursor { taken = 0 }

    fun next(p, cur) {
        let k = cur.taken
        cur.taken = k + 1
        if k >= 2 { return None }
        match p {
            Two(x, y) -> if k == 0 { Some(x) } else { Some(y) }
        }
    }
}

fun main() {
    for s in Two("a", "b") {
        print(s)
    }
}
"""
    assert output(src, capsys) == ["a", "b"]


def test_something_that_is_not_an_iterator_says_so():
    assert fails("fun f(n : Int) { for x in n { print(\"x\") } }") == \
        "no instance for 'Iterator Int'"


# -- the boundary the prelude draws -------------------------------------------


def test_the_primitives_are_not_in_the_surface_language():
    """`Prim.intAdd` is what `instance Add Int` is written in terms of, and it
    is in scope there and nowhere else."""
    assert fails("fun f(a : Int) -> Int = Prim.intAdd(a, a)") == \
        "'Prim.intAdd' is not defined"


def test_a_program_may_not_redeclare_a_prelude_class():
    assert fails("class Add a { fun add(a, a) -> a }") == \
        "class 'Add' is declared more than once"


def test_a_program_may_define_a_name_a_class_method_already_has():
    """A method lives in the *global* namespace and a top-level binding lives
    in its module's, so the two no longer collide (M11a). This is the papercut
    `plan.txt` item 3 opens with: M9 had to rename its `add`."""
    assert scheme('fun add(x : String, y : String) -> String = x + y', "add") == \
        "fun(String, String) -> String"


def test_an_operator_still_means_its_method_next_to_a_local_of_that_name(capsys):
    """`+` desugars to `add` at parse time, so a module that defines its own
    `add` would capture every `+` if that node were resolved by name. It is
    marked as a method instead -- see `turkey/resolve.py`."""
    src = '''
fun add(x, y) = x + y
fun main() {
    print(add("a", "b"))
    print(1 + 2)
}
'''
    assert output(src, capsys) == ["ab", "3"]


def test_a_second_instance_for_a_built_in_type_overlaps():
    assert fails("instance Add Int { fun add(x, y) = x }") == \
        "overlapping instances: 'Add Int' and 'Add Int' both apply"


def test_the_float_operators_are_gone():
    with pytest.raises(TurkeyError):
        check("fun f() -> Float = 1.5 +. 2.0")


def test_the_named_comparisons_are_gone():
    for name in ("String.eq", "String.lt", "Bool.eq", "Float.lt", "Char.eq"):
        assert f"'{name}' is not defined" in fails(f"fun f() = {name}")
