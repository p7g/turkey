"""Classes and instances: what the goldens reach awkwardly or not at all.

`classes.tl` pins the shape of a working program and `err_no_instance.tl` pins
one error. The rest of the surface is here -- the declaration-time checks, the
signature/definition split in the parser, the class variable's kind, and the
rigidity that stops an instance method from quietly narrowing the type its
class gave it.
"""

from __future__ import annotations

import pytest

from turkey import ast
from turkey.driver import check
from turkey.errors import TurkeyError
from turkey.parser import parse
from turkey.types import show_kind, show_scheme

PRELUDE = """
type Either l r = Left(l) | Right(r)
"""


def sigs(src: str) -> dict[str, str]:
    checked = check(PRELUDE + src)
    return {name: show_scheme(scheme) for name, scheme in checked.signatures}


def bad(src: str) -> str:
    with pytest.raises(TurkeyError) as exc:
        check(PRELUDE + src)
    return exc.value.message


def kind_of_class(src: str, name: str) -> str:
    return show_kind(check(PRELUDE + src).classes.classes[name].kind)


EQ = """
class Egal a {
    fun egal(a, a) -> Bool
}

instance Egal Int {
    fun egal(x, y) = x == y
}
"""


# -- parsing ------------------------------------------------------------------


def test_signature_parameters_are_types_not_binders():
    """`fun combine(a, a) -> a` names one type variable twice, not two binders."""
    src = "class Semigroup a { fun combine(a, a) -> a }"
    (decl,) = [d for d in parse(src).decls if isinstance(d, ast.ClassDecl)]
    (method,) = decl.methods
    assert method.body is None
    assert [p.type_expr.name for p in method.params] == ["a", "a"]


def test_a_method_with_a_body_binds_its_parameters():
    src = "class C a { fun f(x : a) -> a = x }"
    (decl,) = [d for d in parse(src).decls if isinstance(d, ast.ClassDecl)]
    (method,) = decl.methods
    assert method.body is not None
    assert isinstance(method.params[0].pat, ast.PVar)


def test_a_signature_must_state_a_return_type():
    with pytest.raises(TurkeyError) as exc:
        parse("class C a { fun f(a) }")
    assert "must state a return type" in exc.value.message


def test_a_top_level_fun_may_not_omit_its_body():
    """The signature reading is a class-body privilege, not a general one."""
    with pytest.raises(TurkeyError) as exc:
        parse("fun f(a, a) -> a")
    assert "expected '=' or a block" in exc.value.message


def test_a_higher_order_parameter_type_needs_no_new_syntax():
    src = "class Functor f { fun map(f a, fun(a) -> b) -> f b }"
    (decl,) = [d for d in parse(src).decls if isinstance(d, ast.ClassDecl)]
    assert isinstance(decl.methods[0].params[1].type_expr, ast.TEFun)


# -- kinds --------------------------------------------------------------------


def test_the_class_variable_gets_the_kind_its_methods_imply():
    src = "class Functor f { fun map(f a, fun(a) -> b) -> f b }"
    assert kind_of_class(src, "Functor") == "* -> *"


def test_an_unconstrained_class_variable_defaults_to_star():
    assert kind_of_class("class Semigroup a { fun combine(a, a) -> a }",
                         "Semigroup") == "*"


def test_a_superclass_shares_the_kind_of_its_subclass_variable():
    src = """
    class Functor f { fun map(f a, fun(a) -> b) -> f b }
    class Pointed f : Functor f { fun pure(a) -> f a }
    """
    assert kind_of_class(src, "Pointed") == "* -> *"


def test_an_instance_head_must_have_the_class_variable_kind():
    src = """
    class Functor f { fun map(f a, fun(a) -> b) -> f b }
    instance Functor Int { fun map(x, g) = x }
    """
    assert "has kind *" in bad(src)


def test_a_partially_applied_head_is_what_makes_a_two_parameter_type_a_functor():
    src = """
    class Functor f { fun map(f a, fun(a) -> b) -> f b }
    instance Functor (Either l) {
        fun map(e, g) = match e {
            Right(x) -> Right(g(x))
            Left(y) -> Left(y)
        }
    }
    fun use(e : Either String Int) -> Either String Int = map(e, fun(n) { return n })
    """
    assert sigs(src)["use"] == "fun(Either String Int) -> Either String Int"


# -- schemes and entailment ---------------------------------------------------


def test_a_method_carries_its_class_as_a_predicate():
    assert sigs(EQ)["egal"] == "[Egal a] fun(a, a) -> Bool"


def test_a_use_site_propagates_the_context_it_cannot_discharge():
    assert sigs(EQ + "fun both(x, y, z) = egal(x, y) && egal(y, z)")["both"] == \
        "[Egal a] fun(a, a, a) -> Bool"


def test_a_ground_use_discharges_against_the_instance_table():
    assert sigs(EQ + "fun same(x : Int) -> Bool = egal(x, x)")["same"] == \
        "fun(Int) -> Bool"


