"""M9: record symmetry, punning, mutable parameters, `Bool`, and a total `pop`.

`records.tl`, `err_record_arity.tl` and `mutation.tl` are the goldens. This
file is the part a golden cannot show: that the two declaration forms and the
two pattern forms are genuinely independent of each other, that the
exhaustiveness checker's witness is now a pattern the checker would accept,
and that a reassigned parameter rebinds a local slot rather than aliasing the
argument.
"""

from __future__ import annotations

import pytest

from turkey.driver import check, run
from turkey.errors import TurkeyError, TurkeyPanic
from turkey.types import show_scheme

SHAPES = """
type Shape = Circle { radius : Int } | Rect { width : Int, height : Int }
"""

POINT = "type Point = Point { x : Int, y : Int }\n"


def output(src: str, capsys) -> list[str]:
    run(src)
    return capsys.readouterr().out.splitlines()


def fails(src: str) -> str:
    with pytest.raises(TurkeyError) as exc:
        check(src)
    return exc.value.message


def warnings(src: str) -> list[str]:
    return check(src).warnings


# -- M9.1: either form matches either declaration ---------------------------


def test_a_record_variant_matches_positionally(capsys):
    src = SHAPES + """
fun main() {
    print(Int.toString(match Circle(2) {
        Circle(r) -> r
        Rect(w, h) -> w * h
    }))
}
"""
    assert output(src, capsys) == ["2"]


def test_the_two_pattern_forms_may_be_mixed_in_one_match(capsys):
    src = SHAPES + """
fun main() {
    let r = Rect { width = 3, height = 4 }
    print(Int.toString(match r {
        Circle { radius } -> radius
        Rect(w, h) -> w * h
    }))
}
"""
    assert output(src, capsys) == ["12"]


def test_a_positional_pattern_binds_in_declaration_order(capsys):
    """Not alphabetical, and not the order a record literal happened to use."""
    src = SHAPES + """
fun main() {
    let r = Rect { height = 4, width = 3 }
    match r {
        Rect(w, h) -> print(Int.toString(w) + "," + Int.toString(h))
        Circle(_) -> print("no")
    }
}
"""
    assert output(src, capsys) == ["3,4"]


def test_a_single_variant_mutable_record_matches_positionally(capsys):
    """A `RecordObj`, not a `ConValue` -- the other runtime shape."""
    src = POINT + """
fun main() {
    let p = Point { x = 1, y = 2 }
    let Point(a, b) = p
    print(Int.toString(a + b))
}
"""
    assert output(src, capsys) == ["3"]


def test_a_positional_pattern_must_supply_every_field():
    """Positions are not self-describing, so only the named form may be partial."""
    assert fails(SHAPES + "fun f(s : Shape) -> Int = match s { Rect(w) -> w }") == (
        "constructor 'Rect' takes 2 argument(s), but the pattern supplies 1"
    )


def test_a_record_pattern_may_still_name_a_subset(capsys):
    src = SHAPES + """
fun main() {
    print(Int.toString(match Rect(3, 4) {
        Rect { height } -> height
        Circle { radius } -> radius
    }))
}
"""
    assert output(src, capsys) == ["4"]


def test_a_record_pattern_on_a_positional_variant_is_still_refused():
    """The asymmetry M9.1 removes ran one way only; this direction stays shut."""
    src = "type Pair = Pair(Int, Int)\nfun f(p : Pair) -> Int = match p { Pair { a } -> a }"
    assert fails(src) == "constructor 'Pair' has positional arguments, not fields"


def test_the_exhaustiveness_witness_is_a_pattern_the_checker_accepts():
    """`render` prints positionally; before M9.1 its suggestion was rejected."""
    src = SHAPES + "fun f(s : Shape) -> Int = match s { Circle(r) -> r }"
    assert warnings(src) == [
        "3:27: warning: this match is not exhaustive; 'Rect(_, _)' is not handled"
    ]
    # And the witness, written out, is what closes the match.
    patched = SHAPES + (
        "fun f(s : Shape) -> Int = match s {\n"
        "    Circle(r) -> r\n"
        "    Rect(_, _) -> 0\n"
        "}"
    )
    assert warnings(patched) == []


# -- M9.2: punning in construction ------------------------------------------


def test_a_record_literal_puns(capsys):
    src = POINT + """
fun main() {
    let x = 1
    let y = 2
    let p = Point { x, y }
    print(Int.toString(p.x + p.y))
}
"""
    assert output(src, capsys) == ["3"]


def test_punned_and_written_fields_mix(capsys):
    src = POINT + """
fun main() {
    let x = 5
    let p = Point { x, y = x * 2 }
    print(Int.toString(p.y))
}
"""
    assert output(src, capsys) == ["10"]


def test_a_pun_names_a_variable_not_the_field():
    assert fails(POINT + "fun main() -> Unit {\n    let p = Point { x, y = 1 }\n}") == (
        "'x' is not defined"
    )


