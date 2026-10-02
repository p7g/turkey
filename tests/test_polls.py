"""Loop polls: every loop's back edge tests the worker's stop word, and calls
the runtime when a stop has been asked for (`Turkey.Polls`).

With one worker nothing asks for a stop, so the poll's slow path runs only
under GC stress, where the runtime keeps the word up and collects at the poll
as it would at an allocation. That is what checks the slow path's work: the
roots it stores from registers and the registers it keeps across its call.
"""

import re

from tests import bootc, lang
from tests.test_memory_model import function

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


# One loop allocates on every iteration, and reaches the allocator's slow
# path, which stops the worker when asked, within a cursor's word of slots;
# the other allocates on only some, and can go around without it.
ALLOCATING = """
fun every(n : Int) -> Array (Option Int) {
    let xs = Array.filled(n, None)
    for var i = 0; i < n; i = i + 1 { xs[i] = Some(i) }
    xs
}

fun some(n : Int) -> Array (Option Int) {
    let xs = Array.filled(n, None)
    for var i = 0; i < n; i = i + 1 {
        if i % 3 == 0 { xs[i] = Some(i) }
    }
    xs
}

fun main() {
    print(len(every(3)) + len(every(4)))
    print(len(some(3)) + len(some(4)))
}
"""


def polls(asm, name):
    return sum(1 for line in function(asm, name)
               if line == "ldr x16, [x28, #272]")


def test_a_loop_that_allocates_on_every_path_needs_no_poll(tmp_path):
    source = tmp_path / "main.gob"
    source.write_text(ALLOCATING)
    text = bootc.boot(*bootc.argv("asm"), str(source))
    assert polls(text, "Main#every") == 0
    assert polls(text, "Main#some") == 1


def test_collecting_while_allocating_loops_run():
    plain = lang.output(ALLOCATING)
    result = lang.run(ALLOCATING, env=STRESS, flags=VERIFY)
    assert result.code == 0, result.stderr
    assert result.stdout == plain


# Recursion with no loop and no allocation reaches no back edge, so a
# function that can call itself again polls at its entry; one that cannot
# does not need to. `walk` holds a heap object live across the poll.
RECURSIVE = """
type Pair = Pair(Int, Int)

fun fib(n : Int) -> Int = if n < 2 { n } else { fib(n - 1) + fib(n - 2) }

fun isEven(n : Int) -> Bool = if n == 0 { True } else { isOdd(n - 1) }
fun isOdd(n : Int) -> Bool = if n == 0 { False } else { isEven(n - 1) }

fun walk(p : Pair, n : Int) -> Int {
    let Pair(a, b) = p
    if n == 0 { a + b } else { walk(p, n - 1) + 1 }
}

fun leaf(n : Int) -> Int = n * 3 + 1

fun main() {
    print(fib(20) + leaf(4))
    print(isEven(10))
    print(walk(Pair(fib(5), leaf(2)), 50))
}
"""


def test_a_recursive_function_polls_at_its_entry(tmp_path):
    source = tmp_path / "main.gob"
    source.write_text(RECURSIVE)
    text = bootc.boot(*bootc.argv("asm"), str(source))
    for name in ("Main#fib", "Main#isEven", "Main#isOdd", "Main#walk"):
        assert polls(text, name) == 1, name
    assert polls(text, "Main#leaf") == 0


def test_collecting_at_entry_polls_keeps_what_recursion_holds():
    plain = lang.output(RECURSIVE)
    result = lang.run(RECURSIVE, env=STRESS, flags=VERIFY)
    assert result.code == 0, result.stderr
    assert result.stdout == plain
