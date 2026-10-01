"""`boot`, compiled once and shared by every test module that runs it.

Compiling `boot` takes about two minutes, from the committed bootstrap by
`scripts/build.sh`. *Running* the compiled binary over the whole corpus takes
ten seconds. Every ratio in this file follows from those two numbers.

So the build happens here, in a module that is not itself a test, and the rule
is: **a test that needs `boot` calls `boot(...)` or `binary()` below**, and
never builds or starts one of its own. The build is paid once per change to
its inputs, shared by every worker and every session, and a test module that
arranged its own would pay minutes per worker for seconds of work. The same
holds one level down: a test that runs `boot` over the whole corpus goes
through `boot_each`, so the corpus is compiled once, not once per worker.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from tests import toolchain

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_MAIN = REPO_ROOT / "src" / "Main.gob"

# The implementation's modules the corpus imports from an ordinary program --
# `ptr.gob` and `ptr_gc.gob` test raw memory, `foreign.gob` a C call and
# `giblets_memory.gob` a giblet -- which the compiler refuses outside the
# library unless named here. Set for every `boot` a test runs, since the corpus
# is compiled from many places; a test of the refusal itself removes it.
INTERNAL_IMPORTS = "TURKEY_TEST_INTERNAL_IMPORTS"
os.environ[INTERNAL_IMPORTS] = "Turkey.Libc,Turkey.Memory,Turkey.Ptr"


# Everything whose contents can change what `boot` compiles to: its own
# source, the library it links against, the committed compiler that builds it
# and the script that does. Hashing these is what lets a build be reused;
# missing one would mean serving a stale binary, which is worse than
# rebuilding, so this list errs wide.
_INPUTS = (("src", "*.gob"), ("lib", "*.gob"),
           ("bootstrap", "*"), ("scripts", "build.sh"))


def _fingerprint(root: Path = REPO_ROOT) -> str:
    """A digest of every input to the build, for use as a cache key.

    Takes the root so that `tests/test_bootc.py` can check the key against a
    copy, rather than editing the real `src/` while other workers under
    `pytest -n auto` may be reading it.
    """
    h = hashlib.sha256()
    for directory, pattern in _INPUTS:
        for path in sorted((root / directory).rglob(pattern)):
            if not path.is_file():
                continue
            # A test's throwaway module in `lib/` (`test_foreign`,
            # `test_giblets`): not an input to the build, and hashing it made
            # every probe a three-minute rebuild -- and, under `-n auto`, a
            # different key for whichever worker looked while one existed.
            if path.name.startswith("Probe_"):
                continue
            h.update(str(path.relative_to(root)).encode())
            h.update(path.read_bytes())
    # `build.sh` links `boot` with `$TURKEY_CC`.
    h.update(toolchain.identity())
    return h.hexdigest()[:16]


# A `boot` built elsewhere, used as it is, with nothing built.
BOOT_OVERRIDE = "TURKEY_BOOT"


@functools.lru_cache(maxsize=1)
def binary() -> Path:
    """The compiled `boot`, built on first use and cached across sessions.

    `$TURKEY_BOOT`, if set, names a `boot` to use instead, and nothing is built.

    `scripts/build.sh`'s stage2: the committed bootstrap compiling today's
    source. A real executable rather than anything in-process, because a
    subprocess per invocation keeps a crash in `boot` from taking the test
    session with it.

    The build takes about two minutes and the result depends on
    nothing but the files `_fingerprint` hashes, so it is kept in a shared directory keyed
    by that hash rather than in a per-session temporary one. A session that
    changes nothing pays nothing. This matters outside the test suite too: a
    one-off script that wants a compiled `boot` does not pay two minutes of
    build to ask a question that takes ten seconds to answer.

    Sharing the directory between concurrent builds is safe because the key is
    a content hash -- two builders racing are producing the same bytes -- but
    the *file* must not be observed half-written, so the build goes to a
    unique path and is moved into place with `os.replace`, which is atomic.
    """
    if (override := os.environ.get(BOOT_OVERRIDE)):
        return Path(override).resolve()
    cached = Path(tempfile.gettempdir()) / "turkey-bootc" / _fingerprint()
    output = cached / "boot"
    if output.exists():
        return output
    cached.mkdir(parents=True, exist_ok=True)
    # Under `pytest -n auto` every worker misses the cache at the same moment,
    # and sixteen identical builds racing each other take far
    # longer than one. The first to take the lock builds; the rest wait for it
    # and find the result. The atomic `os.replace` below is still what keeps a
    # reader from seeing a half-written file.
    with open(cached / "lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if output.exists():
            return output
        return _build(cached, output)


def _build(cached: Path, output: Path) -> Path:
    staging = cached / f"stages.{os.getpid()}"
    try:
        result = subprocess.run(
            ["sh", str(REPO_ROOT / "scripts" / "build.sh"), "--out", str(staging)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"building boot failed\n{result.stdout}\n{result.stderr}")
        os.replace(staging / "stage2", output)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return output


def argv(output: str) -> list[str]:
    """`boot`'s arguments for one output: `asm`, the program's assembly on
    stdout; `check`; or the name of a stage to dump (`core`, `ssa`, `select`,
    `listing`, ...)."""
    match output:
        case "asm":
            return ["build", "-o", "-", "-f", "asm"]
        case "check":
            return ["check"]
        case _:
            return ["check", "--dump", output]


def boot(*args: str) -> str:
    """One `boot` invocation, answering its stdout.

    Bytes, decoded here rather than by `text=True`. Universal-newline
    translation would rewrite a `\\r` inside a *string literal* in the output
    as a `\\n`, which is a difference the reference side never had -- and the
    dumps are compared line by line, so it lands as a mismatch several thousand
    lines from anything that is actually wrong.
    """
    out, _ = boot_with_stderr(*args)
    return out


def boot_with_stderr(*args: str) -> tuple[str, str]:
    """`boot`, answering stdout and stderr both.

    `boot` above throws stderr away, which is right for a test that wants
    only the dump. A test that also wants the diagnostics uses this, rather
    than running `boot` some other way.
    """
    result = subprocess.run(
        toolchain.command(binary(), *args),
        cwd=REPO_ROOT,
        capture_output=True,
    )
    out = result.stdout.decode("utf-8")
    err = result.stderr.decode("utf-8")
    assert result.returncode == 0, (
        f"boot exited {result.returncode}\n{out}\n{err}")
    return out, err


@functools.lru_cache(maxsize=1)
def _build_key() -> str:
    return build_key()


def build_key() -> str:
    """What a cached output of `boot` depends on, as a cache key.

    The build's fingerprint, or -- for a `boot` named by `$TURKEY_BOOT`, whose
    sources are nobody's business -- the binary itself plus the library it
    reads at run time.
    """
    if not os.environ.get(BOOT_OVERRIDE):
        return _fingerprint()
    h = hashlib.sha256(binary().read_bytes())
    # Every output of `lang` is linked with `$TURKEY_CC`.
    h.update(toolchain.identity())
    for path in sorted((REPO_ROOT / "lib").rglob("*.gob")):
        if path.name.startswith("Probe_"):
            continue
        h.update(str(path.relative_to(REPO_ROOT)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


# Binaries, shared by every worker and every session. Each file is keyed by
# the hash of what it was built from, so a stale one is never served and a warm
# one is never rebuilt.
CACHE = Path(tempfile.gettempdir()) / "turkey-native"

# Where `turkey_exports.h` is, for a C probe's `-I`: the declarations of what
# a compiled program exports to C.
PROBE_INCLUDE = REPO_ROOT / "tests"


def digest(*parts: bytes) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return h.hexdigest()[:24]


def replace_built(command: list[str], output: Path) -> None:
    """Run a build whose `-o` is a staging path, then move it to `output`."""
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    os.replace(command[command.index("-o") + 1], output)


def boot_each(output: str, paths: list[Path],
              split: Callable[[str, list[Path]], list[str]],
              flags: tuple[str, ...] = ()) -> dict[Path, str]:
    """`boot` over some programs for one output (`argv`), cached on disk per
    program.

    On disk rather than in an `lru_cache`: a cache per *process* means that
    under `pytest -n auto` every worker that draws one of these tests pays the
    whole corpus run again (for `test_select`, ~22 s a worker).

    Keyed on the build fingerprint -- `src/`, `lib/`, the bootstrap and the
    build script -- plus the command and the program's own bytes. Corpus programs import only
    the library, which the fingerprint covers; a program that imported a
    sibling would need its directory hashed too, as `reference` does.

    The missing programs are computed in one `boot` run, holding a lock per
    program, so a cold entry is filled once while any other worker that wants
    it waits. Per program rather than per output: the `select` dump of
    `src/Main.gob` takes minutes and the corpus's takes seconds, and one lock
    across both made every corpus test wait for the compiler. The locks are
    taken in path order, so two workers wanting overlapping sets cannot
    deadlock. `split` cuts the run's output into one text per path, in order.
    `flags` go to `boot` before the paths (`--gc-verify`), and into the key.
    """
    directory = (Path(tempfile.gettempdir()) / "turkey-bootout" / _build_key()
                 / " ".join((output, *flags)))
    directory.mkdir(parents=True, exist_ok=True)

    def entry(path: Path) -> Path:
        h = hashlib.sha256()
        h.update(str(path).encode())
        h.update(path.read_bytes())
        return directory / h.hexdigest()[:24]

    def load() -> tuple[dict[Path, str], list[Path]]:
        found, missing = {}, []
        for path in paths:
            cached = entry(path)
            if cached.exists():
                found[path] = cached.read_text(encoding="utf-8")
            else:
                missing.append(path)
        return found, missing

    found, missing = load()
    if not missing:
        return found
    with contextlib.ExitStack() as held:
        for cached in sorted({entry(path) for path in missing}):
            lock = held.enter_context(open(cached.with_suffix(".lock"), "w"))
            fcntl.flock(lock, fcntl.LOCK_EX)
        found, missing = load()
        if missing:
            text = boot(*argv(output), *flags, *(str(path) for path in missing))
            chunks = split(text, missing)
            assert len(chunks) == len(missing), (
                f"{len(chunks)} dumps for {len(missing)} programs")
            for path, chunk in zip(missing, chunks):
                cached = entry(path)
                staging = cached.with_suffix(f".{os.getpid()}")
                staging.write_text(chunk, encoding="utf-8")
                os.replace(staging, cached)
                found[path] = chunk
    return found


def split_on(text: str, marker: str) -> list[str]:
    """A multi-program dump, cut into one chunk per program.

    `boot` takes any number of files in one invocation -- which is the whole
    point of this module -- so every caller needs the same unpicking
    afterwards. Two shapes exist: a dump that *starts* each program with a
    marker line (`asm`, `select`), and one that *ends* each with a count line
    (`ssa`). This is the second; `split_before` is the first.
    """
    chunks, current = [], []
    for line in text.splitlines(keepends=True):
        current.append(line)
        if line.startswith(marker):
            chunks.append("".join(current))
            current = []
    # A program's dump ends at its marker; anything after the last one is a
    # crash, and belongs to the program that was being compiled.
    if current:
        chunks.append("".join(current))
    return chunks


def split_before(text: str, marker: str) -> dict[str, str]:
    """A dump whose programs each *begin* with `marker` plus their path."""
    modules: dict[str, str] = {}
    current: list[str] = []
    name: str | None = None
    for line in text.splitlines(keepends=True):
        if line.startswith(marker):
            if name is not None:
                modules[name] = "".join(current)
            name = Path(line[len(marker):].strip()).name
            current = []
        else:
            current.append(line)
    if name is not None:
        modules[name] = "".join(current)
    return modules
