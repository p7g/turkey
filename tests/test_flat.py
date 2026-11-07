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



# A pair with a narrow slot is not inlined, so a tuple of three of them holds
# three pointers to its box. The parameter arrives boxed and the tuple reuses
# that box, rather than taking the pair in slots and boxing it again.
TRIPLE = """
fun triple(p : (Bool, Int)) -> ((Bool, Int), (Bool, Int), (Bool, Int)) = (p, p, p)

fun main() {
    let xs = [(True, 1), (False, 2)]
    for x in xs { print(triple(x).1) }
}
"""


def test_a_field_that_boxes_its_value_takes_the_box_it_was_passed():
    assert lang.output(TRIPLE) == "(True, 1)\n(False, 2)\n"
    result = lang.dump("ssa", TRIPLE)
    assert result.code == 0, result.stderr
    triple = _function(result.stdout, "triple")
    assert triple.startswith("fun @Main#triple(%0:ptr*, %1:ptr*)"), triple
    assert triple.count("object.new") == 1, triple


# Inline fields where the layout has the most to get wrong: `Big` is 81 slots,
# so its last `var` fields are past the mutable mask's bits; a sum's
# constructors each hold their own inline pairs; `Wrap a` is copied per layout
# of `a`, inline at a pair of words and at a four-word record, boxed at a pair
# with a `Bool`; and an existential holds an inline pair beside its payload.
WIDE = """
type Q = Q { a : Int, b : Int, c : Int, d : Int }
type Big = Big {
    f0 : Q, f1 : Q, f2 : Q, f3 : Q, f4 : Q, f5 : Q, f6 : Q, f7 : Q,
    f8 : Q, f9 : Q, f10 : Q, f11 : Q, f12 : Q, f13 : Q, f14 : Q,
    var f15 : Q, var f16 : Q, var f17 : (String, String, String, String), f18 : Q, var f19 : Q, var last : Int,
}

type S = A { p : (String, Int), q : Int, r : String, s : String, t : (Int, String) } | B { n : Int } | C { z : (String, String) }

type Wrap a = Wrap { var v : a, n : Int, m : Int, k : Int, j : Int }

type Any = Any[Show a] { payload : a, more : (String, Int) }

fun mk(x : a) -> Wrap a = Wrap { v = x, n = 1, m = 2, k = 3, j = 4 }

fun swap(w : Wrap a, x : a) -> a {
    let old = w.v
    w.v = x
    old
}

fun q(n : Int) -> Q = Q { a = n, b = n + 1, c = n + 2, d = n + 3 }

fun sumQ(x : Q) -> Int = x.a + x.b + x.c + x.d

fun describe(s : S) -> String = match s {
    A { p = (x, i), q, r, s, t = (j, y) } -> x + Int.toString(i + q + j) + r + s + y
    B { n } -> Int.toString(n)
    C { z = (a, b) } -> a + b
}

fun look(v : Any) -> String = match v {
    Any { payload, more = (s, i) } -> show(payload) + s + Int.toString(i)
}

fun main() {
    var total = 0
    let bigs : Vec Big = []
    for var i = 0; i < 50; i = i + 1 {
        let s = Int.toString(i)
        let b = Big { f0 = q(i), f1 = q(1), f2 = q(2), f3 = q(3), f4 = q(4), f5 = q(5), f6 = q(6),
                      f7 = q(7), f8 = q(8), f9 = q(9), f10 = q(10), f11 = q(11), f12 = q(12),
                      f13 = q(13), f14 = q(14), f15 = q(15), f16 = q(16), f17 = (s, s + "x", s + "y", s + "z"),
                      f18 = q(18), f19 = q(19), last = 0 }
        b.f16 = q(i * 2)
        b.f19 = q(i * 3)
        b.f17 = (b.f17.3, b.f17.2, b.f17.1, b.f17.0)
        b.last = b.f19.d
        total = total + sumQ(b.f16) + sumQ(b.f19) + b.last + sumQ(b.f18) + String.byteLength(b.f17.0)
        Vec.push(bigs, b)
    }
    for b in bigs { total = total + sumQ(b.f19) + sumQ(b.f0) + String.byteLength(b.f17.3) }
    print(total)
    print(bigs[7].f17)
    let ss = [A { p = ("a", 1), q = 2, r = "r", s = "s", t = (3, "t") }, B { n = 5 }, C { z = ("c", "d") }]
    for s in ss { print(describe(s)) }
    let w = mk(("one", 1))
    let old = swap(w, ("two", 2))
    print(old)
    print(w.v)
    let w2 = mk((True, 7))
    let _ = swap(w2, (False, 8))
    print(w2.v)
    let w3 = mk(Q { a = 1, b = 2, c = 3, d = 4 })
    let _ = swap(w3, q(10))
    print(sumQ(w3.v))
    let f = fun(k : Int) -> Int = sumQ(w3.v) + k
    print(f(1))
    let xs = [Any { payload = ("p", 1), more = ("m", 2) }, Any { payload = 3, more = ("n", 4) },
              Any { payload = ("x", "y"), more = ("o", 5) }, Any { payload = 2.5, more = ("q", 6) }]
    for x in xs { print(look(x)) }
}
"""


