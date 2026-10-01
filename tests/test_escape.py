"""`src/Turkey/Escape.gob`: objects that never outlive their call, built in
its frame.

Each test compiles a program, reads the low IR to see which allocations moved
into the frame, and runs it -- plainly, and where the collector matters
compiled with `--gc-verify` and run with `TURKEY_GC_STRESS=1`, which collects
at every allocation and checks the heap each time. A frame object the collector failed to trace
through, or one freed or overwritten while reachable, shows up there as a
verifier failure or wrong output.
"""

import re

from tests import lang

STRESS = {"TURKEY_GC_STRESS": "1"}
VERIFY = ("--gc-verify",)


def _function(ssa: str, name: str) -> str:
    return re.search(rf"^fun @Main#{name}\(.*?^}}$", ssa, re.M | re.S).group(0)


def _ssa(src: str) -> str:
    result = lang.dump("ssa", src)
    assert result.code == 0, result.stderr
    return result.stdout


def _stressed(src: str) -> str:
    result = lang.run(src, env=STRESS, flags=VERIFY)
    assert result.code == 0, result.stderr
    return result.stdout


# A record of closures handed to a function too large to inline that only calls
# them, a hasher handed to one that only updates its integer, and the inner
# closure the callee builds over the record and passes on. The closures capture
# strings built at run time, so a capture the collector lost would print wrong.
READERS = """
type Mapper = Mapper { expr : fun(Int) -> String, ty : fun(String) -> String }
type Hasher = Hasher { h : Int }

fun each(xs : Array Int, f : fun(Int) -> String) -> String {
    var out = ""
    for x in xs { out = out + f(x) + ";" }
    out
}

fun mapKind(xs : Array Int, m : Mapper) -> String {
    let rest = Array.map(xs, fun(x) = Int.toString(x * 10))
    each(xs, fun(x) = m.ty(m.expr(x))) + String.join(rest, ",")
}

fun children(xs : Array Int, tag : Int) -> String {
    let before = "<" + Int.toString(tag)
    let after = Int.toString(tag) + ">"
    mapKind(xs, Mapper { expr = fun(x) = before + Int.toString(x),
                         ty = fun(s) = s + after })
}

fun hashInto(k : Int, d : Hasher) -> Unit {
    for var i = 0; i < 64; i = i + 8 {
        d.h = Int.xor(d.h, Int.and(Int.shr(k, i), 255))
        d.h = Int.mulWrapping(d.h, 1099511628211)
    }
}

fun hash(k : Int) -> Int {
    let d = Hasher { h = -3750763034362895579 }
    hashInto(k, d)
    d.h
}

fun main() {
    print(children([1, 2], 7))
    print(children([3], 8))
    print(hash(12345) % 1000)
}
"""

READERS_OUTPUT = "<717>;<727>;10,20\n<838>;30\n"


def test_objects_a_callee_only_reads_are_built_in_the_frame():
    ssa = _ssa(READERS)
    children = _function(ssa, "children")
    assert "object.new" not in children and "closure.new" not in children, children
    assert children.count("stack.closure") == 2, children
    assert "stack.object kind=1" in children, children
    mapKind = _function(ssa, "mapKind")
    assert "closure.new" not in mapKind, mapKind
    assert "object.new" not in _function(ssa, "hash"), _function(ssa, "hash")


def test_frame_objects_survive_collection():
    """Every allocation in `mapKind` collects under stress. The record and its
    closures are in `children`'s frame, the closures dead as values of their
    own once the record is built: the frame table must keep publishing their
    fields for as long as the record is live, or the strings they captured
    are freed and reused."""
    plain = lang.output(READERS)
    assert plain.startswith(READERS_OUTPUT), plain
    assert _stressed(READERS) == plain


ESCAPES = """
type Box = Box { x : Int }
type Holder = Holder { item : Box }
type Chain = Link(Int, Chain) | End

var kept : Array Box = []

fun toGlobal(n : Int) -> Unit { Array.push(kept, Box { x = n }) }

fun returned(n : Int) -> Box = Box { x = n }

fun captured(n : Int) -> fun() -> Int {
    let b = Box { x = n }
    fun() = b.x
}

fun through(f : fun(Box) -> Int, n : Int) -> Int = f(Box { x = n })

fun stored(h : Holder, n : Int) -> Int {
    h.item = Box { x = n }
    h.item.x
}

fun sum(c : Chain) -> Int = match c {
    Link(n, rest) -> n + sum(rest)
    End -> 0
}

fun chain(n : Int) -> Int {
    var c = End
    for var i = 0; i < n; i = i + 1 { c = Link(i, c) }
    sum(c)
}

fun recursive(n : Int) -> Int {
    fun down(k : Int) -> Int = if k == 0 { n + 1 } else { down(k - 1) }
    down(3)
}

fun main() {
    toGlobal(1)
    print(kept[0].x + returned(2).x + captured(3)() + through(fun(b) = b.x, 4))
    let h = Holder { item = Box { x = 0 } }
    print(stored(h, 5) + h.item.x)
    print(chain(100))
    print(recursive(6))
}
"""


def test_objects_that_outlive_their_call_stay_on_the_heap():
    ssa = _ssa(ESCAPES)
    for name in ["toGlobal", "returned", "captured", "through", "stored"]:
        body = _function(ssa, name)
        assert "stack." not in body and "object.new" in body, (name, body)
    # Each link holds the one built on the previous iteration, which a phi
    # carries round the loop: one frame slot would be every link at once.
    assert "stack." not in _function(ssa, "chain"), _function(ssa, "chain")
    # A recursive closure is written into its own environment once it
    # exists, which is a store after construction.
    assert "stack.closure" not in _function(ssa, "recursive"), _function(ssa, "recursive")
    assert lang.output(ESCAPES) == "10\n10\n4950\n7\n"
    assert _stressed(ESCAPES) == "10\n10\n4950\n7\n"


# A slot is reused on each iteration when nothing still reaches the previous
# iteration's object by the time the site runs again: in `last` the phi that
# carries it round the loop is dead once it is reassigned, and only the final
# object is read after the loop. `chain` above is the case where it is not.
LOOPS = """
type Box = Box { x : Int }

-- Recursive, so that it is called rather than inlined and the box is built.
fun read(b : Box, k : Int) -> Int = if k == 0 { b.x } else { read(b, k - 1) }

fun last(n : Int) -> Int {
    var b = Box { x = -1 }
    for var i = 0; i < n; i = i + 1 { b = Box { x = i } }
    read(b, 2)
}

fun each(n : Int) -> Int {
    var total = 0
    for var i = 0; i < n; i = i + 1 { total = total + read(Box { x = i }, 2) }
    total
}

fun main() {
    print(last(10))
    print(last(0))
    print(each(10))
}
"""


def test_a_slot_is_reused_when_nothing_still_reaches_it():
    ssa = _ssa(LOOPS)
    for name in ["last", "each"]:
        body = _function(ssa, name)
        assert "stack.object" in body and "object.new" not in body, (name, body)
    assert lang.output(LOOPS) == "9\n-1\n45\n"
    assert _stressed(LOOPS) == "9\n-1\n45\n"
