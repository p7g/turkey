"""`Float.toString` and `Float.parse`, diffed against Python's.

A formatter and a parser written by the same hand can agree with each other
and both be wrong: round-tripping one through the other proves the pair is
consistent, not that either is correct. So neither is checked against the
other here. Python's `repr(float)` is the shortest string that reads back
correctly and `float(str)` is correctly rounded (both are David Gay's
`dtoa.c`), and they are the oracle: one Turkey program reads cases on stdin and
answers each, and every answer is compared with Python's.

The cases are the ones a hand-picked list misses: every power of two and its
neighbours, so every binade is visited at its edges; a seeded sweep over bit
patterns; exact halfway points between adjacent floats and decimals a hair
either side of them, which are where correct rounding is decided; strings
longer than any buffer; and every float literal in the repository.
"""

from __future__ import annotations

import math
import random
import re
import struct
from decimal import Decimal, getcontext
from pathlib import Path

from tests.lang import REPO_ROOT, run

PROGRAM = """\
import System.IO as IO

fun main() {
    let text = match IO.readFile("/dev/stdin") {
        Some(t) -> t
        None -> ""
    }
    for line in String.lines(text) {
        match String.splitOnce(line, " ") {
            Some(("f", bits)) -> match Int.parse(bits) {
                Some(n) -> print(Float.toString(Float.fromBits(n)))
                None -> print("bad case")
            }
            Some(("p", s)) -> match Float.parse(s) {
                Some(x) -> print(Float.bits(x))
                None -> print("None")
            }
            _ -> print("bad case")
        }
    }
}
"""

SEED = 75


def bits_of(x: float) -> int:
    return struct.unpack("<q", struct.pack("<d", x))[0]


def float_of(bits: int) -> float:
    return struct.unpack("<d", struct.pack("<q", bits))[0]


def shown(x: float) -> str:
    """What `Show Float` prints: `repr`, with the specials spelled out and a
    `.0` wherever `repr` leaves the mantissa without a point."""
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    mantissa, e, exponent = repr(x).partition("e")
    if "." not in mantissa:
        mantissa += ".0"
    return mantissa + e + exponent


def answers(cases: list[str]) -> list[str]:
    result = run(PROGRAM, stdin="".join(case + "\n" for case in cases))
    assert result.code == 0, result.stderr
    return result.stdout.splitlines()


def diff(cases: list[str], expected: list[str]) -> None:
    got = answers(cases)
    assert len(got) == len(cases), f"{len(got)} answers to {len(cases)} cases"
    wrong = [f"{case!r}: expected {want}, got {have}"
             for case, want, have in zip(cases, expected, got) if want != have]
    assert not wrong, f"{len(wrong)} of {len(cases)} wrong:\n" + "\n".join(wrong[:20])


# ----------------------------------------------------------------- formatting


def edge_floats() -> list[float]:
    """Every power of two from the smallest subnormal to the largest binade,
    each with its neighbours, plus the named extremes."""
    xs = [0.0, 5e-324, 2.2250738585072009e-308, 2.2250738585072014e-308,
          1.7976931348623157e308, 2.0 ** 53 - 1, 2.0 ** 53 + 2, 0.1, 0.2, 0.3]
    for k in range(-1074, 1024):
        p = math.ldexp(1.0, k)
        xs += [p, math.nextafter(p, 0.0), math.nextafter(p, math.inf)]
    # Powers of ten, where the digit count changes and the fixed/exponent
    # boundary sits.
    for k in range(-325, 309):
        p = float(f"1e{k}")
        xs += [p, math.nextafter(p, 0.0), math.nextafter(p, math.inf)]
    return xs


def test_formatting_matches_repr_at_every_binade_edge() -> None:
    xs = edge_floats()
    xs += [-x for x in xs]
    xs += [math.inf, -math.inf, math.nan, -0.0]
    diff([f"f {bits_of(x)}" for x in xs], [shown(x) for x in xs])


def test_formatting_matches_repr_over_random_bit_patterns() -> None:
    rng = random.Random(SEED)
    patterns = [rng.getrandbits(64) - 2 ** 63 for _ in range(100000)]
    # A NaN payload and sign do not show: every NaN prints `NaN`.
    patterns += [bits_of(math.inf) | 1, -1]
    diff([f"f {n}" for n in patterns], [shown(float_of(n)) for n in patterns])


