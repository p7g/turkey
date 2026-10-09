"""Record symmetry, punning, `var` fields, mutable parameters, `Bool`, and a
total `pop`.

`records.gob`, `err_record_arity.gob` and `mutation.gob` are the goldens. This
file is the part a golden cannot show: that the two declaration forms and the
two pattern forms are genuinely independent of each other, that the
exhaustiveness checker's witness is a pattern the checker would accept,
and that a reassigned parameter rebinds a local slot rather than aliasing the
argument.
"""

from __future__ import annotations

import pytest

from tests.lang import check, execute as run, types
from tests.lang import CompileError, Panic

SHAPES = """
type Shape = Circle { radius : Int } | Rect { width : Int, height : Int }
"""

POINT = "type Point = Point { x : Int, y : Int }\n"


def output(src: str, capsys) -> list[str]:
    run(src)
    return capsys.readouterr().out.splitlines()


def fails(src: str) -> str:
    with pytest.raises(CompileError) as exc:
        check(src)
    return exc.value.message


def warnings(src: str) -> list[str]:
    """Always empty: the compiler has no warnings, only errors.

    A test asserting this is empty is what would notice a warning quietly
    appearing, and the next real warning will want the plumbing.
    """
    return check(src).splitlines()


# -- either form matches either declaration --------------------------------


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


def test_a_single_variant_record_matches_positionally(capsys):
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


def test_a_record_pattern_names_a_subset_only_with_rest(capsys):
    """A subset is allowed, but it has to say so."""
    silent = SHAPES + "fun f(s : Shape) -> Int = match s {\n    Rect { height } -> height\n    Circle(r) -> r\n}"
    assert fails(silent) == (
        "the pattern 'Rect' does not mention field 'width'; name it, or write "
        "'..' to ignore the rest")
    src = SHAPES + """
fun main() {
    print(Int.toString(match Rect(3, 4) {
        Rect { height, .. } -> height
        Circle { radius } -> radius
    }))
}
"""
    assert output(src, capsys) == ["4"]


def test_a_record_pattern_on_a_positional_variant_is_still_refused():
    """A positional variant has no names, so a record pattern cannot match it."""
    src = "type Pair = Pair(Int, Int)\nfun f(p : Pair) -> Int = match p { Pair { a } -> a }"
    assert fails(src) == "constructor 'Pair' has positional arguments, not fields"


def test_the_exhaustiveness_witness_is_a_pattern_the_checker_accepts():
    """`render` prints positionally, and a positional pattern is accepted for a
    record variant, so the suggestion compiles as written."""
    src = SHAPES + "fun f(s : Shape) -> Int = match s { Circle(r) -> r }"
    assert fails(src) == "this match is not exhaustive; 'Rect(_, _)' is not handled"
    # And the witness, written out, is what closes the match.
    patched = SHAPES + (
        "fun f(s : Shape) -> Int = match s {\n"
        "    Circle(r) -> r\n"
        "    Rect(_, _) -> 0\n"
        "}"
    )
    assert warnings(patched) == []


# -- punning in construction -----------------------------------------------


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


# -- parameters are mutable ------------------------------------------------


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
    assert fails("fun f(g) -> Int = g(1) + String.byteLength(g(\"s\"))") != ""


# -- `var` fields -------------------------------------------------------------


ACCOUNT = "type Account = Account { owner : String, var balance : Int }\n"


def test_only_a_var_field_can_be_assigned(capsys):
    src = ACCOUNT + """
fun main() {
    let a = Account { owner = "ann", balance = 1 }
    a.balance = a.balance + 1
    print(a.balance)
}
"""
    assert output(src, capsys) == ["2"]
    message = fails(ACCOUNT + """
fun main() {
    let a = Account { owner = "ann", balance = 1 }
    a.owner = "bo"
}
""")
    assert message == ("cannot assign to field 'owner': it is not 'var' in "
                       "'Account' (declared at Main.gob:1:26). Declare it "
                       "'var owner' to make it assignable.")


def test_a_polymorphic_assignment_is_checked_where_the_type_is_supplied():
    src = ACCOUNT + """
fun rename(x, name) { x.owner = name }
fun main() { rename(Account { owner = "ann", balance = 1 }, "bo") }
"""
    assert fails(src).startswith("cannot assign to field 'owner'")
    assert "SetField \"balance\" a" in types(ACCOUNT + "fun bump(x) { x.balance = 1 }")["bump"]


