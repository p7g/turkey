"""`src/Turkey/Clean.gob`: copy and dead code elimination.

The passes run on both sides of instruction selection. What a program does is
covered by the whole corpus, which goes through them; these pin that they do
something, on the low IR the `ssa` dump prints, and that what they must not delete
stays.
"""

import re

from tests import lang


def _main(src: str) -> str:
    result = lang.dump("ssa", src)
    assert result.code == 0, result.stderr
    match = re.search(r"^fun @Main#main\(.*?^}$", result.stdout, re.M | re.S)
    assert match, result.stdout[-2000:]
    return match.group(0)


STATEMENTS = """
fun main() {
    print("one")
    print("two")
    print("three")
}
"""


def test_unit_values_between_statements_are_gone():
    assert lang.output(STATEMENTS) == "one\ntwo\nthree\n"
    body = _main(STATEMENTS)
    # One `()` is the one `main` returns; every statement's unit, and the
    # block parameters that carried them, went.
    assert len(re.findall(r"= const \(\)", body)) == 1, body
    assert ":unit)" not in body, body


LOOP = """
fun main() {
    var total = 0
    var ignored = 0
    for i in [1, 2, 3] {
        total = total + i
        ignored = Int.addWrapping(ignored, i)
    }
    print(total)
}
"""


def test_a_value_that_only_feeds_itself_round_a_loop_is_dead():
    assert lang.output(LOOP) == "6\n"
    body = _main(LOOP)
    assert "add.wrap" not in body, body
    # `total` is read, so its checked addition stays.
    assert re.search(r"= add ", body), body


def test_an_unused_operation_that_may_trap_is_kept():
    src = """
    fun main() {
        let big = Int.maxValue()
        let _ = big + [1][0]
        print("unreachable")
    }
    """
    result = lang.panics(src)
    assert lang.panic_message(result) == "integer overflow in +"
