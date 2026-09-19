"""The integer and float literal sets in the Python checker, and the solver steps over them, including a wider integral tower installed by editing its table.

Internal: split from `test_numeric.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations

import pytest

from turkey import types
from turkey.constraints import ONE_OF, CPred, Env, Solver
from turkey.decls import DeclTable
from turkey.driver import check
from turkey.errors import TypeError_
from turkey.types import (
    INT, Pred, TSet, TVar, float_literal_set, int_literal_set, prune,
    show_scheme,
)


@pytest.fixture
def tower(monkeypatch):
    """A three-member integral tower, added the way a real one would be."""
    monkeypatch.setattr(types, "INTEGRAL_WIDTHS", {"Int": None, "Int8": 8, "Int16": 16})


def sigs(src: str) -> dict[str, str]:
    return {name: show_scheme(scheme) for name, scheme in check(src).signatures}


# --------------------------------------------------------------- the sets


def test_an_integer_literal_admits_the_float_types() -> None:
    assert int_literal_set(0) == {"Int", "Float"}
    assert int_literal_set(-7) == {"Int", "Float"}


def test_a_decimal_literal_does_not_admit_the_integral_types() -> None:
    assert float_literal_set() == {"Float"}


def test_an_integer_too_large_to_represent_exactly_is_not_a_float() -> None:
    """One rule for the whole tower: can the type hold this value exactly?

    A literal past a float's mantissa is an `Int` and not a `Float`, rather
    than being admitted and silently rounded.
    """
    assert int_literal_set(2**53 - 1) == {"Int", "Float"}
    assert int_literal_set(2**53) == {"Int"}
    assert int_literal_set(-(2**53)) == {"Int"}


# --------------------------------------------------- what the sets buy you


# ------------------------------------------------------------- defaulting


def test_defaulting_prefers_the_head_of_the_tower(monkeypatch) -> None:
    """Order is the whole rule: integral defaults to whatever leads
    `INTEGRAL_WIDTHS`, which is how "integral -> Int, decimal -> Double" is one
    mechanism rather than two."""
    monkeypatch.setattr(types, "INTEGRAL_WIDTHS", {"Int16": 16, "Int": None})
    assert sigs("fun f(n) = n\nlet y = f(1)\n")["y"] == "Int16"


# ------------------------------------------------ value-dependence, widened


def test_the_set_depends_on_the_value(tower) -> None:
    assert int_literal_set(100) == {"Int", "Int8", "Int16", "Float"}
    assert int_literal_set(300) == {"Int", "Int16", "Float"}
    assert int_literal_set(-129) == {"Int", "Int16", "Float"}
    assert int_literal_set(-128) == {"Int", "Int8", "Int16", "Float"}


def test_a_value_that_fits_one_type_needs_no_context(tower) -> None:
    """Narrowing happens by *choosing the set*, not by narrowing a type after
    the fact -- so a literal only one type can hold is settled on sight."""
    assert sigs("let big = 10000000000000000000000\n") == {"big": "Int"}


def test_the_context_is_rendered_in_tower_order(tower) -> None:
    """Integral types first, in table order, then the decimal ones."""
    assert sigs("let x = 300\n") == {"x": "[OneOf a {Int, Int16, Float}] a"}


# ------------------------------------------------------------- improvement


def test_two_sets_over_one_variable_intersect() -> None:
    solver = Solver(DeclTable(), Env())
    var = TVar(1)
    solver.deferred = [
        CPred(Pred(ONE_OF, [var, TSet({"Int", "Int8"})])),
        CPred(Pred(ONE_OF, [var, TSet({"Int", "Int16"})])),
    ]
    solver.improve_numeric()
    assert len(solver.deferred) == 1
    assert solver.deferred[0].pred.args[1].names == {"Int"}


def test_an_empty_intersection_is_an_error_not_a_deferral() -> None:
    """Nothing later can widen a closed set, so there is nothing to wait for."""
    solver = Solver(DeclTable(), Env())
    var = TVar(1)
    solver.deferred = [
        CPred(Pred(ONE_OF, [var, TSet({"Int8"})])),
        CPred(Pred(ONE_OF, [var, TSet({"Int16"})])),
    ]
    with pytest.raises(TypeError_, match="no numeric type is both"):
        solver.improve_numeric()


def test_intersecting_to_a_singleton_settles_the_variable() -> None:
    solver = Solver(DeclTable(), Env())
    var = TVar(1)
    solver.deferred = [
        CPred(Pred(ONE_OF, [var, TSet({"Int", "Int8"})])),
        CPred(Pred(ONE_OF, [var, TSet({"Int"})])),
    ]
    solver.settle()
    assert solver.deferred == []
    assert prune(var) is INT
