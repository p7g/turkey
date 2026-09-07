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

import functools
from pathlib import Path

import pytest

from tests import bootc

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOT_MAIN = REPO_ROOT / "boot" / "Main.tl"
PROGRAMS = REPO_ROOT / "tests" / "programs"

# Small, and between them they reach an ordinary function, a loop, a
# user-defined type, and -- `generalization.tl` -- a lambda that survives
# `opt` plus a top-level function used as a value, which are the two cases
# closure conversion exists for. A sample rather than the corpus because these
# assertions are about *shape*; `test_boot` is what runs the whole corpus.
SAMPLE = ["adt.tl", "loops.tl", "stack.tl", "generalization.tl"]


@functools.lru_cache(maxsize=None)
def _all() -> dict[str, str]:
    """`boot ssa` over every sample program, in **one** invocation.

    One process for every program, and a compiled `boot` rather than an
    interpreted one -- both from `tests.bootc`, which explains why. The dump
    has no separator between programs, by design: every one ends with its own
    count line, so splitting on that line recovers them.
    """
    text = bootc.boot("ssa", *(str(PROGRAMS / name) for name in SAMPLE))
    chunks = bootc.split_on(text, "-- lowered")
    assert len(chunks) == len(SAMPLE), (
        f"{len(chunks)} dumps for {len(SAMPLE)} programs:\n{text[-2000:]}")
    return dict(zip(SAMPLE, chunks))


def _ssa(name: str) -> str:
    return _all()[name]


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
def test_nothing_is_skipped(name):
    """The histogram is empty, which is what finishes M27 phase 1.

    Every Core form these programs contain reaches the low IR: the function
    bodies, the lambdas closure conversion lifts out of them, and the globals
    the module initializer computes. This is the coverage ratchet -- it was
    "every reason is `not a function`" while globals were unhandled, and a new
    unhandled form is now a failing test rather than a line in a dump nobody
    reads.
    """
    out = _ssa(name)
    reasons = [line for line in out.splitlines() if line.startswith("--   ")]
    assert not reasons, reasons
    counted = [line for line in out.splitlines() if line.startswith("-- lowered")]
    assert len(counted) == 1
    assert counted[0].endswith("0 skipped"), counted


@pytest.mark.parametrize("name", SAMPLE)
def test_the_globals_are_declared_and_computed(name):
    """Storage with a representation, and one function that fills it.

    A global's representation is not derivable at a use site, and both the
    initializer's store and every load need it -- so the module carries it
    rather than implying it.
    """
    out = _ssa(name)
    assert "global $" in out
    assert "fun @%module.initialize()" in out
    assert "global.store $" in out


def test_a_dictionary_is_allocated_before_its_fields():
    """The two-phase initializer, which is why it is two phases.

    A dictionary's fields are the instance's methods and a method mentions the
    dictionary it belongs to, so building the record in one step would need
    its own address before it had one. Every record-shaped dictionary is
    therefore allocated and published first and filled afterwards.
    """
    out = _ssa("adt.tl")
    body = out.split("fun @%module.initialize()")[1].split("\nfun ")[0]
    # The property per dictionary, rather than a global ordering of opcodes:
    # a method's own body allocates objects too, so counting `object.new`
    # across the whole initializer says nothing. What must hold is that the
    # store publishing a dictionary comes before the load that fills it.
    stores, loads = {}, {}
    for index, line in enumerate(body.splitlines()):
        for op, seen in (("global.store $", stores), ("global.load $", loads)):
            if op in line:
                name = line.split(op, 1)[1].split(",")[0].strip()
                seen.setdefault(name, index)
    filled = [name for name in loads if name in stores]
    assert filled, body[:400]
    for name in filled:
        assert stores[name] < loads[name], (
            f"{name} was read back before it was published")


def test_a_lambda_becomes_a_lifted_function_and_a_closure():
    out = _ssa("generalization.tl")
    assert "closure.new @" in out
    assert "%lambda" in out
    # The environment is the leading parameter, and every user parameter and
    # the result are boxed -- one code pointer is reached from call sites at
    # many types.
    lifted = [line for line in out.splitlines()
              if line.startswith("fun @") and "%lambda" in line]
    assert lifted, out
    for line in lifted:
        assert "-> ptr*" in line, line


def test_a_function_used_as_a_value_gets_a_boxing_adapter():
    """Not a `GlobalLoad`, which would load a code address.

    A closure's code is called at the uniform representation and a top-level
    function's parameters are natural, so something has to convert. That
    something is an ordinary function in this IR rather than an opcode every
    emitter expands for itself.
    """
    out = _ssa("generalization.tl")
    assert "%closure" in out
    adapters = [line for line in out.splitlines()
                if line.startswith("fun @") and "%closure" in line]
    assert adapters, out


def test_the_calling_conventions_are_not_confused():
    """A cross-function check, because the per-function verifier cannot see it.

    A lifted function taking three parameters and an indirect call passing two
    are both well-formed graphs; so is a direct call whose arguments disagree
    with the callee's signature, and a `ClosureNew` naming a symbol nobody
    emitted. Those are exactly what closure conversion introduces.
    """
    for name in SAMPLE:
        out = _ssa(name)
        for phrase in ("which is not a function here", "does not exist",
                       "which takes no environment", "arguments where it takes"):
            assert phrase not in out, (name, phrase)
