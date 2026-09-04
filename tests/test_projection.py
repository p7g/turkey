"""Numeric projections from tuples and positional single-variant values."""

from __future__ import annotations

import pytest

from turkey import ast
from turkey.builtins import initial_values
from turkey.driver import check
from turkey.errors import TurkeyError
from turkey.eval import Evaluator
from turkey.parser import parse
from turkey.pygen import execute
from turkey.types import show_scheme


def failure(src: str) -> str:
    with pytest.raises(TurkeyError) as exc:
        check(src)
    return exc.value.message


def test_parser_distinguishes_numeric_projection_from_fields_and_floats():
    program = parse("fun f(x) = x.0.name.01\nfun n() = 1.25")
    body = program.decls[0].decl.body
    assert isinstance(body, ast.EProject) and body.index == 1
    assert isinstance(body.obj, ast.EField) and body.obj.name == "name"
    assert isinstance(body.obj.obj, ast.EProject) and body.obj.obj.index == 0
    assert isinstance(program.decls[1].decl.body, ast.ELit)


def test_projection_is_read_only():
    assert failure("fun f() { let x = (1, 2); x.0 = 3 }") == (
        "numeric projections are read-only"
    )


def test_generic_projection_is_retained_in_the_signature():
    checked = check("fun first(x) = x.0")
    assert show_scheme(checked.signatures[0][1]) == (
        "[HasProjection 0 a] fun(a) -> Elem.0 a"
    )


def test_projection_result_improves_for_repeated_receiver_and_index():
    """Two projections of one position of one receiver have one type.

    That used to need a rule -- the functional dependency of
    `HasProjection i t a`, enforced by hand in `Solver.improve`. The position's
    type is an associated family now, so both are the type expression
    `Elem.0 a` and ordinary unification does it.
    """
    checked = check("fun duplicate(x) = (x.0, x.0)")
    assert show_scheme(checked.signatures[0][1]) == (
        "[HasProjection 0 a] fun(a) -> (Elem.0 a, Elem.0 a)"
    )


def test_tuple_and_positional_wrapper_project_in_both_backends(capsys):
    src = """
type Payload a = Packed(a, (String, Int))
fun first(x) = x.0
fun main() {
    let p = Packed(7, ("answer", 42))
    print(Int.toString(first(p)) + ":" + Int.toString(p.1.1))
}
"""
    checked = check(src)
    Evaluator(checked.decls, initial_values()).run(checked.opt, checked.main)
    interpreted = capsys.readouterr().out
    execute(checked.opt, checked.decls, checked.main)
    compiled = capsys.readouterr().out
    assert interpreted == compiled == "7:42\n"


@pytest.mark.parametrize("src, message", [
    ("fun f(x : (Int, String)) = x.2", "projection index 2 is out of bounds"),
    ("type R = R { x : Int }\nfun f(x : R) = x.0", "cannot project position 0"),
    ("type S = A(Int) | B(Int)\nfun f(x : S) = x.0", "cannot project position 0"),
])
def test_invalid_projection_receivers_are_rejected(src, message):
    assert message in failure(src)


def test_exhaustiveness_deeply_resolves_an_index_family_payload():
    src = """
fun head(xs) = Some(xs[0])
fun main() {
    match head([(1, "x")]) {
        Some((a, b)) -> print(a)
        None -> print(0)
    }
}
"""
    assert check(src).warnings == []
