"""Kinds in the Python checker's declaration table and unifier.

Internal: split from `test_kinds.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations

import pytest

from turkey.decls import DeclTable
from turkey.errors import TypeError_
from turkey.ast import TypeDecl
from turkey.parser import parse
from turkey.types import (
    ARRAY, INT, KFun, STAR, TVar, apply, array_of, kind_of, prune, show_kind,
    show_scheme, spine, unify,
)


def test_an_existential_bracket_declares_its_variables():
    table = DeclTable()
    table.register_all([d for d in parse(
        "type Some = Some[Show e](e)\n"
        "type Counter = Counter[s] { state : s, read : fun(s) -> Int }\n"
        "type Pair a = Pair[b](a, b)\n"
    ).decls if isinstance(d, TypeDecl)])
    some = table.constructors["Some"]
    assert show_scheme(some.scheme) == "[Show a] fun(a) -> Some"
    assert len(some.exists) == 1 and some.arity == 1
    counter = table.constructors["Counter"]
    assert counter.is_existential and counter.arity == 2
    assert not table.tycons["Counter"].is_mutable_record
    pair = table.constructors["Pair"]
    assert show_scheme(pair.scheme) == "fun(a, b) -> Pair a"
    assert [v.id for v in pair.exists] != []


def kinds(src: str) -> dict[str, str]:
    """Every declared type constructor's kind, as it prints."""
    table = DeclTable()
    table.register_all([d for d in parse(src).decls if isinstance(d, TypeDecl)])
    return {name: show_kind(info.kind) for name, info in table.tycons.items()}


# -- inference over declarations ------------------------------------------


# -- what kinds reject -----------------------------------------------------


# -- kinds inside unification ---------------------------------------------


def test_application_decomposes() -> None:
    """`f a ~ Array Int` binds the head as well as the argument. Sound only
    because there are no type-level lambdas, which is also why an alias has to
    be saturated before it is expanded."""
    f, a = TVar(1), TVar(1)
    applied = apply(f, [a])
    unify(applied, array_of(INT))
    head, args = spine(applied)
    assert head is ARRAY
    assert prune(args[0]) is INT
    # The head's kind was a variable until this unification decided it.
    assert show_kind(kind_of(f)) == "* -> *"


def test_binding_a_variable_checks_its_kind() -> None:
    higher = TVar(1, KFun(STAR, STAR))
    with pytest.raises(TypeError_) as excinfo:
        unify(higher, INT)
    assert "has kind *" in str(excinfo.value)

