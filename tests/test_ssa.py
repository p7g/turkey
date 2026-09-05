"""`boot/Turkey/Ssa.tl` and `Turkey/LowIr.tl`: the low IR.

M27 phase 0. Nothing imports the module yet -- the lowering into it is the
next phase -- so this is what type-checks it and what exercises the analyses.
`boot/SsaCheck.tl` is the driver; see its header for why it lives there.

Run through the Python implementation, which is the host `boot` is compiled by
today. That is the point at which this is a test of the Turkey code rather
than of the two implementations agreeing, which is `test_boot`'s job.
"""

from pathlib import Path

from turkey.driver import run

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "boot" / "SsaCheck.tl"


def _output(capfd) -> str:
    run(DRIVER.read_text(encoding="utf-8"), str(DRIVER))
    return capfd.readouterr().out


def test_the_module_type_checks_and_the_analyses_run(capfd):
    assert _output(capfd)


def test_the_printer_shows_blocks_parameters_and_reps(capfd):
    out = _output(capfd)
    # A block parameter is where a phi would be, and a jump carries the
    # argument for it. Both have to survive printing to be debuggable.
    assert "b3(%4:i64):" in out
    assert "jump b3(%2:i64)" in out
    assert "branch %1:i1, b1, b2" in out
    # Instructions print through the class, so the generic printer needs no
    # knowledge of the instruction type.
    assert "%1 = eq %0, %0" in out
    assert "%3 = add %0, %0" in out


def test_a_diamonds_merge_is_dominated_by_the_split_and_neither_arm(capfd):
    out = _output(capfd)
    # Cooper-Harvey-Kennedy on the smallest graph with a real answer: every
    # block's immediate dominator is the entry, including the merge.
    assert "idom: 0 0 0 0" in out
    assert "b0 dom b3: yes" in out
    assert "b1 dom b3: no" in out
    # Dominance is reflexive, which the verifier relies on for a use in the
    # block that defines it.
    assert "b1 dom b1: yes" in out


def test_a_well_formed_function_verifies(capfd):
    assert "verify: 0" in _output(capfd)


def test_a_use_its_definition_does_not_dominate_is_refused(capfd):
    """The check the Python backend's block-local SSA rule cannot make.

    There, a value crossing an edge goes through a slot and the question never
    arises; here a definition in one arm of a diamond used from the other arm
    is exactly what function-wide SSA exists to reject.
    """
    out = _output(capfd)
    assert "verify after breaking it: 1" in out
    assert "used outside the blocks its definition dominates" in out


def test_the_two_verifiers_stay_apart(capfd):
    """`Ssa.verify` owns the graph; `LowIr.checkReps` owns the opcodes.

    Keeping them separate is what stops the generic layer from accumulating
    target knowledge, so the representation complaint has to come from the
    other one and only when something is actually wrong.
    """
    out = _output(capfd)
    assert "reps: 0" in out
    assert "reps after breaking it: 1" in out
    assert "the operands of a comparison are i64 and i1" in out


def test_an_operand_count_cannot_be_wrong(capfd):
    """There is no arity check because there cannot be one.

    Operands live inside the opcode constructor, so `Bin(Add, x, y)` has two
    by construction. The class of malformed IR a signature check would catch
    is unrepresentable, which is the concrete payoff of writing the
    instruction set as an ADT rather than an opcode string and a list.
    """
    source = (REPO_ROOT / "boot" / "Turkey" / "LowIr.tl").read_text()
    assert "Bin(BinOp, Value, Value)" in source
    assert "ArraySet(Value, Value, Value)" in source


def test_trapping_is_not_purity(capfd):
    """`add` overflows and panics, so an unused one may not be deleted.

    The distinction `traps` draws is one the effects lattice did not have
    until the instruction set was written against it: `diverges` means never
    returns, and this means may not return.
    """
    out = _output(capfd)
    assert "add deletable: no" in out
    assert "and deletable: yes" in out


def test_allocation_is_what_makes_a_safepoint(capfd):
    """Nothing reads this until the register allocator exists.

    It is in the IR from the first commit because retrofitting precise stack
    maps into a backend that did not plan for them is the rewrite this design
    is avoiding (FINDINGS 55).
    """
    out = _output(capfd)
    assert "object.new is a safepoint: yes" in out
    assert "array.get is a safepoint: no" in out
