"""`boot`, compiled once and shared by every test module that runs it.

Compiling `boot` takes about two minutes, from the committed bootstrap
by `scripts/build.sh`; through the Python compiler it took three.
*Running* the compiled binary over the whole corpus takes ten seconds. Every
ratio in this file follows from those two numbers.

`test_boot` learned that once already: it builds an executable and reuses it
across its stages, and its docstring explains why. What it did not do was put
the fixture anywhere its siblings could reach, so `test_ssa_lower` and
`test_native` each went on running `boot` through `turkey.driver.run` --
interpreting the whole bootstrap compiler under the Python implementation,
about three minutes before either looks at its input. `test_native` took
forty-six minutes to do ten seconds of work.

That is the third form of one mistake. FINDINGS 61 was "do not pay startup per
file". FINDINGS 65 was the same again in a different command. This one is **the
fix was made in one module and never reached the others** -- and the thing that
hid it is that `test_ssa_lower`'s docstring already claimed the shape, calling
itself the "same shape as `test_boot` building one binary and sharing it" while
building no binary at all. A comment describing the fix reads exactly like the
fix.

So the fixture lives here, in a module that is not itself a test, and the rule
is: **no test may run `boot` through `turkey.driver.run`.** If a module needs
`boot`, it calls `boot(...)` below and pays the build once per session, shared
with everyone else.

Running a *corpus program* through `turkey.driver.run` is a different thing and
stays: that is the reference implementation answering what the program should
print, which is the oracle `test_native` compares against and takes about a
third of a second. The rule is about the bootstrap compiler, not about the
driver.
"""

from __future__ import annotations

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


# Everything whose contents can change what `boot` compiles to: its own
# source, the library it links against, the committed compiler that builds it
# and the script that does, and the C runtime it is linked with. Hashing these
# is what lets a build be reused; missing one would mean serving a stale
# binary, which is worse than rebuilding, so this list errs wide.
_INPUTS = (("src", "*.gob"), ("lib", "*.gob"),
           ("bootstrap", "*"), ("scripts", "build.sh"),
           ("runtime", "*.c"), ("runtime", "*.h"))


def _fingerprint(root: Path = REPO_ROOT) -> str:
    """A digest of every input to the build, for use as a cache key.

    Takes the root so that `tests/test_bootc.py` can check the key against a
    copy. It used to edit the real `src/` and `turkey/` to do it, which under
    `pytest -n auto` is a truncated `driver.py` imported by some other worker.
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
    changes nothing pays nothing. This matters more outside the test suite than
    in it: a one-off script that wants a compiled `boot` used to pay the full
    build every time it ran, which is two minutes to ask a question that
    takes ten seconds to answer.

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

    `boot types` reports exhaustiveness warnings on stderr, and `boot` above
    threw stderr away -- which is how `test_boot`'s types milestone came to run
    `boot` *interpreted* instead, the one thing this module's header forbids,
    for six minutes a run (FINDINGS 93).
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
    sources are nobody's business -- the binary itself plus the library and the
    runtime it reads at run time.
    """
    if not os.environ.get(BOOT_OVERRIDE):
        return _fingerprint()
    h = hashlib.sha256(binary().read_bytes())
    # Every output of `lang` is linked with `$TURKEY_CC`.
    h.update(toolchain.identity())
    for directory, pattern in (("lib", "*.gob"), ("runtime", "*.c"),
                               ("runtime", "*.h")):
        for path in sorted((REPO_ROOT / directory).rglob(pattern)):
            if path.name.startswith("Probe_"):
                continue
            h.update(str(path.relative_to(REPO_ROOT)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


# Binaries and the runtime object, shared by every worker and every session.
# Each file is keyed by the hash of what it was built from, so a stale one is
# never served and a warm one is never rebuilt.
CACHE = Path(tempfile.gettempdir()) / "turkey-native"
RUNTIME = REPO_ROOT / "runtime" / "turkey_runtime.c"
RUNTIME_HEADER = REPO_ROOT / "runtime" / "turkey_runtime.h"


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


@functools.lru_cache(maxsize=None)
def runtime_object(*flags: str) -> Path:
    """`turkey_runtime.c`, compiled once rather than once per program, with
    `-O1` and any `flags` after it.

    Every test binary used to compile the runtime from source beside its module
    -- the largest C file here, forty-odd times per worker.
    """
    key = digest(RUNTIME.read_bytes(), RUNTIME_HEADER.read_bytes(),
                 toolchain.identity(), "\0".join(flags).encode())
    output = CACHE / f"runtime-{key}.o"
    if not output.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        staging = CACHE / f"runtime-{key}.{os.getpid()}.o"
        replace_built([*toolchain.cc(), "-std=c11", "-O1", *flags, "-c",
                       "-o", str(staging), str(RUNTIME)], output)
    return output


def boot_each(command: str, paths: list[Path],
              split: Callable[[str, list[Path]], list[str]]) -> dict[Path, str]:
    """`boot <command>` over some programs, cached on disk per program.

    The test modules that run `boot` over the corpus kept the result in an
    `lru_cache`, which is one run per *process* -- and under `pytest -n auto`
    every worker that drew one of their tests paid the whole corpus run again.
    `test_select`'s cost ~22 s a worker, sixteen times.

    Keyed on the build fingerprint -- `boot`, `lib/`, `turkey/`, the runtime --
    plus the command and the program's own bytes. Corpus programs import only
    the library, which the fingerprint covers; a program that imported a
    sibling would need its directory hashed too, as `reference` does.

    The missing programs are computed in one `boot` run under a lock, so a cold
    cache is filled once while the other workers wait. `split` cuts that run's
    output into one text per path, in order.
    """
    directory = Path(tempfile.gettempdir()) / "turkey-bootout" / _build_key() / command
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
    with open(directory / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        found, missing = load()
        if missing:
            text = boot(command, *(str(path) for path in missing))
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
    marker line (`llvm`, `asm`), and one that *ends* each with a count line
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