def test_a_missing_instance_names_the_type_that_lacks_one():
    assert bad(EQ + 'fun f() -> Bool = egal("a", "b")') == \
        "no instance for 'Egal String'"


def test_an_instance_context_becomes_the_use_site_obligation():
    """`Egal (Array a)` holds only where `Egal a` does, and says so.

    No return type, so this is inferred rather than checked (delta 38) and the
    context is the solver's to discover.
    """
    src = EQ + """
    instance [Egal a] Egal (Array a) {
        fun egal(xs, ys) = egal(xs[0], ys[0])
    }
    fun heads(xs : Array a, ys : Array a) = egal(xs, ys)
    """
    assert sigs(src)["heads"] == "[Egal a] fun(Array a, Array a) -> Bool"


def test_a_signature_must_declare_the_context_its_body_needs():
    """The other side of delta 38: a stated type is the whole of the type."""
    src = EQ + """
    instance [Egal a] Egal (Array a) {
        fun egal(xs, ys) = egal(xs[0], ys[0])
    }
    fun heads(xs : Array a, ys : Array a) -> Bool = egal(xs, ys)
    """
    assert bad(src) == "no instance for 'Egal a'"


def test_a_signature_that_declares_its_context_is_accepted():
    src = EQ + """
    instance [Egal a] Egal (Array a) {
        fun egal(xs, ys) = egal(xs[0], ys[0])
    }
    fun heads[Egal a](xs : Array a, ys : Array a) -> Bool = egal(xs, ys)
    """
    assert sigs(src)["heads"] == "[Egal a] fun(Array a, Array a) -> Bool"


def test_an_instance_context_is_discharged_when_the_element_is_known():
    src = EQ + """
    instance [Egal a] Egal (Array a) {
        fun egal(xs, ys) = egal(xs[0], ys[0])
    }
    fun ints(xs : Array Int) -> Bool = egal(xs, xs)
    """
    assert sigs(src)["ints"] == "fun(Array Int) -> Bool"


def test_an_instance_context_that_cannot_be_met_is_reported():
    src = EQ + """
    instance [Egal a] Egal (Array a) {
        fun egal(xs, ys) = egal(xs[0], ys[0])
    }
    fun f(xs : Array String) -> Bool = egal(xs, xs)
    """
    assert bad(src) == "no instance for 'Egal String'"


def test_a_declared_context_is_asked_for_rather_than_asserted():
    """A `fun`'s `[...]` travels the same road as a demand its body raised."""
    src = EQ + "fun f[Egal a](x : a, y : a) -> Bool = egal(x, y)"
    assert sigs(src)["f"] == "[Egal a] fun(a, a) -> Bool"


def test_a_declared_context_over_a_variable_the_type_omits_is_ambiguous():
    src = EQ + "fun f[Egal b](x : Int) -> Int = x"
    assert bad(src) == (
        "'Egal b' constrains a type that the declared type of 'f' does not "
        "mention, so no use of 'f' could decide it"
    )


def test_a_context_names_the_annotation_variables_it_shares():
    """`[Egal a]` constrains the `a` of the signature; it does not introduce one."""
    src = EQ + "fun f[Egal a](xs : Array a) -> Bool = egal(xs[0], xs[0])"
    assert sigs(src)["f"] == "[Egal a] fun(Array a) -> Bool"


# -- superclasses -------------------------------------------------------------

ORD = EQ + """
class Rank a : Egal a {
    fun under(a, a) -> Bool
}

instance Rank Int {
    fun under(x, y) = x < y
}
"""


def test_a_superclass_predicate_is_dropped_from_an_inferred_context():
    assert sigs(ORD + "fun f(x, y) = under(x, y) && egal(x, y)")["f"] == \
        "[Rank a] fun(a, a) -> Bool"


def test_a_superclass_method_is_available_under_the_subclass_context():
    assert sigs(ORD + "fun f[Rank a](x : a, y : a) -> Bool = egal(x, y)")["f"] == \
        "[Rank a] fun(a, a) -> Bool"


def test_an_instance_must_have_its_superclass_instance():
    src = ORD.replace("instance Rank Int", "instance Rank Bool").replace(
        "fun under(x, y) = x < y", "fun under(x, y) = x")
    assert bad(src) == \
        "'Egal Bool' is required by 'Rank', but there is no such instance"


def test_a_superclass_must_constrain_the_class_parameter():
    src = EQ + "class Rank a : Egal Int { fun under(a, a) -> Bool }"
    assert "must constrain its own parameter 'a'" in bad(src)


def test_a_class_may_not_be_its_own_superclass():
    src = """
    class A a : B a { fun f(a) -> a }
    class B a : A a { fun g(a) -> a }
    """
    assert bad(src) == "class 'A' is its own superclass"


# -- instance declarations ----------------------------------------------------


