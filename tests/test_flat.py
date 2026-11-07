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


# Each function recurses, so none is inlined, and each returns a flat value:
# a sum with a traced slot, a pair across both register files, three doubles,
# and three slots two of which are traced. `twice` passes `parse`'s `Left` on
# through `?`. The closures go through the adapter that boxes a result.
RETURNED = """
type Err = Err { message : String, code : Int }

fun parse(s : String, n : Int) -> Either Err Int {
    if n == 0 { return Left(Err { message = "bad " + s, code = 7 }) }
    if n < 0 { return parse(s, n + 1) }
    Right(n * 2)
}

fun twice(s : String, n : Int) -> Either Err Int {
    let a = parse(s, n)?
    let b = parse(s, a - 1)?
    Right(a + b)
}

fun pair(n : Int) -> (Int, Float) = if n == 0 { (0, 0.5) } else {
    let (a, b) = pair(n - 1)
    (a + n, b * 2.0)
}

fun floats(x : Float, n : Int) -> (Float, Float, Float) =
    if n == 0 { (x, x + 1.0, x + 2.0) } else { floats(x * 2.0, n - 1) }

fun names(n : Int) -> (String, Int, String) =
    if n == 0 { ("a", 0, "z") } else {
        let (x, k, y) = names(n - 1)
        (x + "b", k + 1, y + Int.toString(n))
    }

fun describe(e : Either Err Int) -> String = match e {
    Left(err) -> "Left(" + err.message + ", " + Int.toString(err.code) + ")"
    Right(n) -> "Right(" + Int.toString(n) + ")"
}

fun main() {
    print(describe(twice("x", 3)))
    print(describe(twice("y", 0)))
    print(describe(twice("z", -2)))
    print(pair(4))
    print(floats(1.5, 3))
    print(names(3))
    let kept = [parse("p", 1), parse("q", 0), twice("r", 2)]
    for e in kept { print(describe(e)) }
    let fs = [parse, twice]
    print(describe(fs[0]("c", 5)))
    let gs = [fun(n : Int) -> (String, Int, String) = names(n)]
    print(gs[0](2))
    var total = 0
    for var i = 0; i < 300; i = i + 1 {
        match parse(Int.toString(i), i % 5) {
            Right(v) -> { total = total + v }
            Left(err) -> { total = total + err.code + String.byteLength(err.message) }
        }
        let (s, k, t) = names(i % 4)
        total = total + k + String.byteLength(s) + String.byteLength(t)
    }
    print(total)
}
"""


def test_a_flat_result_comes_back_in_registers():
    plain = lang.output(RETURNED)
    assert plain == (
        "Right(16)\nLeft(bad y, 7)\nLeft(bad z, 7)\n(10, 8.0)\n(12.0, 13.0, 14.0)\n"
        "(abbb, 3, z123)\nRight(2)\nLeft(bad q, 7)\nRight(10)\nRight(10)\n"
        "(abb, 2, z12)\n3968\n")
    result = lang.dump("ssa", RETURNED)
    assert result.code == 0, result.stderr
    # One register per slot, the integer ones numbered apart from the float
    # ones, and nothing built to return or to read a result.
    for name, results in [("parse", "(i1, ptr*, i64)"), ("pair", "(i64, f64)"),
                          ("floats", "(f64, f64, f64)"), ("names", "(ptr*, i64, ptr*)")]:
        body = _function(result.stdout, name)
        assert body.split("{", 1)[0].endswith(f"-> {results} "), body
        assert "object.new" not in body and "stack.object" not in body, body
    assert re.search(r"= result %\d+, 2\n", _function(result.stdout, "parse"))
    # Only the closure's adapter boxes the result.
    adapter = _function(result.stdout, "parse%boxed")
    assert adapter.split("{", 1)[0].endswith("-> ptr* "), adapter
    assert "object.new" in adapter, adapter
    for env in (STRESS, GENERATIONAL):
        stressed = lang.run(RETURNED, env=env, flags=VERIFY)
        assert stressed.code == 0, stressed.stderr
        assert stressed.stdout == plain


# Each function recurses, so none is inlined. In `pick` and `sum` both arms of
# the `if` read an element, which memory holds boxed; in `mixed` one arm
# builds the value from its slots.
MERGED = """
type R = R { a : Int, b : Bool }

fun pick(xs : Array R, ys : Vec R, c : Bool, n : Int) -> Unit {
    if n > 0 { return pick(xs, ys, !c, n - 1) }
    let x = if c { xs[0] } else { xs[1] }
    Vec.push(ys, x)
}

fun sum(xs : Array R, c : Bool, n : Int) -> Int {
    if n > 0 { return sum(xs, !c, n - 1) }
    let x = if c { xs[0] } else { xs[1] }
    x.a
}

fun mixed(xs : Array R, c : Bool, n : Int) -> Vec R {
    if n > 0 { return mixed(xs, !c, n - 1) }
    let x = if c { xs[0] } else { R { a = n + 5, b = c } }
    [x]
}

fun main() {
    let xs = [R { a = 1, b = True }, R { a = 2, b = False }]
    let ys : Vec R = []
    pick(xs, ys, True, 3)
    pick(xs, ys, True, 2)
    print(ys[0].a + ys[1].a)
    print(sum(xs, False, 1))
    print(mixed(xs, True, 1)[0].a)
    print(mixed(xs, True, 2)[0].a)
}
"""


