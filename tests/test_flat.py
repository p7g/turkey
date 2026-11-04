"""Flat values: an immutable, non-recursive aggregate of at most four slots is
held as its slots, one SSA value each, and boxed only where a memory position
holds it.

Two things are checked. That such a value flows through direct calls, `let`s
and `match` without an allocation, which the low IR shows, including inside a
copy keyed by a layout of several slots. And that a value of several slots,
traced ones among them, kept in every kind of memory position -- a field, a
`var` field, an array element, a cell, a closure's capture, a global, a compact
sum's payload, an existential's payload -- survives a collection at every
allocation with the heap verified around each, where a slot traced under one
tag and raw under another, or a box the collector does not see, shows up as a
verifier failure or as output that differs from the plain run.
"""

import re

from tests import lang

STRESS = {"TURKEY_GC_STRESS": "1"}
GENERATIONAL = {"TURKEY_GC_GENERATIONAL": "1", "TURKEY_GC_STRESS": "1"}
VERIFY = ("--gc-verify",)


def _function(ssa: str, name: str) -> str:
    found = re.search(rf"^fun @Main#{re.escape(name)}\(.*?^}}$", ssa, re.M | re.S)
    assert found, f"no function {name} in the dump"
    return found.group(0)


# Each function recurses, so none is inlined into `main`, and each takes,
# binds, matches and passes on its flat argument.
FLOWING = """
type V3 = V3 { x : Float, y : Float, z : Float }

fun walk(p : (Int, Int), n : Int) -> Int {
    if n == 0 { return p.0 + p.1 }
    let (a, b) = p
    let q = (b, a + 1)
    walk(q, n - 1)
}

fun scale(v : V3, k : Float, n : Int) -> Float {
    if n == 0 { return v.x + v.y + v.z }
    let w = V3 { x = v.x * k, y = v.y * k, z = v.z * k }
    scale(w, k, n - 1)
}

fun firstEven(xs : Array Int, i : Int, found : Option Int) -> Int = match found {
    Some(n) -> n
    None -> if i >= len(xs) { -1 } else {
        let next = if xs[i] % 2 == 0 { Some(xs[i]) } else { None }
        firstEven(xs, i + 1, next)
    }
}

-- Polymorphic recursion, so `nest` is copied per layout, `nest@[i64+i64]`
-- among them, and that copy holds its `a` in two slots.
fun nest(x : a, n : Int) -> Int {
    if n == 0 { return 0 }
    let p = (x, x)
    let (y, _) = p
    1 + nest(p, n - 1) + nest(y, 0)
}

fun main() {
    print(walk((1, 2), 10))
    print(scale(V3 { x = 1.0, y = 2.0, z = 3.0 }, 2.0, 3))
    print(firstEven([1, 3, 4, 5], 0, None))
    print(nest((1, 2), 3))
}
"""


def test_flat_values_flow_through_calls_lets_and_matches_without_allocating():
    assert lang.output(FLOWING) == "13\n48.0\n4\n3\n"
    result = lang.dump("ssa", FLOWING)
    assert result.code == 0, result.stderr
    for name in ("walk", "scale", "firstEven"):
        body = _function(result.stdout, name)
        assert "object.new" not in body and "stack.object" not in body, body
    # The copy at a pair takes it in two registers and allocates nothing on the
    # heap. It passes `(x, x)` on boxed, in its frame, to the copy at four
    # slots, which needs the box: it puts its `x` in a tuple too wide to be
    # flat.
    nest = _function(result.stdout, "nest@[i64+i64]")
    assert nest.startswith("fun @Main#nest@[i64+i64](%0:ptr*, %1:i64, %2:i64,"), nest
    assert "object.new" not in nest, nest
    # Each slot is its own parameter: the pair is two `i64`s, the record three
    # `f64`s, and `Option Int` a tag bit and its payload.
    assert _function(result.stdout, "walk").startswith(
        "fun @Main#walk(%0:ptr*, %1:i64, %2:i64,")
    assert _function(result.stdout, "scale").startswith(
        "fun @Main#scale(%0:ptr*, %1:f64, %2:f64, %3:f64,")
    assert re.match(r"fun @Main#firstEven\(%0:ptr\*, %1:ptr\*, %2:i64, %\d+:i1, %\d+:i64\)",
                    _function(result.stdout, "firstEven"))


KEPT = """
type Named = Named { name : String, n : Int }
type Holder = Holder { var pair : (String, Int), fixed : Named }
type Shape = Dot(String) | Box(String, Int) | Empty

type Oops = Oops { what : String, code : Int }
instance Error Oops {
    fun message(e) = e.what + " " + Int.toString(e.code)
}

let start = ("global", 7)

fun weigh(s : Shape) -> Int = match s {
    Dot(name) -> String.byteLength(name)
    Box(name, k) -> String.byteLength(name) * k
    Empty -> 1
}

fun parse(s : String, i : Int) -> Either String Int =
    if i % 3 == 0 { Left(s + "?") } else { Right(i) }

fun describe(e : Either String Int) -> String = match e {
    Left(s) -> s
    Right(n) -> Int.toString(n)
}

fun fail(i : Int) -> Either SomeError Int =
    if i % 4 == 0 { Left(Error.fail(Oops { what = "bad" + Int.toString(i), code = i })) }
    else { Right(i) }

fun main() {
    let named : Vec Named = []
    let shapes : Vec Shape = []
    let results : Vec (Either String Int) = []
    let maybe : Vec (Option (String, Int)) = []
    let holders : Vec Holder = []
    let errors : Vec SomeError = []
    let readers : Vec (fun(Int) -> String) = []
    var last = start
    for var i = 0; i < 200; i = i + 1 {
        let s = Int.toString(i)
        let n = Named { name = s + "!", n = i }
        Vec.push(named, n)
        Vec.push(shapes, if i % 3 == 0 { Dot(s) } else if i % 3 == 1 { Box(s, i) } else { Empty })
        Vec.push(results, parse(s, i))
        Vec.push(maybe, if i % 2 == 0 { Some((s, i)) } else { None })
        Vec.push(holders, Holder { pair = last, fixed = n })
        match fail(i) {
            Left(e) -> Vec.push(errors, e)
            Right(_) -> {}
        }
        Vec.push(readers, fun(k) = n.name + Int.toString(k + n.n))
        last = (s + "#", i)
    }
    for h in holders { h.pair = (h.pair.0 + "~", h.pair.1 + 1) }
    var total = 0
    for n in named { total = total + String.byteLength(n.name) + n.n }
    for s in shapes { total = total + weigh(s) }
    for r in results { total = total + String.byteLength(describe(r)) }
    for m in maybe {
        match m {
            Some((s, k)) -> { total = total + String.byteLength(s) + k }
            None -> { total = total + 3 }
        }
    }
    for h in holders {
        total = total + String.byteLength(h.pair.0) + h.pair.1 + h.fixed.n
    }
    for e in errors { total = total + String.byteLength(Error.messageOf(e)) }
    for var i = 0; i < len(readers); i = i + 1 {
        total = total + String.byteLength(readers[i](i))
    }
    print(total)
    print(last)
    print(describe(results[3]) + describe(results[4]))
}
"""


def test_flat_values_kept_everywhere_survive_a_collection_at_every_allocation():
    plain = lang.output(KEPT)
    assert plain.strip(), "the program printed nothing"
    for env in (STRESS, GENERATIONAL):
        result = lang.run(KEPT, env=env, flags=VERIFY)
        assert result.code == 0, result.stderr
        assert result.stdout == plain
