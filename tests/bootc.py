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

import atexit
import functools
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOT_MAIN = REPO_ROOT / "boot" / "Main.tl"


@functools.lru_cache(maxsize=1)
def binary() -> Path:
    """The compiled `boot`, built on first use and kept for the session.

    `turkey build` rather than compiling in-process and calling it, for two
    reasons that have not changed: a subprocess per invocation keeps a crash
    in `boot` from taking the test session with it, which matters while `boot`
    still has one -- and it had one this week, on a nine-argument call -- and a
    real executable is what self-hosting needs anyway.
    """
    directory = Path(tempfile.mkdtemp(prefix="turkey-boot-"))
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    output = directory / "boot"
    result = subprocess.run(
        [sys.executable, "-m", "turkey", "build", str(BOOT_MAIN),
         "-o", str(output)],
        cwd=REPO_ROOT,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"building boot failed\n{result.stdout}\n{result.stderr}")
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
