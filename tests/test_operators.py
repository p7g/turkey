"""Operators as class methods, and the prelude that declares them.

`operators.gob` and `iter.gob` are the goldens that run. This file is the part a
golden cannot show: that `+` is *nothing but* a call to `Add.add` -- no table,
no special case, no privilege -- and what follows from that. It also pins the
two boundaries the prelude draws: `Prim.*` is not in the surface language, and
a program may redeclare an arithmetic operator only in the group the language
gives it.
"""

from __future__ import annotations

import pytest

from tests.lang import check, execute as run, types
from tests.lang import CompileError, Panic

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
    with pytest.raises(CompileError) as exc:
        check(src)
    return exc.value.message


def scheme(src: str, name: str) -> str:
    return types(src)[name]


# -- what an operator is ------------------------------------------------------


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
    # Carried by `instance Div Int`, whose primitive truncates.
    src = "fun main() { print(Int.toString(-7 / 2) + \" \" + Int.toString(-7 % 2)) }"
    assert output(src, capsys) == ["-3 -1"]


def test_division_by_zero_still_panics():
    with pytest.raises(Panic, match="division by zero"):
        run("fun main() { print(Int.toString(1 / 0)) }")


# -- the for loop -------------------------------------------------------------


def test_a_for_loop_demands_iterator():
    assert scheme("fun n(xs) -> Int { var k = 0\n for _ in xs { k = k + 1 }\n k }", "n") == \
        "[Iterator a] fun(a) -> Int"


def test_the_loop_variable_is_the_family():
    assert scheme("fun first(xs) { for x in xs { return x }\n error(\"empty\") }",
                  "first") == "[Iterator a] fun(a) -> Iterator.Item a"


def test_a_user_iterator_runs(capsys):
    src = """
type Two a = Two(a, a)

type TwoCursor = TwoCursor { var taken : Int }

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


def test_a_program_may_define_a_name_a_class_method_already_has():
    """A method lives in the *global* namespace and a top-level binding lives
    in its module's, so the two do not collide, and a program is free to call
    a function of its own `add`."""
    assert scheme('fun add(x : String, y : String) -> String = x + y', "add") == \
        "fun(String, String) -> String"


def test_an_operator_still_means_its_method_next_to_a_local_of_that_name(capsys):
    """`+` means `add`, so a module that defines its own `add` would capture
    every `+` if that reference were resolved by name. It is marked as a method
    instead -- see `Turkey.Resolve`."""
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
    with pytest.raises(CompileError):
        check("fun f() -> Float = 1.5 +. 2.0")


def test_the_named_comparisons_are_gone():
    for name in ("String.eq", "String.lt", "Bool.eq", "Float.lt", "Char.eq"):
        assert f"'{name}' is not defined" in fails(f"fun f() = {name}")


# -- declared operators -------------------------------------------------------


def test_a_declared_operator_is_a_call_of_its_target():
    src = "infix Combine (<+>) = pair\nfun pair(a, b) = (a, b)\nfun f(x : Int) = x <+> 'c'"
    assert scheme(src, "f") == "fun(Int) -> (Int, Char)"


def test_a_declared_operator_may_be_used_before_its_declaration(capsys):
    src = """
fun main() { print(1 <+> 2) }
infix Combine (<+>) = plus
fun plus(a : Int, b : Int) = a + b
"""
    assert output(src, capsys) == ["3"]


def test_a_class_method_target_keeps_its_class():
    src = "infix Combine (<>) = combine\nfun twice(x) = x <> x"
    assert scheme(src, "twice") == "[Semigroup a] fun(a) -> a"


def test_an_operator_type_error_points_at_the_operator():
    src = "infix Combine (<+>) = plus\nfun plus(a : Int, b : Int) = a + b\nfun f() = 1 <+> \"x\""
    with pytest.raises(CompileError) as exc:
        check(src)
    assert ":3:13:" in exc.value.rendered


def test_an_unknown_group_is_refused():
    assert fails("infix Concatenation (<+>) = combine").startswith(
        "unknown precedence group 'Concatenation'; the groups are Multiplication,")


def test_a_built_in_operator_cannot_be_redeclared():
    assert fails("infix Conjunction (&&) = combine") == \
        "'&&' is a built-in operator and cannot be redeclared"
    assert fails("infix Disjunction (||) = combine") == \
        "'||' is a built-in operator and cannot be redeclared"


def test_an_arithmetic_operator_keeps_its_group():
    assert fails("infix Combine (+) = combine") == \
        "'+' is always in the group Addition and cannot be declared in Combine"


def test_an_arithmetic_operator_may_be_shadowed_in_its_group(capsys):
    src = """
infix Addition (+) = joined
fun joined(a : String, b : String) -> String = String.join([a, b], "-")
fun main() { print("a" + "b") }
"""
    assert output(src, capsys) == ["a-b"]


@pytest.mark.parametrize("op", ["=", "->", "~"])
def test_punctuation_cannot_be_declared(op):
    assert fails(f"infix Sequence ({op}) = combine") == \
        f"'{op}' is punctuation and cannot be declared as an operator"


def test_the_target_must_take_two_parameters():
    assert fails("infix Sequence (|>) = f\nfun f(x) = x") == \
        "the operator '|>' needs a function of two parameters, and 'f' takes 1"


def test_the_target_must_be_a_function():
    assert fails("infix Sequence (|>) = f\nlet f = fun(a, b) = a") == \
        "the operator '|>' needs a top-level function or class method, and 'f' is neither"


def test_the_target_must_exist():
    assert fails("infix Sequence (|>) = nowhere") == "'nowhere' is not defined"


def test_an_operator_is_declared_once():
    src = "infix Sequence (|>) = combine\ninfix Choice (|>) = combine"
    assert fails(src) == "the operator '|>' is declared twice"


def test_unrelated_groups_need_parentheses():
    src = "infix BitOr (|) = combine\ninfix BitXor (^) = combine\nfun f(a, b, c) = a | b ^ c"
    assert fails(src) == ("'|' (BitOr) and '^' (BitXor) have no precedence between "
                          "them; add parentheses to say which applies first")


def test_a_run_of_operator_characters_is_one_token():
    assert fails("fun f(x : Int) = x*-1") == (
        "the operator '*-' is not declared; if '*-' is more than one operator, "
        "separate them: '* -'")


def test_a_run_where_a_token_was_expected_suggests_the_split():
    assert fails("fun f() { let x =-1\n x }").endswith(
        "; if '=-' is more than one operator, separate them: '= -'")


def test_a_run_may_split_into_declared_operators():
    src = "infix Combine (<>) = combine\nfun f(a : String) = a<>-1"
    assert fails(src).endswith("separate them: '<> -'")


def test_infix_is_still_an_ordinary_name(capsys):
    assert output("fun main() {\n let infix = 3\n print(infix)\n}", capsys) == ["3"]
