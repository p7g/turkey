"""`tests/bootc.py`: the cache the test suite's `boot` comes from.

It is content-addressed, and a content-addressed cache has exactly one way to
be dangerous -- a key that does not cover everything the value depends on. It
then serves a stale answer, silently: here, a `boot` built from source that no
longer exists, passing tests for code nobody is running. So the key is tested
rather than reasoned about.

This test compiles nothing, and it does not touch the repository: it builds a
tree of stand-in files under `tmp_path` and fingerprints that. Perturbing the
real `src/` is harmless in a serial run and not under `pytest -n auto`, where
another worker could cache a fingerprint of the edited tree for the rest of
its life.
"""

from __future__ import annotations

from pathlib import Path

from tests import bootc


def test_the_build_fingerprint_covers_boot_and_the_bootstrap(
        tmp_path: Path) -> None:
    """`binary()` is keyed on this, and a miss here is a stale executable."""
    for relative in ("src/Main.gob", "src/Turkey/Regalloc.gob",
                     "lib/Prelude.gob",
                     "runtime/turkey_runtime.c", "runtime/turkey_runtime.h",
                     "bootstrap/PROVENANCE",
                     "bootstrap/runtime/turkey_runtime.c",
                     "scripts/build.sh"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"-- {relative}\n")
    before = bootc._fingerprint(tmp_path)
    for relative in ("src/Turkey/Regalloc.gob", "bootstrap/PROVENANCE",
                     "bootstrap/runtime/turkey_runtime.c", "scripts/build.sh"):
        target = tmp_path / relative
        original = target.read_bytes()
        target.write_bytes(original + b"\n")
        assert bootc._fingerprint(tmp_path) != before, (
            f"a change to {relative} did not change the build key")
        target.write_bytes(original)
    assert bootc._fingerprint(tmp_path) == before, (
        "the fingerprint did not settle back")
