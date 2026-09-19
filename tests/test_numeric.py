"""Numeric literals: the `OneOf` predicate, improvement, and defaulting.

A numeric literal does not have a type. It has the *set* of types it could
have, and `1` is a numeral, not an `Int` -- its set contains `Float` too, which
is what makes `1 + 2.0` mean what it reads as. Only the reverse is unsafe, so
a decimal literal's set is the float types alone. That asymmetry is the
`Num`/`Fractional` split, and it is the whole of the design.

The goldens cover what whole programs print. This file is the typing: which
literals are accepted where, what a binding over a literal generalizes to, and
where defaulting happens. The sets themselves, and a wider tower installed by
editing the table, are the Python checker's and are in `test_numeric_py`.
"""

from __future__ import annotations

import pytest

from tests import lang
from tests.lang import check, CompileError


def sigs(src: str) -> dict[str, str]:
    return lang.types(src)


# --------------------------------------------------- what the sets buy you


def test_an_integer_past_two_to_the_53_is_not_a_float() -> None:
    """Every integer below 2^53 is exactly a `Float`; past it, one is not, and
    a literal that would round is refused rather than rounded."""
    check("let x : Float = 9007199254740991\n")
    with pytest.raises(CompileError, match="expected Int, found Float"):
        check("let x : Float = 9007199254740993\n")


def test_an_integer_literal_works_where_a_float_is_wanted() -> None:
    assert sigs("fun main() { print(Float.toString(1 + 2.0)) }\n") == {
        "main": "fun() -> Unit"
    }


def test_a_decimal_literal_does_not_work_where_an_int_is_wanted() -> None:
    with pytest.raises(CompileError, match="expected Int, found Float"):
        check("fun f(n : Int) -> Int = n\nlet y = f(1.5)\n")


def test_a_literal_binding_generalizes_over_its_set() -> None:
    assert sigs("let x = 1\n") == {"x": "[OneOf a {Int, Float}] a"}


def test_a_decimal_literal_needs_no_context() -> None:
    """`{Float}` is a singleton, so it is an equation and is discharged as one."""
    assert sigs("let x = 1.5\n") == {"x": "Float"}


# ------------------------------------------------------------- defaulting


def test_an_ambiguous_literal_defaults_at_the_top_level() -> None:
    """`f(1)` is expansive, so nothing generalizes it and the demand travels
    all the way out to where a choice has to be made."""
    assert sigs("fun f(n) = n\nlet y = f(1)\n") == {"f": "fun(a) -> a", "y": "Int"}


def test_an_ambiguous_literal_defaults_at_the_binder() -> None:
    """The literal's type appears in no scheme, so no use site can ever pin it.

    Without defaulting this is the "add a type annotation" error; the point of
    defaulting is that ambiguity is exactly the condition that licenses a
    choice.
    """
    assert sigs('fun main() { let _ = 1\n  print("hi") }\n') == {"main": "fun() -> Unit"}


def test_a_stranded_field_demand_still_does_not_default() -> None:
    """Only `OneOf` defaults. There is no preferred record type to guess."""
    src = ("type Cell = Cell { n : Int }\n"
           "fun main() {\n  var box = []\n  print(Int.toString(box[0].n))\n}\n")
    with pytest.raises(CompileError, match="Add a type annotation"):
        check(src)


# ------------------------------------------------ value-dependence, widened


# ------------------------------------------------------------- improvement