def test_boxes_arriving_at_a_merge_pass_through_it_boxed():
    assert lang.output(MERGED) == "3\n1\n5\n1\n"
    result = lang.dump("ssa", MERGED)
    assert result.code == 0, result.stderr
    # Every value arriving is a box, so the merge takes the box: the push
    # stores the element's own pointer, and the field is read once, after it.
    pick = _function(result.stdout, "pick")
    assert "object.new" not in pick and "object.get %" not in pick.split("call @Data.Vec")[0], pick
    sum_ = _function(result.stdout, "sum")
    assert sum_.count("object.get") == 1, sum_
    # A value built from its slots arrives in them, and the merge takes slots.
    mixed = _function(result.stdout, "mixed")
    assert re.search(r"b\d+\(%\d+:i64, %\d+:i1\):", mixed), mixed


# Two fields that each hold a traced slot and a raw one, so the object's
# traced slots are first only if each field's slots are placed apart: `a.0`,
# `b.0`, then `a.1`, `b.1`, `c`. `T` is five slots, too many to be flat itself.
# `Moving` holds the same pairs in `var` fields, and a record with a narrow
# slot and a sum, which are held boxed.
INLINE = """
type T = T { a : (String, Int), b : (String, Int), c : Int }
type R = R { flag : Bool, n : Int }
type Moving = Moving {
    var a : (String, Int),
    var b : (String, Int),
    var r : R,
    e : Either String Int,
}

fun total(ts : Vec T, ms : Vec Moving) -> Int {
    var n = 0
    for t in ts {
        let T { a = (s, i), b, c } = t
        n = n + String.byteLength(s) + i + String.byteLength(b.0) + b.1 + c
    }
    for m in ms {
        let (s, i) = m.a
        n = n + String.byteLength(s) + i + String.byteLength(m.b.0) + m.b.1 + m.r.n
        n = n + match m.e {
            Left(s) -> String.byteLength(s)
            Right(k) -> k
        }
    }
    n
}

fun main() {
    let ts : Vec T = []
    let ms : Vec Moving = []
    for var i = 0; i < 300; i = i + 1 {
        let s = Int.toString(i)
        Vec.push(ts, T { a = (s + "a", i), b = (s + "bb", i * 2), c = 3 })
        let e = if i % 2 == 0 { Left(s + "e") } else { Right(i) }
        Vec.push(ms, Moving { a = (s, i), b = (s + "!", 1), r = R { flag = True, n = i }, e = e })
    }
    for m in ms {
        let old = m.a
        m.a = (m.b.0 + "~", m.b.1 + 1)
        m.b = old
        m.r = R { flag = !m.r.flag, n = m.r.n + 1 }
    }
    print(total(ts, ms))
    print(ts[7].a)
    print(ms[7].b)
}
"""


def test_fields_of_several_slots_are_held_as_their_slots():
    plain = lang.output(INLINE)
    assert plain == "253755\n(7a, 7)\n(7, 7)\n"
    result = lang.dump("ssa", INLINE)
    assert result.code == 0, result.stderr
    main = _function(result.stdout, "main")
    # `T` is its five slots, two traced. `Moving` is the pairs' four slots and
    # a pointer each to the record and the sum; the sum's is not assignable.
    assert re.search(r"object\.new kind=1 tag=\d+ count=5 bytes=48 pointers=2 mutable=0 ", main), main
    assert re.search(r"object\.new kind=1 tag=\d+ count=6 bytes=56 pointers=4 mutable=31 ", main), main
    # No pair is boxed to be stored, and the record with a `Bool` is, each time.
    assert "kind=0 tag=-1 count=2 bytes=24 pointers=1 " not in main, main
    assert main.count("count=2 bytes=24 pointers=0 ") == 2, main
    for env in (STRESS, GENERATIONAL):
        result = lang.run(INLINE, env=env, flags=VERIFY)
        assert result.code == 0, result.stderr
        assert result.stdout == plain


# The kernel of n-body: each `Vec3` read from a body, combined and stored back
# moves only slots.
BODIES = """
type Vec3 = Vec3 { x : Float, y : Float, z : Float }
type Body = Body { var pos : Vec3, var vel : Vec3, mass : Float }

instance Add Vec3 {
    fun add(a, b) = Vec3 { x = a.x + b.x, y = a.y + b.y, z = a.z + b.z }
}

fun scale(v : Vec3, k : Float) -> Vec3 = Vec3 { x = v.x * k, y = v.y * k, z = v.z * k }

fun step(bodies : Array Body, dt : Float) -> Unit {
    for b in bodies {
        let v = b.vel
        b.vel = v + scale(b.pos, -dt * b.mass)
        b.pos = b.pos + scale(v, dt)
    }
}

fun main() {
    let bodies = [
        Body { pos = Vec3 { x = 1.0, y = 0.0, z = 0.0 }, vel = Vec3 { x = 0.0, y = 1.0, z = 0.0 },
               mass = 1.0 },
        Body { pos = Vec3 { x = 0.0, y = 2.0, z = 0.0 }, vel = Vec3 { x = 0.5, y = 0.0, z = 0.0 },
               mass = 0.25 },
    ]
    for var i = 0; i < 100; i = i + 1 { step(bodies, 0.01) }
    for b in bodies { print(b.pos.x + b.pos.y + b.pos.z + b.vel.x + b.vel.y + b.vel.z) }
}
"""


def test_a_flat_value_moves_between_fields_without_allocating():
    plain = lang.output(BODIES)
    result = lang.dump("ssa", BODIES)
    assert result.code == 0, result.stderr
    step = _function(result.stdout, "step")
    assert "object.new" not in step and "stack.object" not in step, step
    for env in (STRESS, GENERATIONAL):
        result = lang.run(BODIES, env=env, flags=VERIFY)
        assert result.code == 0, result.stderr
        assert result.stdout == plain
