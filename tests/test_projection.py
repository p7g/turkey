"""Numeric projections from tuples and positional single-variant values."""

from __future__ import annotations

import pytest

from tests.lang import check, output, types
from tests.lang import CompileError


def failure(src: str) -> str:
    with pytest.raises(CompileError) as exc:
        check(src)
    return exc.value.message


def test_projection_is_read_only():
    assert failure("fun f() { let x = (1, 2); x.0 = 3 }") == (
        "numeric projections are read-only"
    )


def test_generic_projection_is_retained_in_the_signature():
    assert types("fun first(x) = x.0")["first"] == (
        "[HasProjection 0 a] fun(a) -> Elem.0 a"
    )


def test_projection_result_improves_for_repeated_receiver_and_index():
    """Two projections of one position of one receiver have one type.

    That used to need a rule -- the functional dependency of
    `HasProjection i t a`, enforced by hand in `Solver.improve`. The position's
    type is an associated family now, so both are the type expression
    `Elem.0 a` and ordinary unification does it.
    """
    assert types("fun duplicate(x) = (x.0, x.0)")["duplicate"] == (
        "[HasProjection 0 a] fun(a) -> (Elem.0 a, Elem.0 a)"
    )


def test_tuple_and_positional_wrapper_project():
    src = """
type Payload a = Packed(a, (String, Int))
fun first(x) = x.0
fun main() {
    let p = Packed(7, ("answer", 42))
    print(Int.toString(first(p)) + ":" + Int.toString(p.1.1))
}
"""
    assert output(src) == "7:42\n"


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
    assert check(src) == ""
