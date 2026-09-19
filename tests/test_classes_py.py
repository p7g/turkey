"""Class declarations as the Python parser shapes them.

Internal: split from `test_classes.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations


from turkey import ast
from turkey.parser import parse

# `Either` was declared here until delta 45 put it in the prelude. Declaring one
# anyway would still work -- a type is qualified by its module -- but every
# signature below would then print it as `Main.Either` to say which one it meant.
PRELUDE = ""


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


def test_a_higher_order_parameter_type_needs_no_new_syntax():
    src = "class Mappable f { fun over(f a, fun(a) -> b) -> f b }"
    (decl,) = [d for d in parse(src).decls if isinstance(d, ast.ClassDecl)]
    assert isinstance(decl.methods[0].params[1].type_expr, ast.TEFun)


# -- kinds --------------------------------------------------------------------


# -- schemes and entailment ---------------------------------------------------


# -- superclasses -------------------------------------------------------------

ORD = EQ + """
class Rank a : Egal a {
    fun under(a, a) -> Bool
}

instance Rank Int {
    fun under(x, y) = x < y
}
"""


# -- instance declarations ----------------------------------------------------


def test_an_instance_method_states_no_signature():
    src = "class Egal a { fun egal(a, a) -> Bool }\ninstance Egal Int { fun egal(x, y) = x == y }"
    (inst,) = [d for d in parse(src).decls if isinstance(d, ast.InstanceDecl)]
    assert inst.methods[0].body is not None


# -- rigidity -----------------------------------------------------------------


# -- declaration hygiene ------------------------------------------------------


