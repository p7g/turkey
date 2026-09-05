"""`boot/Turkey/Ssa.tl`: the low IR's graph, dominance and verifier.

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
    assert "b3(%3:i64):" in out
    assert "jump b3(%1:i64)" in out
    assert "branch %0:i1, b1, b2" in out


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
    assert "verify (expect none): 0" in _output(capfd)


def test_a_use_its_definition_does_not_dominate_is_refused(capfd):
    """The check the Python backend's block-local SSA rule cannot make.

    There, a value crossing an edge goes through a slot and the question never
    arises; here a definition in one arm of a diamond used from the other arm
    is exactly what function-wide SSA exists to reject.
    """
    out = _output(capfd)
    assert "verify (expect a dominance complaint): 1" in out
    assert "used outside the blocks its definition dominates" in out