def test_var_is_refused_in_a_type_with_several_constructors():
    assert fails("type T = A { var n : Int } | B").startswith(
        "field 'n' cannot be 'var': 'T' has more than one constructor")


def test_var_is_refused_in_an_existential_record():
    assert fails("type Box = Box[Show a] { var item : a }").startswith(
        "field 'item' cannot be 'var': 'Box' is existential")


def test_a_record_with_no_var_field_is_a_value():
    """Immutable, so the value restriction lets it generalize."""
    src = "type Holder a = Holder { item : Option a }\nlet empty = Holder { item = None }"
    assert types(src)["empty"] == "Holder a"


# -- `Bool` is a declared type ---------------------------------------------


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
    """A non-exhaustive match is an error, and `Bool` is the smallest case."""
    src = "fun f(b : Bool) -> Int = match b { True -> 1 }"
    assert fails(src) == "this match is not exhaustive; 'False' is not handled"


def test_both_boolean_arms_are_a_complete_signature():
    src = "fun f(b : Bool) -> Int = match b {\n    True -> 1\n    False -> 0\n}"
    assert warnings(src) == []


def test_a_program_may_declare_its_own_bool():
    """`Bool` belongs to `Data.Bool`, and a type belongs to its
    module, so this shadows -- but `if` still demands the library's,
    which is what stops the shadow from being a way to break the language."""
    check("type Bool = A | B\nfun main() { print(1) }")
    assert fails("type Bool = A | B\nfun main() { if A { print(1) } }") == (
        "expected Main.Bool, found Data.Bool.Type.Bool in an 'if' condition")


def test_the_lower_case_spellings_are_gone():
    assert fails("fun main() { print(true) }") == "'true' is not defined"


def test_show_prints_the_constructor_name(capsys):
    src = "fun main() {\n    print(1 == 1)\n    print(1 == 2)\n}"
    assert output(src, capsys) == ["True", "False"]


def test_a_bool_is_not_an_array_index():
    """The `isinstance(index, bool)` guard is gone; the type system is the check."""
    assert fails("fun main() {\n    let a = [1]\n    print(a[True])\n}") != ""


# -- `Vec.pop` is total ----------------------------------------------------


def test_pop_answers_with_option(capsys):
    src = """
fun main() {
    let a = [1, 2]
    print(match Vec.pop(a) {
        Some(x) -> Int.toString(x)
        None -> "empty"
    })
    let _ = Vec.pop(a)
    print(match Vec.pop(a) {
        Some(x) -> Int.toString(x)
        None -> "empty"
    })
}
"""
    assert output(src, capsys) == ["2", "empty"]


def test_pops_scheme_names_the_preludes_option():
    scheme_ = types("fun f(xs : Vec Int) -> Option Int = Vec.pop(xs)\n")["f"]
    assert scheme_ == "fun(Vec Int) -> Option Int"


def test_popping_an_empty_array_does_not_panic(capsys):
    src = """
fun main() {
    let a = Vec.new(4)
    print(match Vec.pop(a) {
        Some(x) -> Int.toString(x)
        None -> "empty"
    })
    print(Int.toString(len(a)))
}
"""
    assert output(src, capsys) == ["empty", "0"]


def test_reading_an_uninitialized_slot_is_still_a_panic():
    """`Option` launders the empty case, not a program bug."""
    src = """
fun main() {
    let a = Vec.new(4)
    print(Int.toString(a[0]))
}
"""
    with pytest.raises(Panic):
        run(src)


def test_a_filled_vec_has_its_length_and_grows(capsys):
    src = """
fun main() {
    let xs = Vec.filled(3, 4)
    xs[1] = 9
    Vec.push(xs, 12)
    print(len(xs))
    print(xs)
}
"""
    assert output(src, capsys) == ["4", "[4, 9, 4, 12]"]


def test_map_rehashes_into_filled_fixed_storage(capsys):
    src = """
fun main() {
    let m = Map.new()
    for var i = 0; i < 40; i = i + 1 { Map.put(m, i, i * 3) }
    print(Map.get(m, 0))
    print(Map.get(m, 23))
    print(Map.get(m, 39))
}
"""
    assert output(src, capsys) == ["Some(0)", "Some(69)", "Some(117)"]
