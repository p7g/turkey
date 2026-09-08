"""`tests/bootc.py`: the two caches the test suite runs on.

Both are content-addressed, and a content-addressed cache has exactly one way
to be dangerous -- a key that does not cover everything the value depends on.
It then serves a stale answer, silently, and the stalest thing this repository
could serve is a *reference* dump: `test_boot` would compare `boot` against an
expectation computed from source that no longer exists and report agreement.
That is worse than any failure it is meant to catch, so the keys are tested
rather than reasoned about.

Neither test compiles anything. They substitute a counter for the expensive
computation and check only which calls happen, which is what makes them cheap
enough to run beside the suite they protect.
"""

from __future__ import annotations

from pathlib import Path

from tests import bootc

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_a_reference_is_reused_when_nothing_changed() -> None:
    calls: list[int] = []

    def compute() -> str:
        calls.append(1)
        return f"value-{len(calls)}"

    main = REPO_ROOT / "boot" / "Main.tl"
    first = bootc.reference("test-reuse", main, compute)
    second = bootc.reference("test-reuse", main, compute)
    assert first == second
    assert len(calls) == 1, "the second call recomputed instead of reusing"


def test_a_reference_notices_a_change_to_a_module_the_program_imports() -> None:
    """The bug this test exists for, and it was real.

    The key hashed the program's own bytes. `check` follows imports, so the
    reference for `boot/Main.tl` depends on all of `boot/Turkey/` -- and
    editing a module it imports left the key unchanged and the cached answer
    served. Every backend commit in this repository edits such a module.
    """
    calls: list[int] = []

    def compute() -> str:
        calls.append(1)
        return f"value-{len(calls)}"

    main = REPO_ROOT / "boot" / "Main.tl"
    imported = REPO_ROOT / "boot" / "Turkey" / "Regalloc.tl"
    assert imported.is_file(), "the module this test perturbs is gone"

    before = bootc.reference("test-imports", main, compute)
    original = imported.read_bytes()
    try:
        imported.write_bytes(original + b"\n-- a change to an imported module\n")
        during = bootc.reference("test-imports", main, compute)
    finally:
        imported.write_bytes(original)
    after = bootc.reference("test-imports", main, compute)

    assert during != before, (
        "a change to an imported module did not invalidate the reference")
    assert after == before, (
        "reverting the change did not return the original key")


def test_the_build_fingerprint_covers_boot_and_the_python_compiler() -> None:
    """`binary()` is keyed on this, and a miss here is a stale executable."""
    before = bootc._fingerprint()
    for target in (REPO_ROOT / "boot" / "Turkey" / "Regalloc.tl",
                   REPO_ROOT / "turkey" / "driver.py"):
        assert target.is_file(), f"{target} is gone"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n")
            assert bootc._fingerprint() != before, (
                f"a change to {target.name} did not change the build key")
        finally:
            target.write_bytes(original)
    assert bootc._fingerprint() == before, "the fingerprint did not settle back"
