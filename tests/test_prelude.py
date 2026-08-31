"""`Show`, `Option`, and iteration as a cursor.

`iter.tl` is the golden that runs one of each. This file is what a golden
cannot show: that `print` is an ordinary constrained function rather than a
builtin that happens to take a `String`, and that the `for` loop never asks a
container how long it is -- which is the whole reason the protocol is a cursor
and not an index.
"""

from __future__ import annotations

import pytest

from turkey.driver import check, run
from turkey.errors import TurkeyError
from turkey.types import show_scheme

# A list is the case indexing cannot serve: no kth element to hand out, so an
# indexed `for` over it would be quadratic even if it could be written.
LIST = """
type List a = Nil | Cons(a, List a)
type Cur a = Cur { rest : List a }

instance Iterator (List a) {
    type Item = a
    type Cursor = Cur a

    fun iter(xs) = Cur { rest = xs }

    fun next(xs, cur) = match cur.rest {
        Nil -> None
        Cons(h, t) -> {
            cur.rest = t
            Some(h)
        }
    }
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


# -- print is a function, not a builtin ---------------------------------------


def test_printing_demands_show():
    assert scheme("fun p(x) { print(x) }", "p") == "[Show a] fun(a) -> Unit"
    assert scheme("fun w(x) { write(x) }", "w") == "[Show a] fun(a) -> Unit"


def test_a_literal_still_defaults(capsys):
    # `[OneOf a {Int, Float}, Show a]`, and defaulting picks the first.
    assert output("fun main() { print(1) }", capsys) == ["1"]


def test_a_user_type_prints_through_its_own_instance(capsys):
    src = """
type Money = Money { cents : Int }

instance Show Money {
    fun show(m) = Int.toString(m.cents) + "c"
}

fun main() { print(Money { cents = 7 }) }
"""
    assert output(src, capsys) == ["7c"]


def test_a_type_with_no_instance_cannot_be_printed():
    src = """
type Point = Point { x : Int }

fun main() { print(Point { x = 1 }) }
"""
    assert fails(src) == "no instance for 'Show Point'"


def test_the_container_instances_nest(capsys):
    src = 'fun main() { print([Some(1), None]) ; print([["a"]]) }'
    assert output(src, capsys) == ["[Some(1), None]", "[[a]]"]


def test_the_machine_write_is_not_in_the_surface_language():
    assert fails('fun main() { Prim.print("x") }') == "'Prim.print' is not defined"


def test_the_prelude_exports_its_bindings_and_nothing_else():
    """What a module may write is its *scope*, not the environment: after
    M11a every builtin lives in one environment and resolution is what
    decides which of them a given module can name."""
    scope = check("fun main() {}").scope
    assert scope["print"] == "Prelude#print"
    assert scope["show"] == "show"                     # a method of a shared class
    assert "Prim.print" not in scope
    assert "Prim.intAdd" not in scope


def test_option_comes_from_the_prelude():
    assert scheme("fun f(x) = Some(x)", "f") == "fun(a) -> Option a"


def test_a_program_may_declare_its_own_option():
    """A type belongs to its module now (delta 43), so this shadows rather than
    colliding -- and the two are different types, which is the point."""
    src = "type Option a = None | Some(a)\nfun f(x) = Some(x)"
    # Both print qualified, because printing `Option` twice would say less.
    assert scheme(src, "f") == "fun(a) -> Main.Option a"
    assert "Data.Option.Option" in fails(
        src + "\nfun g(o : Option Int) -> Bool = Option.isSome(o)")


# -- the loop drives a cursor -------------------------------------------------


def test_a_container_that_cannot_be_indexed_still_iterates(capsys):
    src = LIST + """
fun main() {
    for x in Cons(1, Cons(2, Cons(3, Nil))) {
        print(x)
    }
}
"""
    assert output(src, capsys) == ["1", "2", "3"]


def test_the_loop_variable_is_the_item_family():
    assert scheme(LIST + "fun f(xs : List a) { for x in xs { return x }\n error(\"\") }",
                  "f") == "fun(List a) -> a"
    assert scheme("fun f(xs) { for x in xs { return x }\n error(\"\") }",
                  "f") == "[Iterator a] fun(a) -> Item a"


def test_the_cursor_is_made_once_and_advanced_per_element(capsys):
    """`iter` runs once, `next` once per element plus the one that ends it."""
    src = """
type Two = Two { unused : Int }
type TwoCur = TwoCur { taken : Int }

instance Iterator Two {
    type Item = Int
    type Cursor = TwoCur

    fun iter(t) {
        print("iter")
        TwoCur { taken = 0 }
    }

    fun next(t, cur) {
        print("next")
        let k = cur.taken
        cur.taken = k + 1
        if k >= 2 { return None }
        Some(k)
    }
}

fun main() {
    let t = Two { unused = 0 }
    for x in t { print(x) }
}
"""
    assert output(src, capsys) == [
        "iter", "next", "0", "next", "1", "next",
    ]


def test_nothing_asks_the_container_for_a_length(capsys):
    """An iterator with no end at all is fine, because the loop never counts.

    Under the indexed protocol this program could not be written: `count` would
    have to answer, and there is no answer.
    """
    src = """
type Naturals = Naturals { unused : Int }
type Counter = Counter { at : Int }

instance Iterator Naturals {
    type Item = Int
    type Cursor = Counter

    fun iter(n) = Counter { at = 0 }

    fun next(n, cur) {
        let k = cur.at
        cur.at = k + 1
        Some(k)
    }
}

fun main() {
    let ns = Naturals { unused = 0 }
    for x in ns {
        if x > 2 { break }
        print(x)
    }
}
"""
    assert output(src, capsys) == ["0", "1", "2"]


def test_continue_still_advances_the_cursor(capsys):
    src = LIST + """
fun main() {
    for x in Cons(1, Cons(2, Cons(3, Nil))) {
        if x == 2 { continue }
        print(x)
    }
}
"""
    assert output(src, capsys) == ["1", "3"]


def test_an_instance_must_define_both_families():
    src = """
type Bag a = Bag(a)

instance Iterator (Bag a) {
    type Item = a

    fun iter(b) = b
    fun next(b, cur) = None
}
"""
    assert fails(src) == "instance 'Iterator Bag a' does not define Cursor"


def test_the_methods_are_ordinary_and_callable_by_name():
    src = "fun head(xs) { let cur = iter(xs)\n next(xs, cur) }"
    assert scheme(src, "head") == "[Iterator a] fun(a) -> Option (Item a)"
