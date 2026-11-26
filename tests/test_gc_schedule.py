"""The collector's schedule under a memory target: TURKEY_GC_TARGET, the full
collection it makes due, and the guard that lets the heap pass a target set
too low. Each program is built with `--gc-stats`, whose line per collection
gives the next threshold, the bytes the regions hold, and whether the guard is
on and the next collection full."""

import re

import pytest

from tests import lang

# A list kept live while lists as long are built and dropped: each lives
# across several collections, so it is promoted and dies old, which only a
# full collection frees.
KEPT_UNDER_CHURN = """\
import System.Env as Env

type Chain = Link(Int, Chain) | End

fun build(n : Int, acc : Chain) -> Chain = if n == 0 { acc } else { build(n - 1, Link(n, acc)) }

fun total(xs : Chain, acc : Int) -> Int = match xs {
    End -> acc
    Link(x, rest) -> total(rest, acc + x)
}

fun main() {
    let n = match Int.parse(Env.args()[0]) {
        Some(n) -> n
        None -> 0
    }
    let rounds = match Int.parse(Env.args()[1]) {
        Some(n) -> n
        None -> 0
    }
    let kept = build(n, End)
    var sum = 0
    for var round = 0; round < rounds; round = round + 1 { sum = sum + total(build(n, End), 0) }
    print(sum + total(kept, 0))
}
"""

LINE = re.compile(r"^\[gc \d+ (minor|full)\].* next threshold (\d+), held (\d+)(.*)$", re.M)


def run(n, rounds, target=None, wasm=False, env=()):
    env = {"TURKEY_GC_STATS": "1", "TURKEY_GC_THRESHOLD_SCALE": "2", **dict(env)}
    if target is not None:
        env["TURKEY_GC_TARGET"] = target
    result = lang.run(KEPT_UNDER_CHURN, args=(str(n), str(rounds)), env=env,
                      flags=("--gc-stats",),
                      target="wasm32-wasi" if wasm else "host")
    assert result.code == 0, result.stderr
    assert result.stdout == f"{n * (n + 1) // 2 * (rounds + 1)}\n"
    return LINE.findall(result.stderr)


def fulls(lines):
    return sum(kind == "full" for kind, _, _, _ in lines)


def assert_capped(lines, target):
    """Every budget the target set is at most the room under it or the
    floor: a twentieth of the live bytes, which the regions hold, or 128KB
    at a scale of 2."""
    assert lines
    for _, threshold, held, rest in lines:
        if "guard" in rest:
            continue
        held = int(held)
        assert int(threshold) <= max(target - held, held // 20, 131072), (threshold, held)


@pytest.mark.parametrize("target", ["junk", "-5", "0", " 1e6"])
def test_a_target_that_is_not_a_byte_count_sets_none(target):
    lines = run(20000, 20, target)
    assert lines
    assert not any("next full" in rest for _, _, _, rest in lines)


def test_a_target_too_large_to_read_saturates_past_any_heap():
    """23 digits read as 2^60 bytes, which no heap comes near: as good as
    no target, and no overflow."""
    lines = run(20000, 20, "99999999999999999999999")
    assert lines
    assert not any("next full" in rest for _, _, _, rest in lines)


def test_no_target_makes_no_collection_full_for_room():
    lines = run(20000, 20)
    assert not any("next full" in rest for _, _, _, rest in lines)


def test_a_target_caps_the_budget_at_the_room_under_it():
    assert_capped(run(100000, 40, "8000000"), 8_000_000)


def test_a_target_under_the_live_set_makes_more_collections_full():
    lines = run(100000, 40, "1000000")
    assert any("next full" in rest for _, _, _, rest in lines)
    assert fulls(lines) > fulls(run(100000, 40))


def test_the_guard_lets_the_heap_pass_a_target_set_too_low():
    """Under a 1 MB target with 3 MB live and old garbage to free, every
    other collection is full and pauses for longer than the program runs
    between them. A one-nanosecond bucket fills at the first of those, and
    the guard is on, without a second of collecting first."""
    lines = run(100000, 40, "1000000", env={"TURKEY_GC_GUARD_NANOSECONDS": "1"})
    assert any("guard" in rest for _, _, _, rest in lines)


def test_a_target_on_wasm32_caps_the_budget_and_makes_collections_full():
    lines = run(50000, 20, "1000000", wasm=True)
    assert any("next full" in rest for _, _, _, rest in lines)
    assert_capped(lines, 1_000_000)
