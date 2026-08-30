"""Numeric literals: the `OneOf` predicate, improvement, and defaulting.

Both of the sets that ship are singletons (`{Int}` and `{Float}`), so on the
surface language this milestone is invisible by construction -- a singleton
`OneOf` is an equation and is discharged as one, which is why every golden
stayed byte-identical. That makes the goldens useless as coverage for the rules
that only multi-member sets reach.

So most of this file installs a *fake tower* -- `Int8`, `Int16` -- and then runs
ordinary programs through the real driver. That is the claim M3 actually makes:
that a sized-integer tower arrives by editing one table in `turkey/types.py`,
with no change to the generator or the solver. If these tests need either to
change, the claim was false.
"""

from __future__ import annotations

import pytest

from turkey import types
from turkey.constraints import ONE_OF, CPred, Env, Solver
from turkey.decls import DeclTable
from turkey.driver import check
from turkey.errors import TypeError_
from turkey.types import INT, Pred, TSet, TVar, integral_set, prune, show_scheme


@pytest.fixture
def tower(monkeypatch):
    """A three-member integral tower, added the way a real one would be."""
    monkeypatch.setattr(types, "INTEGRAL_WIDTHS", {"Int": None, "Int8": 8, "Int16": 16})


def sigs(src: str) -> dict[str, str]:
    return {name: show_scheme(scheme) for name, scheme in check(src).signatures}


# ----------------------------------------------------------- what ships today


def test_the_shipped_sets_are_singletons() -> None:
    """The reason M3 changes no behaviour, stated as a test rather than a claim."""
    assert integral_set(0) == {"Int"}
    assert integral_set(10**40) == {"Int"}
    assert types.decimal_set() == {"Float"}


def test_a_literal_still_gets_its_type_outright() -> None:
    assert sigs("let x = 1\nlet y = 1.5\n") == {"x": "Int", "y": "Float"}


def test_a_singleton_reports_a_mismatch_as_an_equation_would() -> None:
    """A deferred singleton would blame the literal for a later disagreement.

    This is the whole reason `_one_of` discharges a singleton on the spot, and
    `err_value_restriction.expected` is what would notice if it stopped.
    """
    with pytest.raises(TypeError_) as excinfo:
        check('fun f(s : String) -> String = s\nlet y = f(1)\n')
    assert "expected String, found Int" in str(excinfo.value)


# ------------------------------------------------- value-dependence of the set


def test_the_set_depends_on_the_value(tower) -> None:
    assert integral_set(100) == {"Int", "Int8", "Int16"}
    assert integral_set(300) == {"Int", "Int16"}
    assert integral_set(-129) == {"Int", "Int16"}
    assert integral_set(-128) == {"Int", "Int8", "Int16"}
    assert integral_set(10**40) == {"Int"}


def test_a_value_that_fits_one_type_needs_no_context(tower) -> None:
    """Narrowing happens by *choosing the set*, not by narrowing a type after
    the fact -- so a literal only one type can hold is settled on sight."""
    assert sigs("let big = 10000000000000000000000\n") == {"big": "Int"}


# ----------------------------------------------------------- generalization


def test_a_literal_binding_generalizes_over_its_set(tower) -> None:
    assert sigs("let x = 100\n") == {"x": "[OneOf a {Int, Int8, Int16}] a"}


def test_the_context_is_rendered_in_tower_order(tower) -> None:
    """`Int` first because defaulting prefers it, not alphabetically."""
    assert sigs("let x = 300\n") == {"x": "[OneOf a {Int, Int16}] a"}


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


# --------------------------------------------------------------- defaulting


def test_an_ambiguous_literal_defaults_at_the_binder(tower) -> None:
    """The literal's type appears in no scheme, so no use site can ever pin it.

    Without defaulting this is the "add a type annotation" error; the point of
    defaulting is that ambiguity is exactly the condition that licenses a
    choice.
    """
    assert sigs('fun main() { 100\n  print("hi") }\n') == {"main": "fun() -> Unit"}


def test_an_ambiguous_literal_defaults_at_the_top_level(tower) -> None:
    """`f(100)` is expansive, so nothing generalizes it and the demand travels
    all the way out."""
    assert sigs("fun f(n) = n\nlet y = f(100)\n") == {"f": "fun(a) -> a", "y": "Int"}


def test_defaulting_prefers_the_head_of_the_tower(tower, monkeypatch) -> None:
    """Order is the whole rule: integral defaults to whatever leads
    `INTEGRAL_WIDTHS`, which is how "integral -> Int, decimal -> Double" is one
    mechanism rather than two."""
    monkeypatch.setattr(types, "INTEGRAL_WIDTHS", {"Int16": 16, "Int": None})
    assert sigs("fun f(n) = n\nlet y = f(100)\n")["y"] == "Int16"


def test_a_stranded_field_demand_still_does_not_default(tower) -> None:
    """Only `OneOf` defaults. There is no preferred record type to guess."""
    src = 'type Cell = Cell { n : Int }\nfun main() {\n  var box = []\n  print(Int.toString(box[0].n))\n}\n'
    with pytest.raises(TypeError_, match="Add a type annotation"):
        check(src)


# ------------------------------------------------------------------ errors


def test_a_type_outside_the_set_is_rejected(tower) -> None:
    with pytest.raises(TypeError_, match=r"expected one of Int, Int8, Int16, found String"):
        check('fun f(s : String) -> String = s\nlet y = f(100)\n')
