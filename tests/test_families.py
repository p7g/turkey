"""Associated type families: reduction, deferral, and what they let a class say.

`families.tl` is the golden that runs. This file is the part a golden cannot
show -- that an equation over a family *waits* rather than succeeding or
failing, which is the third outcome M7 gives unification, and that everything
downstream (schemes, dictionaries, the evaluator) sees a family only after it
has reduced.
"""

from __future__ import annotations

import pytest

from turkey.driver import check, run
from turkey.errors import TurkeyError
from turkey.types import TFam, show_scheme

CONTAINER = """
class Container c {
    type Elem c

    fun first(c) -> Elem c
}

instance Container (Array a) {
    type Elem = a

    fun first(xs) = xs[0]
}

type Box = Box { it : Int }

instance Container Box {
    type Elem = Int

    fun first(b) = b.it
}
"""


def output(src: str, capsys) -> list[str]:
    run(src)
    return capsys.readouterr().out.splitlines()


def fails(src: str) -> str:
    with pytest.raises(TurkeyError) as exc:
        check(src)
    return exc.value.message


def scheme(src: str, name: str) -> str:
    checked = check(src)
    return next(show_scheme(s) for n, s in checked.signatures if n == name)


# -- declaring one ------------------------------------------------------------


def test_a_family_is_declared_over_the_class_parameter():
    assert "must be a family over 'c'" in fails(
        "class C c {\n type Elem d\n fun f(c) -> Int\n}"
    )


def test_a_family_may_not_share_a_name_with_a_type():
    assert "already a type" in fails(
        "type Elem = Elem(Int)\nclass C c {\n type Elem c\n fun f(c) -> Int\n}"
    )


def test_two_classes_may_not_declare_the_same_family():
    assert "already a type family of class 'C'" in fails(
        "class C c {\n type Elem c\n fun f(c) -> Int\n}\n"
        "class D d {\n type Elem d\n fun g(d) -> Int\n}"
    )


def test_a_family_must_be_applied():
    # It is saturated where it is written, for the same reason an alias is.
    assert "must be applied to a type" in fails(
        CONTAINER + "fun f(x : Elem) -> Int = 1"
    )


def test_a_family_takes_the_kind_the_class_gives_its_parameter():
    src = """
    class Box f {
        type Held f

        fun peek(f a) -> a
        fun held(f a) -> Held f
    }
    """
    # `f` is applied in `peek`, so it is `* -> *`, and `Held` is a family over
    # a constructor rather than over a type.
    assert "has kind *, but 'Held' is a family over a type of kind * -> *" in fails(
        src + "fun f(x : Held Int) -> Int = 1"
    )


# -- defining one -------------------------------------------------------------


def test_an_instance_must_define_every_family():
    assert "does not define Elem" in fails(
        "class C c {\n type Elem c\n fun f(c) -> Elem c\n}\n"
        "instance C Int {\n fun f(x) = x\n}"
    )


def test_an_instance_may_not_define_a_family_the_class_lacks():
    assert "not a type family of class 'C'" in fails(
        "class C c {\n fun f(c) -> Int\n}\n"
        "instance C Int {\n type Elem = Int\n fun f(x) = 1\n}"
    )


def test_a_definition_may_only_use_the_head_s_variables():
    assert "'b' is not bound by the instance head 'Array a'" in fails(
        "class C c {\n type Elem c\n fun f(c) -> Elem c\n}\n"
        "instance C (Array a) {\n type Elem = b\n fun f(xs) = xs[0]\n}"
    )


def test_a_definition_may_not_grow_its_argument():
    # `Elem (Array a)` is `Elem` again over something *larger*, so reduction
    # would not terminate. The rule is that a family's argument here is a
    # variable of the head, which is a proper subterm of what selected it.
    assert "so that reduction terminates" in fails(
        "class C c {\n type Elem c\n fun f(c) -> Elem c\n}\n"
        "instance C (Array a) {\n type Elem = Elem (Array a)\n fun f(xs) = xs[0]\n}"
    )


# -- reducing one -------------------------------------------------------------


def test_a_family_reduces_once_the_instance_is_known():
    assert scheme(CONTAINER + "fun f(xs : Array Int) = first(xs)",
                  "f") == "fun(Array Int) -> Int"


def test_reduction_iterates():
    # `Elem (Array (Array Int))` is `Array Int`, which `first` then reduces
    # again -- one call per family application, but the *chain* is followed.
    assert scheme(CONTAINER + "fun f(xs : Array (Array Int)) = first(first(xs))",
                  "f") == "fun(Array (Array Int)) -> Int"


