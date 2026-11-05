"""A join parameter that every jump builds on the spot is passed as its fields.

`match String.step(text, at) { Some((c, next)) -> ... }` leaves a join whose
parameter is the `(Char, Index)` pair once `step` is inlined, and the backend
keeps a tuple in the frame even when it never escapes: three stores at the
jump and two loads in the join, per character. These check the `opt` dump has
the fields as parameters, that the assembly no longer builds the pair, and
that the answers are unchanged -- splitting a parameter wrongly is a
miscompile that still splits.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests import bootc, lang, toolchain
from tests.test_inline import _body


FIRST_TWO = """
fun firstTwo(text : String) -> Int {
    match String.step(text, String.start(text)) {
        Some((c, next)) -> if c == 'a' && String.atEnd(text, next) { 1 } else { 2 }
        None -> 0
    }
}

fun countA(text : String) -> Int {
    var n = 0
    var at = String.start(text)
    while let Some((c, next)) = String.step(text, at) {
        if c == 'a' { n = n + 1 }
        at = next
    }
    n
}

fun main() {
    print(firstTwo("a"))
    print(firstTwo("ab"))
    print(firstTwo(""))
    print(countA("banana é a"))
}
"""


def test_a_string_step_join_takes_the_fields():
    dump = lang.dump("opt", FIRST_TWO)
    assert dump.code == 0, dump.stderr
    for name in ("Main#firstTwo", "Main#countA"):
        body = _body(dump.stdout, name)
        assert ": (Char, Index)" not in body, body
    # The loop binds the two fields one by one: as the parameters of the
    # step's join, or, where that join has one jump, as `let`s at the jump.
    count = _body(dump.stdout, "Main#countA")
    assert re.search(r"%jf\S* : Char\b", count), count
    assert re.search(r"%jf\S* : (Index|Int)\b", count), count
    assert lang.output(FIRST_TWO) == "1\n2\n0\n4\n"


def test_the_pair_is_not_built_in_the_frame(tmp_path: Path):
    entry = tmp_path / "main.gob"
    entry.write_text(FIRST_TWO, encoding="utf-8")
    asm = bootc.boot(*bootc.argv("asm"), str(entry))
    lines = asm.splitlines()
    start = lines.index(f'"{toolchain.c_symbol("Main#firstTwo")}":')
    body = []
    for line in lines[start + 1:]:
        if line.startswith('"') and line.endswith('":'):
            break
        body.append(line.strip())
    # The address of a frame object; `x29` is the frame pointer itself.
    taken = [line for line in body
             if re.match(r"add x(?!29\b)\d+, sp, #", line)]
    assert taken == [], "\n".join(body)


LOOPS = """
-- Both jumps to `go` build the pair, so the pair becomes two parameters.
fun fib(n : Int) -> Int {
    fun go(p : (Int, Int), i : Int) -> Int {
        let (a, b) = p
        if i == 0 { a } else { go((b, a + b), i - 1) }
    }
    go((0, 1), n)
}

-- `p` is used whole as well, so the body rebuilds it, once per iteration.
fun keep(n : Int) -> String {
    fun go(p : (Int, Int), i : Int) -> String =
        if i == 0 { show(p) } else { go((i, i * 2), i - 1) }
    go((0, 1), n)
}

-- One jump passes a variable, so there is nothing to split.
fun mixed(q : (Int, Int), n : Int) -> Int {
    fun go(p : (Int, Int), i : Int) -> Int {
        let (a, b) = p
        if i == 0 { a - b } else { go((b, a), i - 1) }
    }
    go(q, n)
}

fun main() {
    print(fib(40))
    print(keep(3))
    print(mixed((7, 2), 3))
}
"""


def test_a_loop_carried_pair_is_split_only_where_every_jump_builds_it():
    dump = lang.dump("opt", LOOPS)
    assert dump.code == 0, dump.stderr
    assert "p : (Int, Int)" not in _body(dump.stdout, "Main#fib")
    keep = _body(dump.stdout, "Main#keep")
    assert "join rec go(%jf" in keep, keep
    assert "let p : (Int, Int) =" in keep, keep
    assert "join rec go(p : (Int, Int)" in _body(dump.stdout, "Main#mixed")
    assert lang.output(LOOPS) == "102334155\n(1, 2)\n-5\n"