# -------------------------------------------------------------------- parsing


def parsed(s: str) -> str:
    return str(bits_of(float(s)))


def as_literal(d: Decimal) -> str:
    """An exact decimal in the syntax `Float.parse` reads, which always has a
    point with a digit either side of it."""
    text = format(d, "f")
    if "." not in text:
        text += ".0"
    return text


def halfway_cases() -> list[str]:
    """The exact midpoint between two adjacent floats -- which must round to
    the even one -- and decimals just above and below it, which must not."""
    getcontext().prec = 2000
    rng = random.Random(SEED)
    lows = [math.ldexp(1.0, k) for k in (-1074, -1073, -1022, -1, 0, 52, 53, 1023)]
    lows += [float_of(rng.getrandbits(62)) for _ in range(300)]
    lows += [rng.uniform(1.0, 1e6) for _ in range(100)]
    out = []
    for low in lows:
        high = math.nextafter(low, math.inf)
        if math.isinf(high) or math.isnan(low):
            continue
        mid = (Decimal(low) + Decimal(high)) / 2
        nudge = Decimal(high - low) / Decimal(10) ** 30
        out += [as_literal(mid), as_literal(mid + nudge), as_literal(mid - nudge)]
    # Past the end: halfway between the largest finite value and the next
    # power of two rounds to infinity, and just under it does not.
    top = Decimal(1.7976931348623157e308) + Decimal(2) ** 970
    out += [as_literal(top), as_literal(top - 1)]
    return out


def random_decimals() -> list[str]:
    rng = random.Random(SEED)
    out = []
    for _ in range(5000):
        whole = str(rng.randrange(10 ** rng.randrange(1, 12)))
        fraction = "".join(rng.choice("0123456789")
                           for _ in range(rng.randrange(1, 25)))
        text = f"{rng.choice(['', '-', '+'])}{whole}.{fraction}"
        if rng.random() < 0.7:
            text += f"{rng.choice('eE')}{rng.choice(['', '-', '+'])}{rng.randrange(0, 350)}"
        out.append(text)
    # What `toString` prints, read back.
    xs = (float_of(rng.getrandbits(63)) for _ in range(3000))
    out += [shown(x) for x in xs if math.isfinite(x)]
    return out


def literal_floats() -> list[str]:
    """Every float literal in the corpus, the reference and the compiler."""
    pattern = re.compile(r"(?<![\w.])\d+\.\d+(?:[eE][+-]?\d+)?")
    found = set()
    for root in ("tests/programs", "docs/ref", "src", "lib", "examples"):
        for path in Path(REPO_ROOT, root).rglob("*"):
            if path.suffix in (".gob", ".md"):
                found.update(pattern.findall(path.read_text()))
    return sorted(found)


def test_parsing_is_correctly_rounded() -> None:
    cases = halfway_cases() + random_decimals() + literal_floats()
    cases += ["0.0", "-0.0", "4.9e-324", "2.4703282292062327e-324",
              "2.4703282292062328e-324", "1.8e308", "1.0e-400", "1.0e400",
              "0.000000000000000000000000000001", "00012.50",
              "1.0e99999999999999999999", "1.0e-99999999999999999999",
              # Longer than the 800 digits held: the digits past that point
              # still decide a tie.
              as_literal(Decimal(5e-324) / 2) + "0" * 900 + "1",
              as_literal(Decimal(5e-324) / 2) + "0" * 900]
    diff([f"p {s}" for s in cases], [parsed(s) for s in cases])


def test_parsing_accepts_the_specials_and_nothing_but_the_syntax() -> None:
    got = answers(["p NaN", "p Infinity", "p -Infinity"])
    assert math.isnan(float_of(int(got[0])))
    assert [float_of(int(g)) for g in got[1:]] == [math.inf, -math.inf]

    rejected = ["", "1", "-1", ".5", "1.", "1.e5", "1.5e", "1.5e+", "+", "-",
                "nan", "inf", "+Infinity", "-NaN", "1.5 ", " 1.5", "1.5x",
                "1_0.0", "0x1.0", "1.5e5.0", "1.2.3", "--1.0", "1.0ee5"]
    assert answers([f"p {s}" for s in rejected]) == ["None"] * len(rejected)
