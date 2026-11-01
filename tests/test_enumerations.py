"""Enumerations: a sum whose constructors are all nullary is an untraced word.

Every place such a value can be kept -- a record field, a `var` field, a
`Vec` element, an `Option` payload, a tuple, a closure's capture -- is a slot
the collector reads by its layout, so a word there taken for a pointer, or a
pointer taken for a word, shows up under a collection at every allocation with
the heap verified around each, as a verifier failure or as output that
differs from the plain run.
"""

import pytest

from tests import lang

STRESS = {"TURKEY_GC_STRESS": "1"}
GENERATIONAL = {"TURKEY_GC_GENERATIONAL": "1", "TURKEY_GC_STRESS": "1"}
VERIFY = ("--gc-verify",)

KEPT = """
type One = One
type Light = Off | On
type Phase = Idle | Marking | Sweeping | Done

type Cell = Cell { var phase : Phase, light : Light, name : String, one : One }

fun advance(p : Phase) -> Phase = match p {
    Idle -> Marking
    Marking -> Sweeping
    Sweeping -> Done
    Done -> Idle
}

fun weight(p : Phase) -> Int = match p {
    Idle -> 1
    Marking -> 10
    Sweeping -> 100
    Done -> 1000
}

fun flip(l : Light) -> Light = match l {
    Off -> On
    On -> Off
}

fun main() {
    let cells : Vec Cell = []
    let phases : Vec Phase = []
    let maybe : Vec (Option Phase) = []
    let pairs : Vec (Phase, String) = []
    let lights : Vec Light = []
    var light = Off
    var phase = Idle
    for var i = 0; i < 300; i = i + 1 {
        phase = advance(phase)
        light = flip(light)
        let name = Int.toString(i)
        Vec.push(cells, Cell { phase = phase, light = light, name = name, one = One })
        Vec.push(phases, phase)
        Vec.push(maybe, if i % 3 == 0 { None } else { Some(phase) })
        Vec.push(pairs, (phase, name + "!"))
        Vec.push(lights, light)
    }
    for c in cells { c.phase = advance(c.phase) }
    let later = fun(n) = weight(phase) + n
    var total = 0
    for c in cells {
        total = total + weight(c.phase) + String.byteLength(c.name)
        match c.one { One -> { total = total + 1 } }
        match c.light {
            On -> { total = total + 1 }
            Off -> {}
        }
    }
    for p in phases { total = total + weight(p) }
    for m in maybe {
        match m {
            Some(p) -> { total = total + weight(p) }
            None -> { total = total + 7 }
        }
    }
    for (p, s) in pairs { total = total + weight(p) + String.byteLength(s) }
    for l in lights {
        match l {
            On -> { total = total + 3 }
            Off -> {}
        }
    }
    print(total)
    print(later(1))
    print(match light {
        On -> "on"
        Off -> "off"
    })
}
"""


def test_enumerations_kept_everywhere_survive_a_collection_at_every_allocation():
    plain = lang.output(KEPT)
    assert plain.strip(), "the program printed nothing"
    for env in (STRESS, GENERATIONAL):
        result = lang.run(KEPT, env=env, flags=VERIFY)
        assert result.code == 0, result.stderr
        assert result.stdout == plain


@pytest.mark.parametrize("src, expected", [
    # A one-constructor enumeration is held at no width at all.
    ("type One = One\n"
     "fun same(a : One, b : One) -> Bool = match (a, b) { (One, One) -> True }\n"
     "fun main() { print(same(One, One)) }\n", "True\n"),
    # Two constructors is one bit, by the same rule as `Bool`.
    ("type Light = Off | On\n"
     "fun show(l : Light) -> String = match l { Off -> \"off\"\n On -> \"on\" }\n"
     "fun main() { print(show(On) + show(Off)) }\n", "onoff\n"),
    # A wildcard after some constructors is the switch's default.
    ("type Phase = Idle | Marking | Sweeping | Done\n"
     "fun busy(p : Phase) -> Bool = match p { Idle -> False\n Done -> False\n _ -> True }\n"
     "fun main() { print([busy(Idle), busy(Marking), busy(Sweeping), busy(Done)]) }\n",
     "[False, True, True, False]\n"),
])
def test_an_enumeration_matches_by_its_index(src, expected):
    assert lang.output(src) == expected
