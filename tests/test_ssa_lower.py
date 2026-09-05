"""`boot/Turkey/SsaLower.tl`: Core to the low IR.

M27 phase 1, and incomplete on purpose. A Core form nothing handles yet stops
one binding rather than the run, and `boot ssa` reports the count -- which is
the progress signal for building this in slices against real programs.

What is asserted is not how *much* lowers. It is that everything which does is
well formed: `Turkey.Ssa.verify` runs over every lowered function and the dump
carries a `!!` line per complaint. There is no byte-identical oracle below Core
and there should not be -- diffing this against a JIT's IR would couple it to
the artifact it deliberately does not copy -- so the verifier is what stands in
until `boot ssa` can produce code that runs (`NATIVE-BACKEND.md`).
"""

import contextlib
import functools
import io
from pathlib import Path

import pytest

from turkey.driver import run

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOT_MAIN = REPO_ROOT / "boot" / "Main.tl"
PROGRAMS = REPO_ROOT / "tests" / "programs"

# Small, and between them they reach an ordinary function, a loop and a
# user-defined type. Running `boot` under the Python implementation costs
# seconds per program, so this is a sample rather than the corpus; the corpus
# is what `boot ssa` is for once it compiles.
SAMPLE = ["adt.tl", "loops.tl", "stack.tl"]


@functools.lru_cache(maxsize=None)
def _ssa(name: str) -> str:
    """`boot ssa <program>`, once per program.

    Running `boot` under the Python implementation costs about two minutes,
    so this is cached across the tests in the module for the same reason
    `test_boot` builds one binary rather than ten.
    """
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        run(BOOT_MAIN.read_text(encoding="utf-8"), str(BOOT_MAIN),
            ["ssa", str(PROGRAMS / name)])
    return out.getvalue()


@pytest.mark.parametrize("name", SAMPLE)
def test_everything_that_lowers_is_well_formed(name):
    """The verifier after every pass, doing the job it exists for.

    A dominance violation, a jump whose arity or representation disagrees with
    its target, a branch on something that is not an `i1` -- each would be a
    `!!` line naming the function that produced it.
    """
    out = _ssa(name)
    complaints = [line for line in out.splitlines() if "!!" in line]
    assert not complaints, complaints


@pytest.mark.parametrize("name", SAMPLE)
def test_something_lowers(name):
    out = _ssa(name)
    lowered = [line for line in out.splitlines() if line.startswith("-- lowered")]
    assert len(lowered) == 1
    count = int(lowered[0].split()[2])
    assert count > 0, out


def test_the_dump_is_the_low_ir():
    """Blocks, block parameters, representations and the instruction set."""
    out = _ssa("adt.tl")
    assert "fun @" in out
    assert "; entry:" in out
    assert "ret %" in out
    # A traced pointer prints with a star, which is the collector's obligation
    # and the distinction `ptr`/`boxed` could not make while also answering a
    # register class.
    assert ":ptr*" in out


def test_a_direct_call_names_its_callee():
    """`Direct` and `Indirect` are different constructors, not a flag.

    A call to a known top-level binding is a symbol; a call to a value is
    through a closure. Instruction selection matches on which.
    """
    out = _ssa("adt.tl")
    assert "call @" in out


def test_a_pattern_match_becomes_a_tag_test_and_a_branch():
    """`CMatch` lowered: the tag, the comparison, the branch, the failure arm.

    A single-variant type has nothing to distinguish and gets no tag test --
    that case is `test_a_single_variant_pattern_reads_no_tag` -- so this asks
    for the multi-variant one, which `adt.tl` has.
    """
    out = _ssa("adt.tl")
    assert "object.tag " in out
    # Exhaustiveness has already decided this arm is unreachable. It is
    # emitted anyway so that a bug in an earlier pass stops with a name.
    assert 'panic' in out
    assert 'no match arm applied' in out


def test_a_single_variant_pattern_reads_no_tag():
    """`Array a = Array(ArrayStorage a)` must not pay a tag test per access.

    Reading the tag to compare it against the only value it can hold is a
    load, a compare and a branch on the hot path, and the arm it branches to
    is unreachable.
    """
    out = _ssa("stack.tl")
    body = out.split("fun @Data.Array#state@Int")[1].split("\nfun ")[0]
    assert "object.tag" not in body, body


def test_the_environment_is_scoped():
    """Two bindings of one name must not become one value.

    Core names are not unique -- monomorphization and inlining copy bodies --
    so an environment that only ever grows makes the second `i` in a function
    read the first one's value. That is a capture, and it reaches the verifier
    as a dominance violation rather than as a wrong answer.
    """
    for name in SAMPLE:
        out = _ssa(name)
        assert "used outside the blocks its definition dominates" not in out


def test_representations_are_converted_explicitly():
    """A value crossing between a generic context and a concrete one.

    Parametricity is what creates these: a polymorphic body holds values at
    the uniform representation, so a value whose type is a variable arrives
    boxed where a concrete one is required. Every conversion is an
    instruction rather than a `Rep` mismatch later passes must notice.
    """
    out = "".join(_ssa(name) for name in SAMPLE)
    assert "box %" in out


def test_the_array_primitives_are_instructions():
    """Not runtime calls: the element representation decides the stride.

    That is a fact instruction selection must see rather than one buried in a
    callee, which is why `NATIVE-BACKEND.md` puts the heap operations in the
    opcode and leaves the rest as symbols.
    """
    out = _ssa("stack.tl")
    assert "array.get " in out
    assert "array.new " in out
    assert "array.length " in out


@pytest.mark.parametrize("name", SAMPLE)
def test_only_whole_globals_remain(name):
    """The lowering reports why, and by now the reasons are down to one.

    A global whose value the module initializer computes is not a function and
    is its own phase; every Core form these programs contain otherwise
    lowers. This is the coverage signal, asserted so it cannot quietly regress.
    """
    out = _ssa(name)
    reasons = [line for line in out.splitlines()
               if line.startswith("--   ")]
    assert reasons, out
    assert all("not a function" in line for line in reasons), reasons