def test_a_family_over_an_open_type_survives_into_the_scheme():
    # Not an error and not a solution: the scheme carries it, and each use
    # site reduces it for itself.
    assert scheme(CONTAINER + "fun f(xs) = first(xs)",
                  "f") == "[Container a] fun(a) -> Elem a"


def test_a_scheme_may_be_constrained_on_a_family():
    src = CONTAINER + """
    class Show a {
        fun show(a) -> String
    }

    instance Show Int {
        fun show(n) = Int.toString(n)
    }

    fun render[Container c, Show (Elem c)](xs : c) -> String = show(first(xs))
    """
    # The whole point of families over a second class parameter: `Show` is
    # demanded of a type the signature never names.
    assert scheme(src, "render") == "[Container a, Show (Elem a)] fun(a) -> String"


def test_an_equation_over_an_open_family_waits_rather_than_failing():
    # At `n + 1` nothing yet says what `xs` is, so `Elem c ~ Int` is neither
    # true nor false -- it waits, and the *later* line decides it. That third
    # outcome is the milestone; order-independence is what it buys.
    src = CONTAINER + """
    fun f(xs) {
        let n = first(xs)
        let m = n + 1
        let ys : Array Int = xs
        return m
    }
    """
    assert scheme(src, "f") == "fun(Array Int) -> Int"


def test_an_equation_no_signature_could_promise_is_rejected():
    # The counterpart: here nothing ever decides `c`, and `Elem c ~ Int` holds
    # for some containers and not others, so `f` cannot be given a type.
    src = CONTAINER + """
    fun f(xs) {
        let n : Int = first(xs)
        return n
    }
    """
    assert "cannot reduce 'Elem a' to 'Int'" in fails(src)


def test_an_equation_that_can_never_be_decided_is_rejected():
    message = fails(CONTAINER + "fun f[Container c](xs : c) -> Int = first(xs)")
    assert "cannot reduce 'Elem a' to 'Int'" in message
    assert "which 'Container' instance defines it" in message


def test_a_family_over_a_type_with_no_instance_is_rejected_where_written():
    assert fails(CONTAINER + "fun f(x : Elem Bool) -> Int = 1\nfun g() = f(true)") == (
        "no instance for 'Container Bool', so 'Elem Bool' has no definition"
    )


def test_a_family_is_not_injective():
    # Two instances agreeing on `Elem` must not make their containers equal.
    src = CONTAINER + """
    fun f(a : Array Int, b : Box) -> Int {
        var x = first(a)
        x = first(b)
        return x
    }
    """
    assert scheme(src, "f") == "fun(Array Int, Box) -> Int"


def test_two_of_the_same_family_application_unify():
    # Reflexivity, which is as far as it goes.
    src = CONTAINER + """
    fun f[Container c](xs : c, ys : c) -> Elem c {
        var x = first(xs)
        x = first(ys)
        return x
    }
    """
    assert scheme(src, "f") == "[Container a] fun(a, a) -> Elem a"


# -- what it makes run --------------------------------------------------------


def test_the_element_type_dispatches_the_method_called_on_it(capsys):
    src = CONTAINER + """
    class Show a {
        fun show(a) -> String
    }

    instance Show Int {
        fun show(n) = Int.toString(n)
    }

    instance Show Bool {
        fun show(b) = if b { "yes" } else { "no" }
    }

    fun render[Container c, Show (Elem c)](xs : c) -> String = show(first(xs))

    fun main() {
        print(render([7]))
        print(render([true]))
        print(render(Box { it = 3 }))
    }
    """
    assert output(src, capsys) == ["7", "yes", "3"]


def test_a_family_is_erased_before_the_evaluator_sees_it():
    checked = check(CONTAINER + "fun main() { print(Int.toString(first([1]))) }")
    scheme_ = next(s for n, s in checked.signatures if n == "main")
    assert not _has_family(scheme_.body)


def _has_family(t) -> bool:
    from turkey.types import TApp, TFun, TTuple, prune

    t = prune(t)
    if isinstance(t, TFam):
        return True
    if isinstance(t, TApp):
        return _has_family(t.fn) or _has_family(t.arg)
    if isinstance(t, TFun):
        return any(_has_family(p) for p in t.params) or _has_family(t.ret)
    if isinstance(t, TTuple):
        return any(_has_family(e) for e in t.elems)
    return False