def test_inline_fields_in_wide_objects_sums_and_generic_copies():
    plain = lang.output(WIDE)
    assert plain == '53255\n(7z, 7y, 7x, 7)\na6rst\n5\ncd\n(one, 1)\n(two, 2)\n(False, 8)\n46\n47\n(p, 1)m2\n3n4\n(x, y)o5\n2.5q6\n'
    result = lang.dump("ssa", WIDE)
    assert result.code == 0, result.stderr
    assert re.search(r"object\.new kind=1 tag=\d+ count=81 ", _function(result.stdout, "main"))
    for env in (STRESS, GENERATIONAL):
        result = lang.run(WIDE, env=env, flags=VERIFY)
        assert result.code == 0, result.stderr
        assert result.stdout == plain


# `m` never escapes, so it is built in `f`'s frame, and its inline `var` pairs
# are assigned across calls that allocate and so may collect.
STACKED = """
type M = M { var a : (String, Int), var b : (String, String), c : Int, d : (Int, String) }

fun churn(n : Int) -> Int {
    var k = 0
    for var i = 0; i < n; i = i + 1 { k = k + String.byteLength(Int.toString(i) + "x") }
    k
}

fun f(s : String, i : Int) -> Int {
    let m = M { a = (s + "1", i), b = (s + "2", s + "3"), c = i, d = (i, s + "4") }
    let k = churn(50)
    m.a = (m.b.1 + "!", m.a.1 + k)
    let k2 = churn(50)
    m.b = (m.d.1, s + "5")
    let M { a = (x, n), b = (y, z), c, d = (e, w) } = m
    String.byteLength(x + y + z + w) + n + c + e + k2
}

fun main() {
    var t = 0
    for var i = 0; i < 200; i = i + 1 { t = t + f(Int.toString(i), i) }
    print(t)
}
"""


def test_inline_fields_of_an_object_in_the_frame_survive_collections():
    plain = lang.output(STACKED)
    assert plain == '118660\n'
    result = lang.dump("ssa", STACKED)
    assert result.code == 0, result.stderr
    f = _function(result.stdout, "f")
    assert "stack.object" in f and "object.set" in f, f
    for env in (STRESS, GENERATIONAL):
        result = lang.run(STACKED, env=env, flags=VERIFY)
        assert result.code == 0, result.stderr
        assert result.stdout == plain


# Arrays of flat products, each element held as its slots: two integers,
# three floats, and a traced slot beside an untraced one. Each is built by a
# literal, `Array.init` and `Array.filled`, assigned element by element, and
# moved by `Array.copy`, `Array.fill` and the growth of a `Vec`.
ARRAYS = """
type Vec3 = Vec3 { x : Float, y : Float, z : Float }

fun pairs(n : Int) -> Array (Int, Int) = Array.init(n, fun(i) = (i, i * 10))

fun points(n : Int) -> Array Vec3 =
    Array.init(n, fun(i) = Vec3 { x = Float.fromInt(i), y = 0.5, z = -1.0 })

fun named(n : Int) -> Array (String, Int) = Array.init(n, fun(i) = (Int.toString(i), i))

fun weigh(xs : Array (String, Int)) -> Int {
    var n = 0
    for (s, k) in xs { n = n + String.byteLength(s) + k }
    n
}

fun main() {
    let ps = pairs(6)
    ps[2] = (7, 70)
    Array.copy(ps, 3, [(1, 1), (2, 2)], 0, 2)
    var total = 0
    for (a, b) in ps { total = total + a + b }
    print(total)

    let vs = points(4)
    vs[1] = Vec3 { x = vs[0].x + 2.0, y = vs[3].y, z = 0.25 }
    Array.fill(vs, 2, 2, Vec3 { x = 1.0, y = 1.0, z = 1.0 })
    var sum = 0.0
    for v in vs { sum = sum + v.x + v.y + v.z }
    print(sum)

    let ns = named(500)
    ns[10] = ("ten", -10)
    let grown : Vec (String, Int) = []
    for (s, k) in ns { Vec.push(grown, (s + "!", k + 1)) }
    let kept = Array.filled(3, (Int.toString(total), 1))
    Array.copy(ns, 0, kept, 0, 3)
    print(weigh(ns))
    print(Vec.toArray(grown)[499])
    print(ns[1])
    print([(1.5, 2.5), (3.5, 4.5)][1])
}
"""


def test_an_array_holds_flat_products_as_their_slots():
    plain = lang.output(ARRAYS)
    assert plain == "149\n8.25\n126127\n(499!, 500)\n(149, 1)\n(3.5, 4.5)\n", plain
    result = lang.dump("ssa", ARRAYS)
    assert result.code == 0, result.stderr
    # One allocation for the whole array, and no element boxed.
    for name, layout in [("pairs", r"\[i64, i64\] stride=16 pointers=0"),
                         ("points", r"\[f64, f64, f64\] stride=24 pointers=0"),
                         ("named", r"\[ptr\*, i64\] stride=16 pointers=1")]:
        body = _function(result.stdout, name)
        assert len(re.findall(r"= array\.new ", body)) == 1, body
        assert re.search(r"= array\.new .*" + layout, body), body
        assert "object.new" not in body and "stack.object" not in body, body
    for env in (STRESS, GENERATIONAL):
        result = lang.run(ARRAYS, env=env, flags=VERIFY)
        assert result.code == 0, result.stderr
        assert result.stdout == plain
