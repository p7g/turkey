"""`src/Turkey/Opt.gob`: primitives applied to constants, folded in Core.

What Core *is* must show the fold: a global whose value is a primitive of
literals is a literal at every mention, and compiles to an immediate. What a
program *does* must not move: a constant operation that traps is left alone,
so it panics with its message, and only when it is reached.
"""

import re

import pytest

from tests import lang


def _opt(src: str) -> str:
    result = lang.dump("opt", src)
    assert result.code == 0, result.stderr
    return result.stdout


def _binding(dump: str, name: str) -> str:
    match = re.search(rf"^Main#{name} : .*?(?=^\S|\Z)", dump, re.M | re.S)
    assert match, dump
    return match.group(0)


@pytest.mark.parametrize("definition, literal", [
    ("Char.toInt(':')", "58"),
    ("-1", "-1"),
    ("Int.shl(1, 4) | 3", "19"),
    ("Int.minValue()", "-9223372036854775808"),
])
def test_a_global_of_constants_is_a_literal_at_each_mention(definition, literal):
    dump = _opt(f"""
let limit = {definition}
fun at(b : Int) -> Bool = b == limit
fun main() {{ print(at(3)) }}
""")
    assert f"Prim.intEq(b, {literal})" in _binding(dump, "at")


def test_a_folded_global_compiles_to_an_immediate():
    result = lang.dump("listing", """
let colon = Char.toInt(':')
fun at(b : Int) -> Bool = b == colon
fun main() { print(at(58)) }
""")
    assert result.code == 0, result.stderr
    assert re.search(r"cmp x\d+, #58$", result.stdout, re.M)
    assert not re.search(r"ldr .*Main#colon", result.stdout)


def test_a_constant_condition_selects_its_branch():
    dump = _opt("""
fun main() {
    if Char.toInt('a') < 0x80 { print("ascii") } else { print("wide") }
}
""")
    main = _binding(dump, "main")
    assert '"ascii"' in main and '"wide"' not in main


@pytest.mark.parametrize("expression, message", [
    ("Int.maxValue() + 1", "integer overflow in +"),
    ("-Int.minValue()", "integer overflow in unary -"),
    ("7 / 0", "division by zero"),
    ("Int.shl(1, 64)", "Int.shl: shift amount 64 is outside 0..63"),
    ("Int.shr(1, -1)", "Int.shr: shift amount -1 is outside 0..63"),
])
def test_a_constant_operation_that_traps_panics_where_it_is_reached(expression, message):
    result = lang.panics(f"""
fun main() {{
    print("before")
    print({expression})
}}
""")
    assert result.stdout == "before\n"
    assert lang.panic_message(result) == message


@pytest.mark.parametrize("expression", [
    "Int.maxValue() + 1", "7 / 0", "Int.shl(1, 64)",
])
def test_a_trap_in_a_branch_not_taken_never_panics(expression):
    assert lang.output(f"""
fun pick(wanted : Bool) -> String = if wanted {{ show({expression}) }} else {{ "skipped" }}
fun main() {{ print(pick(False)) }}
""") == "skipped\n"
