"""Kinds: inference over declarations, defaulting, and application checking.

The golden `hkt.gob` shows a higher-kinded program working end to end; what it
cannot show is the *shape* the checker inferred, or the cases the surface
language reaches only awkwardly. Those are here.
"""

from __future__ import annotations

import pytest

from tests import lang
from tests.lang import CompileError, check, types


@pytest.mark.parametrize("src, variable", [
    ("type Keys k = Keys(Array v)", "v"),
    ("type Box = Box { value : a }", "a"),
    ("type Alias = Array a", "a"),
])
def test_a_type_declaration_rejects_an_undeclared_variable(src, variable):
    with pytest.raises(CompileError, match=(
        rf"type variable '{variable}' is not declared by type"
    )):
        check(src)


@pytest.mark.parametrize("src, message", [
    ("type T a = T[a](a)", "'a' is a parameter of type 'T'"),
    ("type T = T[s](b)", "type variable 'b' is not declared by type"),
    ("type T = T[s, s](s)", "binds a variable twice"),
    ("type T = T[Nope e](e)", "unknown class 'Nope'"),
])
def test_an_existential_bracket_is_checked(src, message):
    with pytest.raises(CompileError, match=message):
        check(src)


def test_a_declared_phantom_parameter_is_valid():
    assert "tag" in types("type Tagged a = Tagged(Int)\nfun tag(n) = Tagged(n)")


def kinds(src: str) -> dict[str, str]:
    """Every type constructor the program declares, and its kind as it prints."""
    return {name.removeprefix("Main#"): kind
            for name, kind in lang.kinds(src).items() if name.startswith("Main#")}


# -- inference over declarations ------------------------------------------


def test_a_parameter_used_applied_gets_an_arrow_kind() -> None:
    # Nothing in the source says `f :: * -> *`; `f a` in the body is the only
    # evidence, and it is enough.
    assert kinds("type Wrap f a = Wrap(f a)")["Wrap"] == "(* -> *) -> * -> *"


def test_an_unconstrained_parameter_defaults_to_star() -> None:
    assert kinds("type Pair a b = Pair(a, b)")["Pair"] == "* -> * -> *"


def test_mutual_recursion_needs_no_ordering() -> None:
    """Arity is syntactic, so every kind skeleton exists before any body is
    read -- which is why there is no SCC pass here, unlike the value level."""
    table = kinds("""
        type Forest f = Forest(f (Tree f))
        type Tree f = Leaf | Node(Forest f)
    """)
    assert table["Tree"] == "(* -> *) -> *"
    assert table["Forest"] == "(* -> *) -> *"


def test_a_higher_kinded_parameter_propagates_through_a_declaration() -> None:
    table = kinds("""
        type Wrap f a = Wrap(f a)
        type Twice g = Twice(Wrap g Int)
    """)
    assert table["Twice"] == "(* -> *) -> *"


def test_an_alias_body_constrains_the_alias_kind() -> None:
    # An alias is expanded at each use, so its body is read nowhere else --
    # without a pass of its own, `f` here would default to `*`.
    assert kinds("type Boxed f = f Int")["Boxed"] == "(* -> *) -> *"


def test_the_prelude_and_built_in_kinds() -> None:
    table = lang.kinds("")
    assert table["Data.Array#Array"] == "* -> *"
    assert table["Prim.Array"] == "* -> *"


# -- what kinds reject -----------------------------------------------------


def bad(src: str) -> str:
    with pytest.raises(CompileError) as excinfo:
        check(src)
    return str(excinfo.value)


def test_over_application_is_a_kind_error_not_an_arity_check() -> None:
    # There is no arity comparison any more: `Int Bool` fails for the same
    # reason `Array Int Bool` does, which is that `*` is not an arrow.
    assert "'Int' has kind *" in bad("fun f(x : Int Bool) = x")
    assert "'Array Int' has kind *" in bad("fun f(x : Array Int Bool) = x")


def test_a_constructor_is_not_a_type() -> None:
    assert "but a type of kind * is needed" in bad("fun f(x : Array) = x")


def test_a_constructor_is_not_a_field_type() -> None:
    assert "but a type of kind * is needed" in bad("type Bad = Bad(Array)")


def test_an_alias_must_stand_for_a_type() -> None:
    assert "but a type of kind * is needed" in bad("type Bad = Array")


def test_an_alias_cannot_be_partially_applied() -> None:
    """The one head that is not rigid. `f a ~ g b` decomposes pointwise only
    because every head is a constructor, so an unsaturated alias would make
    the decomposition unsound rather than merely inconvenient."""
    message = bad("type Boxed f = f Int\nfun k(x : Boxed) = x")
    assert "cannot be partially applied" in message


def test_a_self_application_would_need_an_infinite_kind() -> None:
    assert "a kind that contains itself" in bad("type Bad f = Bad(f f)")


def test_a_variable_head_must_still_be_a_variable() -> None:
    with pytest.raises(CompileError, match="parse error"):
        check("fun f(x : (Array Int) Bool) = x")


# -- kinds inside unification ---------------------------------------------


def test_a_higher_kinded_variable_survives_generalization() -> None:
    """`Wrap a b` in a signature means the `a` was quantified at kind `* -> *`;
    if instantiation dropped the kind, the second use below would not unify."""
    signatures = types("""
        type Wrap f a = Wrap(f a)
        fun unwrap(w) = match w { Wrap(inner) -> inner }
        fun main() {
            let one : Option Int = unwrap(Wrap(Some(1)))
            let two : Array Int = unwrap(Wrap([2]))
        }
    """)
    assert signatures["unwrap"] == "fun(Wrap a b) -> a b"
