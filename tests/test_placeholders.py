"""Placeholder functions: what `_` and `\\` are rejected for, and what the
diagnostics say.

What a placeholder function *does* is recorded in
`tests/programs/placeholders.gob`. This file is the other half: every error
the parser gives for `_` or `\\`, and the type error that says what a `_` was
taken to mean.
"""

from __future__ import annotations

import pytest

from tests import lang


def fails(body: str) -> str:
    return lang.fails(f"""
fun pow(x, n) = x * n
type Planet = Planet {{ name : String, moons : Int }}

fun main() {{
    let xs = [1, 2, 3]
    let n = 2
    {body}
}}
""")


@pytest.mark.parametrize("body", [
    "let f = _",
    "_",
    "print(Array.map(xs, fun(y) = _))",
])
def test_a_bare_placeholder_with_nothing_around_it(body):
    assert "'_' on its own is not a function; write 'fun(x) = x'" in fails(body)


@pytest.mark.parametrize("body", [
    "print(Array.map(xs, _ + _))",
    "print(Array.map(xs, _ * n + _))",
])
def test_an_implicit_function_uses_its_parameter_once(body):
    assert "a function made by '_' uses its parameter once" in fails(body)


@pytest.mark.parametrize("body, where", [
    ("if _ > n { print(1) }", "the condition of 'if'"),
    ("while _ { print(1) }", "the condition of 'while'"),
    ("for x in _ { print(x) }", "the sequence of 'for'"),
    ("for var i = 0; _ < i; i = i + 1 { print(i) }", "the condition of 'for'"),
    ("match _ { _ -> print(1) }", "the value 'match' inspects"),
    ("match _.moons { _ -> print(1) }", "the value 'match' inspects"),
    ("if let Some(x) = _ { print(x) }", "the value a 'let' condition matches"),
    ("while let Some(x) = _ { print(x) }", "the value a 'let' condition matches"),
])
def test_a_condition_refuses_a_placeholder(body, where):
    assert f"'_' cannot be used in {where}" in fails(body)


def test_an_assignment_target_refuses_a_placeholder():
    message = fails("var p = Planet { name = \"x\", moons = 0 }\n    _.moons = 3")
    assert "'_' cannot be used in the target of an assignment" in message


def test_a_nested_boundary_inside_a_condition_is_its_own():
    """The `_` belongs to the argument's function, not to the condition --
    so the error is a type error about that function, not the refusal."""
    message = fails("if Array.contains(xs, _ + 1) { print(1) }")
    assert "cannot be used in" not in message


def test_explicit_needs_a_placeholder():
    assert "'\\' makes a function of the '_' after it, and there is none" \
        in fails("let g = \\n + 1")


def test_a_nested_explicit_claims_every_placeholder_inside_it():
    assert "'\\' makes a function of the '_' after it" \
        in fails("print(Array.map(xs, \\Array.map(xs, \\_ * 10)))")


def test_a_non_final_placeholder_statement_is_a_discarded_function():
    """No rule of its own: a statement is a boundary, so `_ * n` is a
    function, and a function is not a value a statement may drop."""
    message = fails("_ * n\n    print(1)")
    assert "is discarded" in message


@pytest.mark.parametrize("body, meaning", [
    ("print(Array.map(xs, show(_ * 2)))", "fun($x) = $x * 2"),
    ("print(Array.map(xs, show(pow(_, 2))))", "fun($x) = pow($x, 2)"),
    ("print(Array.map([Planet { name = \"a\", moons = 1 }], show(_.name)))",
     "fun($x) = $x.name"),
    ("print(Array.map(xs, show((_ + 1) * 2)))", "fun($x) = ($x + 1) * 2"),
])
def test_a_mismatch_at_the_call_says_what_the_placeholder_meant(body, meaning):
    assert f"where '_' means {meaning}" in fails(body)
