"""The arm64 backend's fixed point: stage2 compiles the compiler to stage1's bytes.

M28 phase 6. `boot` is built by LLVM (stage1) and emits arm64 assembly for its
own source; linking that gives stage2, a compiler built by this backend rather
than by LLVM. Asking stage2 to compile the same source again must produce
exactly what stage1 produced.

That is the strong form of the bootstrap check and not the usual one. The
ordinary fixpoint compares stage3 against a fourth stage, which only says the
compiler is stable once. This compares one source compiled by two *different*
compilers -- one built by LLVM, one built by the backend under test -- so a
miscompile in the backend has to reproduce itself exactly in order to hide.

**Opt in.** It costs a couple of minutes and re-pays on every change to
`boot/`, which is when it is least wanted, so it is marked `bootstrap` and
excluded by default (`pyproject.toml`). Run it with:

    pytest -m bootstrap

`tests/test_emit.py` already assembles stage1's emission of the compiler on
every run, so what is opt-in here is the linking and the second compile, not
the coverage of the emitter itself.
"""

from __future__ import annotations

import functools
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import bootc
from tests.bootc import CACHE, runtime_object as _runtime_object
from tests.bootc import digest as _digest, replace_built as _replace_built

pytestmark = pytest.mark.bootstrap

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOT_MAIN = REPO_ROOT / "boot" / "Main.gob"


def _split(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "// === ")
    return [modules[p.name] for p in paths]


@functools.lru_cache(maxsize=None)
def _stage1_assembly() -> str:
    """Stage1's arm64 assembly for the compiler's own source.

    The same disk-cached artifact `tests/test_emit.py` assembles, so running
    both costs one emission rather than two.
    """
    return bootc.boot_each("native", [BOOT_MAIN], _split)[BOOT_MAIN]


@functools.lru_cache(maxsize=None)
def _stage2() -> Path:
    """Stage1's output for itself, assembled and linked: a compiler this
    backend built."""
    assembly = _stage1_assembly().encode("utf-8")
    runtime = _runtime_object()
    output = CACHE / f"stage2-arm64-{_digest(assembly, runtime.read_bytes())}.bin"
    if not output.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        stem = output.with_suffix(f".{os.getpid()}")
        source = stem.with_suffix(stem.suffix + ".s")
        source.write_bytes(assembly)
        staging = stem.with_suffix(stem.suffix + ".bin")
        try:
            _replace_built(["cc", "-o", str(staging), str(source),
                            str(runtime)], output)
        finally:
            source.unlink(missing_ok=True)
    return output


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
def test_stage2_compiles_the_compiler_to_stage1s_bytes():
    """The fixed point, as a byte comparison.

    Hashed rather than compared directly: the two texts are about 54 MB each,
    and holding both plus a diff is a great deal of memory to spend on an
    assertion that is almost always true. On a mismatch the first differing
    line is reported, which is what a failure actually needs.
    """
    stage1 = _stage1_assembly()
    result = subprocess.run([str(_stage2()), "native", str(BOOT_MAIN)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    stage2 = bootc.split_before(result.stdout, "// === ")[BOOT_MAIN.name]

    if _sha(stage1) == _sha(stage2):
        return
    one, two = stage1.splitlines(), stage2.splitlines()
    for number, (a, b) in enumerate(zip(one, two), start=1):
        if a != b:
            pytest.fail(f"stage2 differs from stage1 at line {number}:\n"
                        f"  stage1: {a}\n  stage2: {b}")
    pytest.fail(f"stage2 has {len(two)} lines, stage1 has {len(one)}")


@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
def test_stage2_agrees_with_stage1_over_the_corpus():
    """One program, two compilers, for every program in the corpus.

    Cheaper than the test above and a different failure surface: the fixed
    point catches a miscompile of the compiler, and this catches one that
    only shows in what the compiler *emits* for other sources.
    """
    programs = sorted(path for path in (REPO_ROOT / "tests" / "programs").glob("*.gob")
                      if not path.name.startswith("err_"))
    stage1 = bootc.boot_each("asm", programs, _split_asm)
    result = subprocess.run([str(_stage2()), "asm", *[str(p) for p in programs]],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    stage2 = bootc.split_before(result.stdout, "; === ")
    for path in programs:
        assert _sha(stage1[path]) == _sha(stage2[path.name]), path.name


def _split_asm(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "; === ")
    return [modules[p.name] for p in paths]
