"""The bootstrap: what is committed, and the fixed point it reaches.

`bootc.binary()` is stage2 of `tools/build.sh`: the committed compiler in
`bootstrap/` compiling today's source (BOOTSTRAP.md). Here stage2 compiles the
same source again, which links into stage3, and stage3 compiling it once more
must emit stage2's output byte for byte.

This used to be a stronger form: stage1 was built by LLVM through the Python
compiler, so the comparison was one source compiled by two *different*
compilers. With the Python compiler on its way out (TIX-96) the two compilers
are the committed one and the one built from today's source -- the ordinary
fixed point, which is what OCaml's `make compare` and Rust's stage3 check.

**The fixed point is opt in.** It costs a couple of minutes and re-pays on every
change to `boot/`, which is when it is least wanted, so it is marked
`bootstrap` and excluded by default (`pyproject.toml`). Run it with:

    pytest -m bootstrap

`tests/test_emit.py` already assembles stage2's emission of the compiler on
every run, so what is opt-in here is the linking and the second compile, not
the coverage of the emitter itself. The provenance check is cheap and always
runs.
"""

from __future__ import annotations

import functools
import gzip
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import bootc
from tests.bootc import CACHE, runtime_object as _runtime_object
from tests.bootc import digest as _digest, replace_built as _replace_built

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOT_MAIN = REPO_ROOT / "boot" / "Main.gob"
BOOTSTRAP = REPO_ROOT / "bootstrap"


def _split(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "// === ")
    return [modules[p.name] for p in paths]


@functools.lru_cache(maxsize=None)
def _stage3_assembly() -> str:
    """Stage2's arm64 assembly for the compiler's own source.

    The same disk-cached artifact `tests/test_emit.py` assembles, so running
    both costs one emission rather than two.
    """
    return bootc.boot_each("native", [BOOT_MAIN], _split)[BOOT_MAIN]


@functools.lru_cache(maxsize=None)
def _stage3() -> Path:
    """Stage2's output for itself, assembled and linked: a compiler built by
    today's source rather than by the committed one."""
    assembly = _stage3_assembly().encode("utf-8")
    runtime = _runtime_object()
    output = CACHE / f"stage3-arm64-{_digest(assembly, runtime.read_bytes())}.bin"
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


@pytest.mark.bootstrap
@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
def test_stage3_compiles_the_compiler_to_stage2s_bytes():
    """The fixed point, as a byte comparison.

    Hashed rather than compared directly: the two texts are about 54 MB each,
    and holding both plus a diff is a great deal of memory to spend on an
    assertion that is almost always true. On a mismatch the first differing
    line is reported, which is what a failure actually needs.
    """
    stage3 = _stage3_assembly()
    result = subprocess.run([str(_stage3()), "native", str(BOOT_MAIN)],
                            cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    stage4 = bootc.split_before(result.stdout, "// === ")[BOOT_MAIN.name]

    if _sha(stage3) == _sha(stage4):
        return
    one, two = stage3.splitlines(), stage4.splitlines()
    for number, (a, b) in enumerate(zip(one, two), start=1):
        if a != b:
            pytest.fail(f"stage3's output differs from stage2's at line {number}:\n"
                        f"  stage2: {a}\n  stage3: {b}")
    pytest.fail(f"stage3 emitted {len(two)} lines, stage2 {len(one)}")


@pytest.mark.bootstrap
@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
def test_stage3_agrees_with_stage2_over_the_corpus():
    """One program, two compilers, for every program in the corpus.

    Cheaper than the test above and a different failure surface: the fixed
    point catches a miscompile of the compiler, and this catches one that
    only shows in what the compiler *emits* for other sources.
    """
    programs = sorted(path for path in (REPO_ROOT / "tests" / "programs").glob("*.gob")
                      if not path.name.startswith("err_"))
    stage2 = bootc.boot_each("asm", programs, _split_asm)
    result = subprocess.run([str(_stage3()), "asm", *[str(p) for p in programs]],
                            cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    stage3 = bootc.split_before(result.stdout, "; === ")
    for path in programs:
        assert _sha(stage2[path]) == _sha(stage3[path.name]), path.name


def _split_asm(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "; === ")
    return [modules[p.name] for p in paths]


def _provenance() -> dict[str, str]:
    lines = (BOOTSTRAP / "PROVENANCE").read_text().splitlines()
    return dict(line.split(": ", 1) for line in lines if line)


def test_the_bootstrap_is_what_its_provenance_says():
    """Cheap, so not opt in: the committed artifact against its record.

    `tools/build.sh` checks the same hashes before it builds, so a mismatch
    here is a build that will refuse to start. The commit has to exist for
    the chain to be walked back to source.
    """
    fields = _provenance()
    packed = (BOOTSTRAP / "arm64-darwin.s.gz").read_bytes()
    assert hashlib.sha256(packed).hexdigest() == fields["gz-sha256"]
    assert hashlib.sha256(gzip.decompress(packed)).hexdigest() == fields["asm-sha256"]
    if (REPO_ROOT / ".git").exists() and shutil.which("git"):
        found = subprocess.run(
            ["git", "cat-file", "-e", f"{fields['commit']}^{{commit}}"],
            cwd=REPO_ROOT, capture_output=True)
        assert found.returncode == 0, f"no commit {fields['commit']}"
