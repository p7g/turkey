"""The bootstrap: what is committed, and the fixed point it reaches.

`bootc.binary()` is stage2 of `scripts/build.sh`: the committed compiler in
`bootstrap/` compiling today's source. Here stage2 writes the same source as
an executable, stage3, and stage3 must write itself byte for byte.

The two compilers compared are the committed one and the one built from
today's source: the ordinary fixed point, which is what OCaml's `make compare`
and Rust's stage3 check. It is weaker than comparing two independent compilers
of the same source, since a miscompile that reproduces itself exactly survives
it; what it does catch is any change in what the compiler writes for itself.
It compares executables, so the encoder, the layout and the signature are
inside it as well as the code.

**The fixed point is opt in.** It costs a couple of minutes and re-pays on every
change to `src/`, which is when it is least wanted, so it is marked
`bootstrap` and excluded by default (`pyproject.toml`). Run it with:

    pytest -m bootstrap

`tests/test_emit.py` already assembles stage2's emission of the compiler on
every run, so what is opt-in here is the second compile, not the coverage of
the emitter itself. The provenance check is cheap and always runs.
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

from tests import bootc, toolchain
from tests.bootc import CACHE

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOT_MAIN = REPO_ROOT / "src" / "Main.gob"
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
    return bootc.boot_each("asm", [BOOT_MAIN], _split)[BOOT_MAIN]


def _write_boot(compiler: Path, output: Path) -> None:
    result = subprocess.run(
        toolchain.command(compiler, "build", "-o", str(output), str(BOOT_MAIN)),
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]


@functools.lru_cache(maxsize=None)
def _stage3() -> Path:
    """The executable stage2 writes for the compiler's own source: a compiler
    built by today's source rather than by the committed one."""
    # Keyed on the source as well as on stage2: under `$TURKEY_BOOT`,
    # `build_key` covers the binary and `lib/` alone, and stage3 is
    # today's `src/` too.
    h = hashlib.sha256(bootc.build_key().encode())
    for path in sorted((REPO_ROOT / "src").rglob("*.gob")):
        h.update(str(path.relative_to(REPO_ROOT)).encode() + b"\0")
        h.update(path.read_bytes())
    key = h.hexdigest()[:16]
    output = CACHE / f"stage3-{key}"
    if not output.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        staging = CACHE / f"stage3-{key}-{os.getpid()}"
        try:
            _write_boot(bootc.binary(), staging)
            os.replace(staging, output)
        finally:
            staging.unlink(missing_ok=True)
    return output


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.bootstrap
def test_stage3_writes_itself_byte_for_byte(tmp_path):
    """The fixed point: stage3 writes stage4, which must be stage3's bytes.

    The `-o` path is not in the bytes: the signature's identifier is the
    entry file's stem, so stage4 is written under another directory and
    name. On a mismatch the first line where stage3's assembly differs from
    stage2's is reported, since an executable's first differing byte names
    no line.
    """
    stage4 = tmp_path / "stage4"
    _write_boot(_stage3(), stage4)
    if _stage3().read_bytes() == stage4.read_bytes():
        return
    result = subprocess.run(
        toolchain.command(_stage3(), *bootc.argv("asm"), str(BOOT_MAIN)),
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    one = _stage3_assembly().splitlines()
    two = bootc.split_before(result.stdout, "// === ")[BOOT_MAIN.name].splitlines()
    for number, (a, b) in enumerate(zip(one, two), start=1):
        if a != b:
            pytest.fail(f"stage3's assembly differs from stage2's at line {number}:\n"
                        f"  stage2: {a}\n  stage3: {b}")
    if len(one) != len(two):
        pytest.fail(f"stage3 emitted {len(two)} lines, stage2 {len(one)}")
    pytest.fail("stage3 and stage4 differ, and stage2's and stage3's assembly do not")


@pytest.mark.bootstrap
def test_stage3_agrees_with_stage2_over_the_corpus():
    """One program, two compilers, for every program in the corpus.

    Cheaper than the test above and a different failure surface: the fixed
    point catches a miscompile of the compiler, and this catches one that
    only shows in what the compiler *emits* for other sources.
    """
    programs = bootc.corpus()
    stage2 = bootc.boot_each("select", programs, _split_asm)
    result = subprocess.run(
        toolchain.command(_stage3(), *bootc.argv("select"), *[str(p) for p in programs]),
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    stage3 = bootc.split_before(result.stdout, "; === ")
    for path in programs:
        assert _sha(stage2[path]) == _sha(stage3[path.name]), path.name


STRESSED = REPO_ROOT / "tests" / "programs" / "adt.gob"


@functools.lru_cache(maxsize=None)
def _unstressed() -> str:
    return bootc.boot(*bootc.argv("asm"), str(STRESSED))


def _stressed(boot: Path | None = None, **settings: str) -> str:
    env = dict(os.environ, **settings)
    result = subprocess.run(toolchain.command(boot or bootc.binary(), *bootc.argv("asm"), str(STRESSED)),
                            cwd=REPO_ROOT, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr.decode("utf-8")[:4000]
    return result.stdout.decode("utf-8")


@pytest.mark.bootstrap
@pytest.mark.parametrize("seed", ["1", "2", "3"])
def test_boot_compiles_the_same_under_sampled_gc_stress(seed):
    """`turkey` itself under GC stress, the one program whose roots are densest.

    Stress at every allocation does not finish at this scale: `turkey` makes
    tens of millions of objects before it reaches the program. Sampled at
    one collection per 100,000 allocations on average it is about 800
    collections and a dozen seconds, and each seed puts them at different
    allocations.
    """
    assert _stressed(TURKEY_GC_STRESS="100000", TURKEY_GC_STRESS_SEED=seed) == _unstressed()


@pytest.fixture(scope="module")
def verifying_boot(tmp_path_factory) -> Path:
    """`turkey` compiled by `turkey` with the heap verifier in, which is only ever
    chosen when a program is compiled."""
    directory = tmp_path_factory.mktemp("verifying-turkey")
    binary = directory / "turkey"
    bootc.executable(bootc.BOOT_MAIN, binary, "--gc-verify")
    return binary


@pytest.mark.bootstrap
def test_boot_compiles_the_same_under_sampled_gc_stress_and_verification(verifying_boot):
    """The same, with the heap verified at every collection, sparser because
    each verification walks the whole heap."""
    assert _stressed(verifying_boot, TURKEY_GC_STRESS="1000000") == _unstressed()


def _split_asm(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "; === ")
    return [modules[p.name] for p in paths]


def _provenance() -> dict[str, str]:
    lines = (BOOTSTRAP / "PROVENANCE").read_text().splitlines()
    return dict(line.split(": ", 1) for line in lines if line)


def test_the_bootstrap_is_what_its_provenance_says():
    """Cheap, so not opt in: the committed artifact against its record.

    `scripts/build.sh` checks the same hashes before it builds, so a mismatch
    here is a build that will refuse to start. The commit has to exist for
    the chain to be walked back to source, which
    `scripts/reproduce-bootstrap.sh` does.
    """
    fields = _provenance()
    artifacts = sorted(BOOTSTRAP.glob("*.gz"))
    assert {a.name for a in artifacts} == {"arm64-darwin.gz", "arm64-linux.gz"}
    for artifact in artifacts:
        target = artifact.name.removesuffix(".gz")
        packed = artifact.read_bytes()
        assert hashlib.sha256(packed).hexdigest() == fields[f"{target} gz-sha256"]
        assert (hashlib.sha256(gzip.decompress(packed)).hexdigest()
                == fields[f"{target} exe-sha256"])
    if (REPO_ROOT / ".git").exists() and shutil.which("git"):
        found = subprocess.run(
            ["git", "cat-file", "-e", f"{fields['commit']}^{{commit}}"],
            cwd=REPO_ROOT, capture_output=True)
        assert found.returncode == 0, f"no commit {fields['commit']}"


def test_build_sh_speaks_only_the_bootstraps_command_line():
    """A tripwire for the bump after the one that renamed `native`.

    `scripts/build.sh` asks each stage whether it says `build` or the older
    `native`, because the committed compiler may predate `build`. Once a bump
    records a bootstrap that says `build`, every compiler `build.sh` runs
    does, and the fallback is dead code: delete `emit_command` and call
    `build` directly. `scripts/compare.sh` keeps its copy, since it builds
    older trees.
    """
    if not _provenance()["command"].startswith("build "):
        return
    script = (REPO_ROOT / "scripts" / "build.sh").read_text()
    assert "native" not in script, (
        "the compiler in bootstrap/ now takes `build`, so scripts/build.sh no longer "
        "needs to fall back to `native`: remove `emit_command` and call "
        "`build -o - -f asm` directly")
