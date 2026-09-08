"""`boot`, compiled once and shared by every test module that runs it.

Compiling `boot` takes about three minutes. *Running* the compiled binary over
the whole corpus takes ten seconds. Every ratio in this file follows from
those two numbers.

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

import functools
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_MAIN = REPO_ROOT / "boot" / "Main.tl"


# Everything whose contents can change what `boot` compiles to: its own
# source, the library it links against, the Python compiler that builds it, and
# the C runtime it is linked with. Hashing these is what lets a build be
# reused; missing one would mean serving a stale binary, which is worse than
# rebuilding, so this list errs wide.
_INPUTS = (("boot", "*.tl"), ("lib", "*.tl"),
           ("turkey", "*.py"), ("runtime", "*.c"), ("runtime", "*.h"))


def _fingerprint() -> str:
    """A digest of every input to the build, for use as a cache key."""
    h = hashlib.sha256()
    for directory, pattern in _INPUTS:
        for path in sorted((REPO_ROOT / directory).rglob(pattern)):
            h.update(str(path.relative_to(REPO_ROOT)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


@functools.lru_cache(maxsize=1)
def binary() -> Path:
    """The compiled `boot`, built on first use and cached across sessions.

    `turkey build` rather than compiling in-process and calling it, for two
    reasons that have not changed: a subprocess per invocation keeps a crash
    in `boot` from taking the test session with it, which matters while `boot`
    still has one -- and it had one this week, on a nine-argument call -- and a
    real executable is what self-hosting needs anyway.

    The build takes about three minutes and the result depends on nothing but
    the files `_fingerprint` hashes, so it is kept in a shared directory keyed
    by that hash rather than in a per-session temporary one. A session that
    changes nothing pays nothing. This matters more outside the test suite than
    in it: a one-off script that wants a compiled `boot` used to pay the full
    build every time it ran, which is three minutes to ask a question that
    takes ten seconds to answer.

    Sharing the directory between concurrent builds is safe because the key is
    a content hash -- two builders racing are producing the same bytes -- but
    the *file* must not be observed half-written, so the build goes to a
    unique path and is moved into place with `os.replace`, which is atomic.
    """
    cached = Path(tempfile.gettempdir()) / "turkey-bootc" / _fingerprint()
    output = cached / "boot"
    if output.exists():
        return output
    cached.mkdir(parents=True, exist_ok=True)
    staging = cached / f"boot.{os.getpid()}"
    result = subprocess.run(
        [sys.executable, "-m", "turkey", "build", str(BOOT_MAIN),
         "-o", str(staging)],
        cwd=REPO_ROOT,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        staging.unlink(missing_ok=True)
        raise AssertionError(
            f"building boot failed\n{result.stdout}\n{result.stderr}")
    os.replace(staging, output)
    return output


def boot(*args: str) -> str:
    """One `boot` invocation, answering its stdout.

    Bytes, decoded here rather than by `text=True`. Universal-newline
    translation would rewrite a `\\r` inside a *string literal* in the output
    as a `\\n`, which is a difference the reference side never had -- and the
    dumps are compared line by line, so it lands as a mismatch several thousand
    lines from anything that is actually wrong.
    """
    result = subprocess.run(
        [str(binary()), *args],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    out = result.stdout.decode("utf-8")
    err = result.stderr.decode("utf-8")
    assert result.returncode == 0, (
        f"boot exited {result.returncode}\n{out}\n{err}")
    return out


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


# The Python implementation's own inputs. Deliberately *not* `boot/`: a
# reference dump is keyed on these plus the source of the one program being
# compiled, so changing `boot/Turkey/Regalloc.tl` invalidates the entry for
# `boot/Main.tl` -- which genuinely changed -- and leaves the corpus entries
# alone.
_REFERENCE_INPUTS = (("turkey", "*.py"), ("lib", "*.tl"))


@functools.lru_cache(maxsize=1)
def _reference_fingerprint() -> str:
    h = hashlib.sha256()
    for directory, pattern in _REFERENCE_INPUTS:
        for path in sorted((REPO_ROOT / directory).rglob(pattern)):
            h.update(str(path.relative_to(REPO_ROOT)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


def reference(stage: str, path: Path, compute) -> str:
    """One Python-side reference dump, cached on disk by content hash.

    `turkey.driver.check` on `boot/Main.tl` takes about seventy seconds, and
    `test_boot` runs it once per *stage* -- five times for one answer that
    cannot have changed between them. Over the corpus it is another thirteen
    seconds a stage. None of it depends on anything but the Python
    implementation and the program being compiled, so none of it needs doing
    twice.

    Keyed per program rather than over the corpus as a whole, which is what
    makes it useful during backend work: a change to `boot/` invalidates the
    `boot/Main.tl` entry and nothing else, so the twenty-nine corpus entries
    stay warm. A change to `turkey/` invalidates everything, which is correct
    -- that is the side being compared against.

    Strings, not the structures they came from: `check` answers a mutable
    object and a cache that handed the same one to two tests would be a
    cross-test aliasing bug of the worst kind, silent and order-dependent.
    """
    h = hashlib.sha256()
    h.update(_reference_fingerprint().encode())
    h.update(stage.encode())
    h.update(str(path).encode())
    # Every `.tl` beside the program, not just the program. `check` follows
    # imports, so the reference for `boot/Main.tl` depends on all of
    # `boot/Turkey/` -- and a key that hashed only `Main.tl` would have served
    # a stale expectation after any change to a module it imports. That is the
    # worst failure this project can have: the differential oracle comparing
    # against the wrong answer and reporting agreement. Hashing the whole
    # directory is coarse for `tests/programs`, where each program imports only
    # the library, and exactly right for `boot`.
    for sibling in sorted(path.parent.rglob("*.tl")):
        h.update(str(sibling.relative_to(path.parent)).encode())
        h.update(sibling.read_bytes())
    cached = (Path(tempfile.gettempdir()) / "turkey-reference"
              / h.hexdigest()[:24])
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    value = compute()
    cached.parent.mkdir(parents=True, exist_ok=True)
    staging = cached.with_suffix(f".{os.getpid()}")
    staging.write_text(value, encoding="utf-8")
    os.replace(staging, cached)
    return value
