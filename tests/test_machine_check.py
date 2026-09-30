"""`src/Turkey/MachineCheck.gob`: representations, checked on arm64 output.

Below the low IR's verifiers there is no type system: a pointer and an integer
are the same bits, and a register a callee never wrote reads as whatever it
last held. `src/MachineCheckCases.gob` builds machine functions by hand, one
well-formed and the rest each carrying one representation bug, and prints what
the check says about each.

The first three are representation bugs that got past every verifier above
this one, and are kept as regression tests for it.
"""

import functools
from pathlib import Path

from tests import lang

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "src" / "MachineCheckCases.gob"


@functools.lru_cache(maxsize=1)
def _dump() -> str:
    return lang.output(DRIVER)


def _case(name: str) -> list[str]:
    """The complaints printed under one case: its count line, then its
    indented messages."""
    lines = _dump().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(name + ": "))
    count = int(lines[start].rsplit(": ", 1)[1])
    messages = lines[start + 1:start + 1 + count]
    assert all(m.startswith("  ") for m in messages), lines[start:start + 1 + count]
    return [m.strip() for m in messages]


def test_a_well_formed_function_passes():
    assert _case("well formed") == []


def test_the_void_entry_points_are_known():
    assert "exit is void: True" in _dump()


def test_a_ground_call_left_on_the_generic_copy_is_refused():
    """A ground type application inside a generic body that monomorphization
    skipped: the caller asks for an `Int` and calls the copy that answers the
    uniform representation."""
    [message] = _case("a ground call left on the generic copy")
    assert "reads i64 from" in message and "which answers ptr*" in message


def test_a_scalar_constant_at_a_pointer_representation_is_refused():
    """The collector would follow 65536 as an address. Null and the words
    below 4096 are skipped -- a compact sum's nullaries live there -- and the
    well-formed case holds one of each. A small scalar in a traced register is
    therefore indistinguishable from a nullary, and this check cannot refuse
    one."""
    [message] = _case("a scalar constant at a pointer representation")
    assert "the constant 65536 is held at ptr*" in message


def test_a_void_calls_result_read_as_a_pointer_is_refused():
    """`System.Env.exit` returns any type and `turkey_exit` returns nothing,
    so at a pointer-shaped specialization the caller would root whatever `x0`
    last held."""
    [message] = _case("a void call's result read as a pointer")
    assert "reads ptr* from" in message and "which returns void" in message


def test_an_argument_at_the_wrong_representation_is_refused():
    [message] = _case("an integer passed where a pointer is taken")
    assert "passes i64 to" in message and "which is ptr*" in message


def test_a_double_in_the_general_file_is_refused():
    [message] = _case("a double in the general file")
    assert "f64, not in the general file" in message


def test_a_narrow_access_to_a_wide_value_is_refused():
    [message] = _case("a 32-bit load of a 64-bit value")
    assert "i64 at 4 bytes" in message


def test_a_wide_access_to_packed_memory_is_refused():
    [message] = _case("a word read from a byte array")
    assert "i8 at 8 bytes through an untraced address" in message


def test_an_integer_moved_into_a_traced_register_is_refused():
    [message] = _case("an integer moved into a traced register")
    assert "moves i64 into ptr*" in message


def test_exit_at_a_pointer_type_compiles_and_exits():
    """The void call, from source. `exit` at `String` once read `x0` after
    `turkey_exit` into a traced register; the check refuses that, so this
    compiling at all is the regression test, and the status is the behavior."""
    result = lang.run(
        "import System.Env as Env\n"
        "\n"
        "fun name(n : Int) -> String = if n > 0 { \"many\" } else { Env.exit(3) }\n"
        "\n"
        "fun main() {\n"
        "    print(name(1))\n"
        "    print(name(0))\n"
        "}\n")
    assert result.stdout == "many\n"
    assert result.code == 3