def test_two_instances_for_one_constructor_overlap():
    assert bad(EQ + "instance Egal Int { fun egal(x, y) = False }") == \
        "overlapping instances: 'Egal Int' and 'Egal Int' both apply"


def test_an_instance_head_must_be_a_constructor_over_distinct_variables():
    src = EQ + "instance Egal (Either Int Int) { fun egal(x, y) = True }"
    assert "distinct type variables" in bad(src)


def test_an_instance_must_define_every_method_that_has_no_default():
    assert bad(EQ.replace("fun egal(x, y) = x == y", "")) == \
        "instance 'Egal Int' does not define egal"


def test_an_instance_may_not_define_a_method_of_another_class():
    src = EQ + """
    class Display a { fun display(a) -> String }
    instance Display Int { fun egal(x, y) = True }
    """
    assert bad(src) == "'egal' is not a method of class 'Display'"


def test_an_instance_method_states_no_signature():
    src = "class Egal a { fun egal(a, a) -> Bool }\ninstance Egal Int { fun egal(x, y) = x == y }"
    (inst,) = [d for d in parse(src).decls if isinstance(d, ast.InstanceDecl)]
    assert inst.methods[0].body is not None


def test_a_method_may_not_share_a_name_with_a_top_level_function():
    src = EQ + "fun egal(x, y) = x"
    assert "'egal' is already defined" in bad(src)


def test_a_method_may_not_be_declared_by_two_classes():
    src = EQ + "class Same a { fun egal(a, a) -> Bool }"
    assert bad(src) == "'egal' is already a method of class 'Egal'"


# -- rigidity -----------------------------------------------------------------


def test_an_instance_method_may_not_narrow_the_type_its_class_gave_it():
    """The variables the instance does not fix are rigid, so a body that is
    less general than the signature is rejected rather than accommodated."""
    src = """
    class Functor f { fun map(f a, fun(a) -> b) -> f b }
    instance Functor Option {
        fun map(opt, g) = match opt {
            Some(x) -> Some(x + 1)
            None -> None
        }
    }
    """
    assert "expected" in bad(src)


def test_an_instance_method_must_return_what_the_class_says():
    src = """
    class Functor f { fun map(f a, fun(a) -> b) -> f b }
    instance Functor Option { fun map(opt, g) = opt }
    """
    assert "expected" in bad(src)


def test_an_instance_method_must_have_the_declared_arity():
    assert bad(EQ.replace("fun egal(x, y) = x == y", "fun egal(x) = True")) == \
        "this function takes 1 argument but 2 were supplied"


def test_a_default_body_is_checked_against_the_class_signature():
    src = """
    class Semigroup a {
        fun combine(a, a) -> a
        fun triple(x : a) -> a = combine(x, combine(x, 1))
    }
    """
    # The class variable is rigid inside the default, so `1` is not free to
    # become whatever `a` turns out to be.
    assert bad(src) == \
        "a numeric literal cannot have type 'a'; it must be one of Int, Float"


def test_a_default_may_use_the_class_it_is_declared_in():
    src = """
    class Semigroup a {
        fun combine(a, a) -> a
        fun triple(x : a) -> a = combine(x, combine(x, x))
    }
    instance Semigroup Int { fun combine(x, y) = x + y }
    """
    assert sigs(src)["triple"] == "[Semigroup a] fun(a) -> a"


def test_a_default_may_demand_another_class_through_its_own_context():
    """`fold` is defined through `foldMap[Monoid m]`, and the `m` is rigid in
    the default's body -- so `Monoid m` has to come from the context, not from
    the instance table."""
    src = """
    class Semigroup a { fun combine(a, a) -> a }
    class Monoid a : Semigroup a { fun empty() -> a }
    class Foldable t {
        fun foldMap[Monoid m](t a, fun(a) -> m) -> m
        fun fold[Monoid m](xs : t m) -> m = foldMap(xs, fun(x) { return x })
    }
    """
    assert sigs(src)["fold"] == "[Foldable a, Monoid b] fun(a b) -> b"


# -- declaration hygiene ------------------------------------------------------


def test_an_unknown_class_in_a_context_is_reported():
    assert bad("fun f[Nope a](x : a) -> a = x") == "unknown class 'Nope'"


def test_an_unknown_class_in_an_instance_is_reported():
    assert bad("instance Nope Int { fun f(x) = x }") == "unknown class 'Nope'"


def test_a_class_may_not_be_declared_twice():
    src = EQ + "class Egal a { fun same(a, a) -> Bool }"
    assert bad(src) == "class 'Egal' is declared more than once"


def test_a_class_may_not_share_a_name_with_a_type():
    src = "class Option a { fun f(a) -> a }"
    assert "is already a type" in bad(src)


def test_a_method_parameter_needs_a_type():
    src = "class C a { fun f(x) -> a = x }"
    assert "needs a type" in bad(src)
