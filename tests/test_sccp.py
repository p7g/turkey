"""`src/Turkey/Sccp.gob`: constant propagation over the low IR.

Two kinds of check. What a program *does* must not move: every trap a
constant operation would raise is still raised, with its message, and every
fold agrees with running the operation. And what the low IR *is* must show the
folding, or the pass could be a no-op and every behavioral test would pass.
"""

import re

import pytest

from tests import lang


def _main(src: str) -> str:
    """The low IR of the program's own `main`, as `boot ssa` prints it."""
    result = lang.dump("ssa", src)
    assert result.code == 0, result.stderr
    match = re.search(r"^fun @Main#main\(.*?^}$", result.stdout, re.M | re.S)
    assert match, result.stdout[-2000:]
    return match.group(0)


FOLDED = """
fun scale(n : Int) -> Int = n * 8 + 6 * 7

fun main() {
    let limit = 40 + 2
    if limit > 100 { print("never") }
    print(scale(limit) / 4)
    print(limit % 5)
    print(-limit)
    print(Int.minValue() % -1)
    print(Int.shl(1, 62))
}
"""


def test_constants_fold_and_agree_with_running_them():
    assert lang.output(FOLDED) == "94\n2\n-42\n0\n4611686018427387904\n"


def test_folded_arithmetic_and_the_dead_branch_are_gone():
    body = _main(FOLDED)
    for op in ("add", "mul", "div", "rem", "neg", "gt", "branch"):
        assert not re.search(rf"= {op} |^\s*{op} ", body, re.M), (op, body)
    assert '"never"' not in body


@pytest.mark.parametrize("expression, message", [
    ("Int.maxValue() + 1", "integer overflow in +"),
    ("Int.minValue() - 1", "integer overflow in -"),
    ("Int.maxValue() * 2", "integer overflow in *"),
    ("-Int.minValue()", "integer overflow in unary -"),
    ("Int.minValue() / -1", "integer overflow in /"),
    ("7 / 0", "division by zero"),
    ("7 % 0", "remainder by zero"),
])
def test_a_constant_operation_that_traps_still_traps(expression, message):
    result = lang.panics(f"fun main() {{ print({expression}) }}")
    assert lang.panic_message(result) == message


def test_a_trapping_operation_is_kept_rather_than_folded():
    body = _main("fun main() { print(Int.maxValue() + 1) }")
    assert re.search(r"= add ", body), body


@pytest.mark.parametrize("divisor", ["3", "-3", "1", "-1"])
def test_division_by_a_known_divisor_agrees(divisor):
    src = f"""
    fun main() {{
        for a in [-7, 7, -8, 8, 0, Int.maxValue(), Int.minValue() + 1] {{
            let q = a / {divisor}
            let r = a % {divisor}
            if a != q * {divisor} + r {{ print("broken") }}
        }}
        print(Int.minValue() % {divisor})
    }}
    """
    want = {"3": "-2", "-3": "-2", "1": "0", "-1": "0"}[divisor]
    assert lang.output(src) == want + "\n"


def test_a_block_parameter_known_on_every_live_edge_is_folded():
    src = """
    fun pick(flag : Bool) -> Int = if flag { 10 } else { 10 }

    fun main() {
        var n = 3
        n = n * 2
        print(pick(n > 1) + n)
    }
    """
    assert lang.output(src) == "16\n"
    body = _main(src)
    assert not re.search(r"= (add|mul|gt) ", body), body
