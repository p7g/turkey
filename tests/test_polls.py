"""Loop polls: every loop's back edge tests the worker's stop word, and calls
the runtime when a stop has been asked for (`Turkey.Polls`).

With one worker nothing asks for a stop, so the poll's slow path runs only
under GC stress, where the runtime keeps the word up and collects at the poll
as it would at an allocation. That is what checks the slow path's work: the
roots it stores from registers and the registers it keeps across its call.
"""

import re

from tests import bootc, lang

STRESS = {"TURKEY_GC_STRESS": "1"}
VERIFY = ("--gc-verify",)

# Loops with no call and no allocation in them, so that a poll is the only
# safepoint they reach, holding heap objects live across it: in a function
# that calls nothing, where they can stay in registers a call clobbers, and
# alongside values computed in the loop.
LOOPS = """
fun sum(xs : Array Int, times : Int) -> Int {
    var total = 0
    for var t = 0; t < times; t = t + 1 {
        for var i = 0; i < len(xs); i = i + 1 { total = total + xs[i] }
    }
    total
}

fun main() {
    let xs = [1, 2, 3, 4, 5]
    let name = Int.toString(len(xs)) + " items"
    var spin = 0
    while spin < 200 { spin = spin + 1 }
    print(sum(xs, 20))
    print(name + " " + Int.toString(spin))
}
"""


def test_a_loop_tests_the_stop_word_on_its_back_edge(tmp_path):
    source = tmp_path / "main.gob"
    source.write_text(LOOPS)
    text = bootc.boot(*bootc.argv("asm"), str(source))
    assert re.search(r"ldr x16, \[x28, #272\]\n\s+cbnz x16, ", text)
    assert '"_turkey_poll"' in text or '"turkey_poll"' in text


def test_collecting_at_polls_keeps_what_the_loops_hold():
    plain = lang.output(LOOPS)
    result = lang.run(LOOPS, env=STRESS, flags=VERIFY)
    assert result.code == 0, result.stderr
    assert result.stdout == plain
