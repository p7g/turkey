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

**Neither touches the repository.** Both used to perturb real files --
`boot/Turkey/Regalloc.gob` and `turkey/driver.py` -- and restore them, which is
harmless in a serial run and not under `pytest -n auto`: another worker could
import a truncated `driver.py`, start a spurious three-minute build of `boot`,
or cache a fingerprint of the edited tree for the rest of its life. They work on
copies in `tmp_path` now.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from tests import bootc


def _stage(name: str) -> str:
    """A stage name no previous run can have used.

    The cache these tests exercise is on disk and survives the session, so a
    fixed stage name makes a test that passes exactly once: cold, the first
    call computes and the second reuses; warm, *neither* computes and an
    assertion counting one call fails. Both of these tests were written that
    way and both passed on the run that created their entries.
    """
    return f"{name}-{uuid.uuid4().hex}"


def _program(root: Path) -> tuple[Path, Path]:
    """A program and a module it imports, in the layout `boot/` has."""
    main = root / "Main.gob"
    imported = root / "Turkey" / "Regalloc.gob"
    imported.parent.mkdir(parents=True)
    main.write_text("import Turkey.Regalloc as Regalloc\nfun main() {}\n")
    imported.write_text("module Turkey.Regalloc ()\n")
    return main, imported


def test_a_reference_is_reused_when_nothing_changed(tmp_path: Path) -> None:
    calls: list[int] = []

    def compute() -> str:
        calls.append(1)
        return f"value-{len(calls)}"

    main, _ = _program(tmp_path)
    stage = _stage("test-reuse")
    first = bootc.reference(stage, main, compute)
    second = bootc.reference(stage, main, compute)
    assert first == second
    assert len(calls) == 1, "the second call recomputed instead of reusing"


def test_a_reference_notices_a_change_to_a_module_the_program_imports(
        tmp_path: Path) -> None:
    """The bug this test exists for, and it was real.

    The key hashed the program's own bytes. `check` follows imports, so the
    reference for `boot/Main.gob` depends on all of `boot/Turkey/` -- and
    editing a module it imports left the key unchanged and the cached answer
    served. Every backend commit in this repository edits such a module.
    """
    calls: list[int] = []

    def compute() -> str:
        calls.append(1)
        return f"value-{len(calls)}"

    main, imported = _program(tmp_path)
    stage = _stage("test-imports")
    before = bootc.reference(stage, main, compute)
    original = imported.read_bytes()
    imported.write_bytes(original + b"\n-- a change to an imported module\n")
    during = bootc.reference(stage, main, compute)
    imported.write_bytes(original)
    after = bootc.reference(stage, main, compute)

    assert during != before, (
        "a change to an imported module did not invalidate the reference")
    assert after == before, (
        "reverting the change did not return the original key")


def test_the_build_fingerprint_covers_boot_and_the_bootstrap(
        tmp_path: Path) -> None:
    """`binary()` is keyed on this, and a miss here is a stale executable.

    The Python compiler is deliberately *not* an input: `boot` is built from
    `bootstrap/` (TIX-95), and keying on `turkey/` would rebuild it for edits
    that cannot change it.
    """
    for relative in ("boot/Main.gob", "boot/Turkey/Regalloc.gob",
                     "lib/Prelude.gob", "turkey/driver.py",
                     "runtime/turkey_runtime.c", "runtime/turkey_runtime.h",
                     "bootstrap/PROVENANCE", "bootstrap/runtime/turkey_runtime.c",
                     "tools/build.sh"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"-- {relative}\n")
    before = bootc._fingerprint(tmp_path)
    for relative in ("boot/Turkey/Regalloc.gob", "bootstrap/PROVENANCE",
                     "bootstrap/runtime/turkey_runtime.c", "tools/build.sh"):
        target = tmp_path / relative
        original = target.read_bytes()
        target.write_bytes(original + b"\n")
        assert bootc._fingerprint(tmp_path) != before, (
            f"a change to {relative} did not change the build key")
        target.write_bytes(original)
    assert bootc._fingerprint(tmp_path) == before, (
        "the fingerprint did not settle back")
    (tmp_path / "turkey/driver.py").write_text("-- edited\n")
    assert bootc._fingerprint(tmp_path) == before, (
        "the Python compiler changed the build key")