# -- M9.3: parameters are mutable -------------------------------------------


def test_a_parameter_may_be_reassigned(capsys):
    src = """
fun gcd(a : Int, b : Int) -> Int {
    while b != 0 {
        let t = b
        b = a % b
        a = t
    }
    a
}
fun main() { print(Int.toString(gcd(48, 18))) }
"""
    assert output(src, capsys) == ["6"]


def test_a_lambda_parameter_may_be_reassigned(capsys):
    src = """
fun main() {
    let f = fun(n) {
        n = n + 1
        n
    }
    print(Int.toString(f(5)))
}
"""
    assert output(src, capsys) == ["6"]


def test_reassigning_a_destructured_parameter_does_not_write_through(capsys):
    """Patterns bind; they do not alias. The caller's record is untouched."""
    src = POINT + """
fun clobber(Point(x, y)) -> Int {
    x = 99
    x + y
}
fun main() {
    let p = Point { x = 1, y = 2 }
    print(Int.toString(clobber(p)))
    print(Int.toString(p.x))
}
"""
    assert output(src, capsys) == ["101", "1"]


def test_a_let_inside_a_function_still_refuses_assignment():
    src = "fun f(a : Int) -> Int {\n    let b = a\n    b = 1\n    b\n}"
    assert fails(src).startswith("cannot assign to 'b': it was bound with 'let'")


def test_a_parameter_is_still_monomorphic():
    """Mutability is about the binding form, not the type; `CDef` is unchanged."""
    assert fails("fun f(g) -> Int = g(1) + String.length(g(\"s\"))") != ""


# -- M9.4: `Bool` is a declared type ----------------------------------------


def test_the_boolean_constructors_are_ordinary_constructors(capsys):
    src = """
fun flip(b : Bool) -> Bool {
    match b {
        True -> False
        False -> True
    }
}
fun main() { print(flip(True)) }
"""
    assert output(src, capsys) == ["False"]


def test_a_one_armed_boolean_match_is_not_exhaustive():
    src = "fun f(b : Bool) -> Int = match b { True -> 1 }"
    assert warnings(src) == [
        "1:26: warning: this match is not exhaustive; 'False' is not handled"
    ]


def test_both_boolean_arms_are_a_complete_signature():
    src = "fun f(b : Bool) -> Int = match b {\n    True -> 1\n    False -> 0\n}"
    assert warnings(src) == []


def test_a_program_may_declare_its_own_bool():
    """`Bool` belongs to `Data.Bool` (delta 42), and a type belongs to its
    module (delta 43), so this shadows -- but `if` still demands the library's,
    which is what stops the shadow from being a way to break the language."""
    check("type Bool = A | B\nfun main() { print(1) }")
    assert fails("type Bool = A | B\nfun main() { if A { print(1) } }") == (
        "expected Main.Bool, found Data.Bool.Bool in an 'if' condition")


def test_the_lower_case_spellings_are_gone():
    assert fails("fun main() { print(true) }") == "'true' is not defined"


def test_show_prints_the_constructor_name(capsys):
    src = "fun main() {\n    print(1 == 1)\n    print(1 == 2)\n}"
    assert output(src, capsys) == ["True", "False"]


def test_a_bool_is_not_an_array_index():
    """The `isinstance(index, bool)` guard is gone; the type system is the check."""
    assert fails("fun main() {\n    let a = [1]\n    print(a[True])\n}") != ""


# -- M9.5: `Array.pop` is total ---------------------------------------------


def test_pop_answers_with_option(capsys):
    src = """
fun main() {
    let a = [1, 2]
    print(match Array.pop(a) {
        Some(x) -> Int.toString(x)
        None -> "empty"
    })
    let _ = Array.pop(a)
    print(match Array.pop(a) {
        Some(x) -> Int.toString(x)
        None -> "empty"
    })
}
"""
    assert output(src, capsys) == ["2", "empty"]


def test_pops_scheme_names_the_preludes_option():
    scheme_ = next(
        s for n, s in check("fun f(xs : Array Int) -> Option Int = Array.pop(xs)").signatures
        if n == "f"
    )
    assert show_scheme(scheme_) == "fun(Array Int) -> Option Int"


def test_popping_an_empty_array_does_not_panic(capsys):
    src = """
fun main() {
    let a = Array.new(4)
    print(match Array.pop(a) {
        Some(x) -> Int.toString(x)
        None -> "empty"
    })
    print(Int.toString(a.length))
}
"""
    assert output(src, capsys) == ["empty", "0"]


def test_reading_an_uninitialized_slot_is_still_a_panic():
    """`Option` launders the empty case, not a program bug."""
    src = """
fun main() {
    let a = Array.new(4)
    print(Int.toString(a[0]))
}
"""
    with pytest.raises(TurkeyPanic):
        run(src)
