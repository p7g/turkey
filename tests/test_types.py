"""Unification, the bottom type, generalization and rendering (design.md section 4)."""

from __future__ import annotations

import pytest

from turkey.errors import TypeError_
from turkey.types import (
    BOOL,
    BOTTOM,
    INT,
    STRING,
    Scheme,
    TCon,
    TFun,
    TTuple,
    TVar,
    generalize,
    instantiate,
    join,
    prune,
    show,
    show_scheme,
    unify,
)


# -- unification and pruning ------------------------------------------------


def test_unify_binds_a_variable_and_prune_follows_it():
    a = TVar(1)
    unify(a, INT)
    assert prune(a) is INT


def test_prune_follows_a_chain_of_bindings():
    a, b = TVar(1), TVar(1)
    unify(a, b)
    unify(b, INT)
    assert prune(a) is INT


def test_occurs_check_rejects_an_infinite_type():
    a = TVar(1)
    with pytest.raises(TypeError_, match="infinite type"):
        unify(a, TCon("Array", [a]))


def test_constructor_mismatch_raises():
    with pytest.raises(TypeError_):
        unify(INT, STRING)


def test_tfun_arity_mismatch_uses_singular_and_plural():
    with pytest.raises(TypeError_, match="takes 1 argument but 2 were supplied"):
        unify(TFun([INT], INT), TFun([INT, INT], INT))
    with pytest.raises(TypeError_, match="takes 2 arguments but 1 was supplied"):
        unify(TFun([INT, INT], INT), TFun([INT], INT))


# -- bottom absorption (section 4.3) --------------------------------------------


def test_unify_with_bottom_is_a_noop():
    # Does not raise, and does not bind anything.
    unify(BOTTOM, INT)
    unify(INT, BOTTOM)


def test_join_recovers_the_surviving_type():
    assert join(BOTTOM, INT) is INT
    assert join(INT, BOTTOM) is INT
    assert join(BOTTOM, BOTTOM) is BOTTOM


# -- generalization / instantiation ------------------------------------------


def test_generalize_only_quantifies_deeper_variables():
    deep = TVar(5)
    assert len(generalize(TCon("Array", [deep]), 1).quantified) == 1

    shallow = TVar(1)
    assert len(generalize(TCon("Array", [shallow]), 1).quantified) == 0


def test_instantiate_yields_fresh_independent_variables():
    v = TVar(2)
    scheme = Scheme([v], v)
    fresh = lambda: TVar(1)  # noqa: E731 -- the factory the solver would supply
    i1 = instantiate(scheme, fresh)
    i2 = instantiate(scheme, fresh)
    assert i1 is not i2

    unify(i1, INT)
    assert prune(i1) is INT
    assert isinstance(prune(i2), TVar)  # the other instantiation is untouched


# -- rendering --------------------------------------------------------------


def test_show_renders_surface_syntax():
    assert show(TFun([INT, STRING], BOOL)) == "fun(Int, String) -> Bool"
    assert show(TCon("Array", [TVar(1)])) == "Array a"
    assert show(TTuple([INT, STRING])) == "(Int, String)"
    # A function type nested as a constructor argument gets parenthesized.
    assert show(TCon("Array", [TFun([INT], INT)])) == "Array (fun(Int) -> Int)"


def test_show_scheme_marks_non_quantified_variables_with_underscore():
    free = TVar(1)
    assert show_scheme(Scheme([], TCon("Array", [free]))) == "Array _a"

    bound = TVar(1)
    assert show_scheme(Scheme([bound], TCon("Array", [bound]))) == "Array a"
