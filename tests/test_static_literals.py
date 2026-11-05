"""String literals as static objects (`Turkey.Emit.emitLiterals`).

A literal is laid out in the program's data, outside every heap region, and
the collector and the heap verifier must recognize it there wherever it is
reached from: a heap object's field, a stack root, a global. Under GC stress a
pointer the collector cannot place is a panic, and the verifier fails on one,
so a program that reaches literals all three ways and runs clean under both is
the check.
"""

import re

import pytest

from tests import lang


REACHED = """
type Box = Box { var label : String, n : Int }

let greeting = "hello"
let names = ["alpha", "beta", "gamma"]

fun build(i : Int) -> Box = Box { label = if i % 2 == 0 { "even" } else { "odd" }, n = i }

fun main() {
    let held = "on the stack"
    let boxes : Vec Box = []
    for var i = 0; i < 2000; i = i + 1 {
        Vec.push(boxes, build(i))
        if i % 3 == 0 { boxes[i].label = names[i % 3] }
    }
    var total = 0
    for b in boxes { total = total + String.byteLength(b.label) }
    print(greeting + ", " + names[1])
    print(total)
    print(boxes[1999].label)
    print(held)
}
"""

EXPECTED = "hello, beta\n8000\nodd\non the stack\n"


@pytest.mark.parametrize("env, flags", [
    ({}, ()),
    ({"TURKEY_GC_STRESS": "1"}, ()),
    ({"TURKEY_GC_VERIFY": "1", "TURKEY_GC_STRESS": "7"}, ("--gc-verify",)),
])
def test_literals_reached_from_heap_stack_and_globals(env, flags):
    result = lang.run(REACHED, env=env, flags=flags)
    assert result.code == 0, result.stderr
    assert result.stdout == EXPECTED


def test_a_literal_is_its_address_and_not_a_startup_allocation():
    result = lang.dump("listing", 'fun main() { print("static") }')
    assert result.code == 0, result.stderr
    assert re.search(r"add x\d+, x\d+, .*static\.str\d+.*PAGEOFF", result.stdout)
    assert "turkey_string_new" not in result.stdout
