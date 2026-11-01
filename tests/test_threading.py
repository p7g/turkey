"""`src/Turkey/Threading.gob`: a join on a `Bool` whose only work is to branch
on it is bypassed, so `||` and `&&` in a condition are compares and branches,
with no `i1` passed to a join and none branched on again.

Each shape test reads the low IR of one function.
"""

import re

from tests import lang


def _body(ssa: str, name: str) -> str:
    found = re.search(rf"^fun @{re.escape(name)}\(.*?^}}$", ssa, re.M | re.S)
    assert found, name
    return found.group(0)


def _ssa(src: str, name: str) -> str:
    result = lang.dump("ssa", src)
    assert result.code == 0, result.stderr
    return _body(result.stdout, name)


def _bool_joins(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines()
            if re.match(r"\s*b\d+\(.*:i1", line)]


AT = """
fun at(xs : Array Int, i : Int) -> Int {
    if i == 77777 { return at(xs, i - 1) }
    xs[i]
}

fun main() { print(at([1, 2, 3], 2)) }
"""


def test_a_bounds_check_is_two_branches_and_the_access_is_proven():
    """The library's `i < 0 || !(i < length)`: each comparison is branched
    on where it is made, and the block holding the access is entered only from
    `i < length`, so the access carries no check of its own.

    A call to itself keeps `at` from being inlined into `main`.
    """
    body = _ssa(AT, "Main#at")
    assert not _bool_joins(body), body
    assert not re.search(r"= not ", body), body
    assert re.search(r"%(\d+) = lt %2, %\d+\n\s*branch %\1:i1", body), body
    gets = [line.strip() for line in body.splitlines() if "= array.get " in line]
    assert gets and all(line.endswith("; in bounds") for line in gets), gets
    assert lang.output(AT) == "3\n"


LOGIC = """
fun f(a : Int, b : Int, c : Int) -> Int {
    var n = 0
    if a < b || b < c { n = n + 1 }
    if a < b && b < c { n = n + 2 }
    if !(a < b || b < c) { n = n + 4 }
    if (a < b || b < c) && c < a { n = n + 8 }
    if a == b || (b == c && c != 2) { n = n + 16 }
    if !(a < b) && !(c < b) { n = n + 32 }
    let k = a < c || b > 1
    if k { n = n + 64 }
    var i = 0
    while i < 3 && (a + i < c || b == i) { i = i + 1 }
    n + 128 * i
}

fun main() {
    let out = []
    for a in [0, 1, 2, 3] { for b in [0, 1, 2, 3] { for c in [0, 1, 2, 3] {
        Vec.push(out, Int.toString(f(a, b, c)))
    } } }
    print(String.join(out, " "))
}
"""


def test_every_condition_is_branches_and_answers_as_before():
    """Nested `||` and `&&`, negated, and in a loop's test: no join on a
    `Bool` survives in `f`, and every combination answers what it did."""
    assert not _bool_joins(_ssa(LOGIC, "Main#f"))
    assert lang.output(LOGIC) == (
        "180 241 369 497 1 337 323 451 65 193 449 451 65 193 321 465 "
        "180 161 225 353 20 52 369 369 73 65 193 451 73 65 193 337 "
        "180 169 161 225 4 52 33 353 84 84 116 241 73 73 65 209 "
        "180 169 169 161 4 52 41 33 68 68 100 97 84 84 84 116\n")


VALUE = """
fun either(a : Int, b : Int) -> Unit {
    if a == 77777 { return either(a - 1, b) }
    let k = a < b || b < 0
    if k { print(k) }
}

fun main() { either(0, 1) }
"""


def test_a_bool_read_after_the_branch_is_left_a_join():
    """`k` is branched on and printed, so the join that delivers it has a use
    the branch does not see, and bypassing it would leave `print(k)` with no
    value."""
    body = _ssa(VALUE, "Main#either")
    assert _bool_joins(body), body
    assert lang.output(VALUE) == "True\n"
