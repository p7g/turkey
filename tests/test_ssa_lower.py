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
