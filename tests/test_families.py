"""Associated type families: reduction, deferral, and what they let a class say.

`families.tl` is the golden that runs. This file is the part a golden cannot
display -- that an equation over a family *waits* rather than succeeding or
failing, which is the third outcome M7 gives unification, and that everything
downstream (schemes, dictionaries, the evaluator) sees a family only after it
has reduced.
"""

from __future__ import annotations

import pytest

from turkey import ast
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
    class Display a {
        fun display(a) -> String
    }

    instance Display Int {
        fun display(n) = Int.toString(n)
    }

    fun render[Container c, Display (Elem c)](xs : c) -> String = display(first(xs))
    """
    # The whole point of families over a second class parameter: `Display` is
    # demanded of a type the signature never names.
    assert scheme(src, "render") == "[Container a, Display (Elem a)] fun(a) -> String"


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


def test_an_equation_no_instance_decides_is_carried_by_the_scheme():
    # The counterpart, and what delta 39 changed. Nothing here ever decides
    # `c`, and `Elem c ~ Int` holds for some containers and not others -- so
    # the condition travels to the caller, exactly as `Container c` does. This
    # was a hard error before: the equation was stuck, and a stuck equation was
    # rejected at the binder rather than retained by it.
    src = CONTAINER + """
    fun f(xs) {
        let n : Int = first(xs)
        return n
    }
    """
    assert scheme(src, "f") == "[Elem a ~ Int, Container a] fun(a) -> Elem a"


def test_a_carried_equation_is_checked_at_the_use_site(capsys):
    """What the caller has to make good on, and what happens if it cannot."""
    src = CONTAINER + """
    fun f(xs) {
        let n : Int = first(xs)
        return n
    }
    fun main() { print(Int.toString(f([1, 2]))) }
    """
    assert output(src, capsys) == ["1"]

    bad = CONTAINER + """
    fun f(xs) {
        let n : Int = first(xs)
        return n
    }
    fun g() = f(["no"])
    """
    # `Elem (Array String)` reduces, and the equality is then an ordinary
    # mismatch at the call rather than a mystery inside `f`.
    assert "expected String, found Int" in fails(bad)


def test_an_equation_that_can_never_be_decided_is_rejected():
    message = fails(CONTAINER + "fun f[Container c](xs : c) -> Int = first(xs)")
    # `c` is the name the signature wrote, kept by the skolem (delta 38).
    assert "cannot reduce 'Elem c' to 'Int'" in message
    assert "which 'Container' instance defines it" in message


def test_a_family_over_a_type_with_no_instance_is_rejected_where_written():
    assert fails(CONTAINER + "fun f(x : Elem Bool) -> Int = 1\nfun g() = f(True)") == (
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
    class Display a {
        fun display(a) -> String
    }

    instance Display Int {
        fun display(n) = Int.toString(n)
    }

    instance Display Bool {
        fun display(b) = if b { "yes" } else { "no" }
    }

    fun render[Container c, Display (Elem c)](xs : c) -> String = display(first(xs))

    fun main() {
        print(render([7]))
        print(render([True]))
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


# -- equality constraints (delta 39) ------------------------------------------

OPS = """
type Op = Inc(Int) | Loop(Array Op)

fun useOp(o : Op) -> Int = 1
"""


def test_an_equality_parses_and_round_trips():
    src = OPS + """
    fun c[Iterator s, Item s ~ Op](ops : s) -> Int {
        var n = 0
        for op in ops { n = n + 1 }
        n
    }
    """
    assert scheme(src, "c") == "[Iterator a, Item a ~ Op] fun(a) -> Int"


def test_an_equality_on_the_element_is_inferred():
    """No annotation: the element is merely *used* at a concrete type.

    This was a hard error before delta 39 -- `Item a ~ Op` was stuck at the
    binder, and a stuck equation was rejected there rather than retained.
    """
    src = OPS + """
    fun countOps(ops) {
        var n = 0
        for op in ops { n = n + useOp(op) }
        n
    }
    """
    assert scheme(src, "countOps") == "[Item a ~ Op, Iterator a] fun(a) -> Int"


def test_a_given_equality_lets_a_match_find_its_constructors(capsys):
    """The rewrite, not merely the discharge.

    `op` has type `Item s`, and a `match` cannot look up `Inc` until that
    *becomes* `Op`. Only a declared equality can do it: the given is read as a
    reduction rule for the family (`Solver.reduce`).
    """
    src = OPS + """
    fun runOps[Iterator s, Item s ~ Op](ops : s) -> Int {
        var n = 0
        for op in ops {
            match op {
                Inc(k) -> { n = n + k }
                Loop(inner) -> { n = n + runOps(inner) }
            }
        }
        n
    }
    fun main() {
        print(Int.toString(runOps([Inc(2), Loop([Inc(3), Inc(4)]), Inc(1)])))
    }
    """
    assert scheme(src, "runOps") == "[Iterator a, Item a ~ Op] fun(a) -> Int"
    assert output(src, capsys) == ["10"]


def test_matching_the_element_is_inferred_too():
    """A constructor pattern *unifies*, so it needs no given to work.

    `Inc` fixes the scrutinee at `Op` by the ordinary route, which defers
    `Item a ~ Op` and now retains it. So the un-annotated form gets a type as
    well, and a more general one than delta 38 alone could give it. The given
    earns its place where a family must reduce for something other than
    unification -- see the `runOps` above, whose `Item s` is rigid.
    """
    src = OPS + """
    fun runOps(ops) {
        var n = 0
        for op in ops {
            match op {
                Inc(k) -> { n = n + k }
                Loop(inner) -> { n = n + 1 }
            }
        }
        n
    }
    """
    assert scheme(src, "runOps") == "[Item a ~ Op, Iterator a] fun(a) -> Int"


def test_two_answers_for_one_family_application_conflict():
    """A family is a function of its argument, so it has one answer."""
    src = OPS + """
    fun bad[Iterator s, Item s ~ Op, Item s ~ Int](ops : s) -> Int = 1
    """
    assert "and a family has one answer for one argument" in fails(src)


def test_the_left_side_of_an_equality_must_be_a_family():
    src = "fun f[Int ~ a](x : a) -> Int = 1"
    assert "is not a type family" in fails(src)


def test_an_equality_may_not_define_a_family_by_itself():
    src = OPS + "fun f[Iterator s, Item s ~ Array (Item s)](ops : s) -> Int = 1"
    assert "in terms of itself" in fails(src)


def test_a_context_entry_that_is_neither_form_is_a_parse_error():
    assert "an equality, as in 'Item c ~ Op'" in fails("fun f[Ord a b](x : a) -> Int = 1")


def test_an_equality_costs_no_dictionary():
    """`~` is not a class, so the filters that erase `HasField` erase it too."""
    src = OPS + """
    fun c[Iterator s, Item s ~ Op](ops : s) -> Int {
        var n = 0
        for op in ops { n = n + 1 }
        n
    }
    """
    checked = check(src)
    decl = next(s.decl for s in checked.ordered
                if isinstance(s, ast.SFun) and s.decl.name == "c")
    assert [p.name for p in decl.dicts.preds] == ["Iterator"]
    assert len(decl.dicts.params) == 1
