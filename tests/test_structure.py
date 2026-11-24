"""`src/Turkey/Wasm/Structure.gob`: a control-flow graph as wasm's structured
control flow.

`src/StructureCases.gob` builds graphs by hand, structures each, and prints
the result with two checks: that every reachable block is placed exactly
once, and that the graph and the structured body, run side by side on the
same decisions, enter the same blocks and end the same way, over runs that
between them take every edge. Every case must pass both; a few shapes are
also pinned exactly, where the shape is the point.
"""

import functools
from pathlib import Path

import pytest

from tests import lang

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "src" / "StructureCases.gob"

STRUCTURED = [
    "straight line",
    "diamond",
    "diamond without a join",
    "both arms to one block",
    "two merge nodes",
    "self loop",
    "while loop",
    "nested loops with break and continue",
    "early return from a loop",
    "loop after a join",
    "dense switch",
    "sparse switch",
    "switch with no default",
    "switch in a loop",
    "an unreachable block",
]


@functools.lru_cache(maxsize=1)
def _cases() -> dict[str, list[str]]:
    """The driver's output, split into each case's lines."""
    cases: dict[str, list[str]] = {}
    name = None
    for line in lang.output(DRIVER).splitlines():
        if line.startswith("-- case "):
            name = line[len("-- case "):]
            cases[name] = []
        else:
            assert name is not None, line
            cases[name].append(line)
    return cases


def _body(name: str) -> str:
    """The structured body a case printed, without its check lines."""
    return "\n".join(line for line in _cases()[name]
                     if not line.startswith(("static: ", "runs: ")))


def test_every_case_is_printed():
    assert sorted(_cases()) == sorted(STRUCTURED + ["irreducible"])


@pytest.mark.parametrize("name", STRUCTURED)
def test_every_block_is_placed_once(name):
    assert "static: ok" in _cases()[name]


@pytest.mark.parametrize("name", STRUCTURED)
def test_the_body_runs_as_the_graph_does(name):
    assert "runs: 64 agree, every edge taken" in _cases()[name]


@pytest.mark.parametrize("name", STRUCTURED)
def test_every_graph_is_well_formed(name):
    assert not [line for line in _cases()[name] if line.startswith("malformed graph")]


def test_an_irreducible_loop_is_rejected_by_name():
    assert _cases()["irreducible"] == [
        "internal error: 'irreducible' has a loop with more than one entry, "
        "which wasm's structured control flow cannot express"]


def test_a_diamond_needs_no_block_and_no_br():
    """The join follows the `if`, and both arms fall through to it."""
    assert _body("diamond") == "\n".join([
        "b0",
        "if %1",
        "  b1",
        "else",
        "  b2",
        "end",
        "b3",
        "return %2",
    ])


def test_merge_nodes_nest_by_reverse_postorder():
    """b4 branches to b5, so the `if` standing in for b4's block sits inside
    b5's block; and the branches to b4 fall through to it."""
    assert _body("two merge nodes") == "\n".join([
        "block",
        "  b0",
        "  if %1",
        "    b1",
        "    if %3",
        "      b3",
        "      br 2",
        "    end",
        "  else",
        "    b2",
        "    if %5",
        "    else",
        "      br 2",
        "    end",
        "  end",
        "  b4",
        "end",
        "b5",
        "return %6",
    ])


def test_break_and_continue_are_branches_by_depth():
    assert _body("nested loops with break and continue") == "\n".join([
        "b0",
        "loop",
        "  b1",
        "  if %1",
        "    b2",
        "    loop",
        "      b3",
        "      if %3",
        "        b4",
        "        if %5",
        "          b5",
        "          if %7",
        "            br 5",
        "          else",
        "            br 3",
        "          end",
        "        end",
        "      end",
        "      b6",
        "      br 2",
        "    end",
        "  else",
        "    b7",
        "    return %8",
        "  end",
        "end",
    ])


def test_a_dense_switch_is_a_table_with_gaps_to_the_default():
    assert "br_table %0 - 0 [2 1 0 1 3 2] 3" in _body("dense switch")


def test_a_sparse_switch_tests_each_key():
    assert "br_cases %0 [1 -> 2, 100 -> 1, 10000 -> 0] 3" in _body("sparse switch")


def test_a_switch_with_no_default_lets_its_last_key_stand_in_for_one():
    assert "br_table %0 - 7 [1 0 2] 1" in _body("switch with no default")


def test_a_switch_can_continue_its_loop():
    """Key 0 goes back to the loop's header: depth 3 is the `loop`."""
    assert "br_table %0 - 0 [3 2 1] 0" in _body("switch in a loop")


def test_an_unreachable_block_is_not_placed():
    assert "b3" not in _body("an unreachable block").split()
