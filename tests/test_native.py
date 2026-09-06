"""`boot llvm`: the corpus compiled to native code, and run.

M27 phase 2, and the first point at which the bootstrap compiler produces
something that *executes*. Everything below Core was checked by `Ssa.verify`
until now, because there is no byte-identical oracle down there; here there is
a better one, and it is the one `NATIVE-BACKEND.md` names for this phase --
**differential execution**. Each program is compiled by `boot`, linked against
the runtime, run, and its output diffed against the reference implementation
running the same source.

Not against `tests/programs/*.expected`. That file is what `turkey run` prints,
which includes compile-time warnings on stderr, and a compiled binary has no
compile time. It is also a file someone can update; the reference
implementation is not (FINDINGS 64).

One `boot` process for the whole corpus. Starting `boot` costs about 2:42 and
compiling a program about half a second, so one process per program turns three
minutes of work into an hour -- which is FINDINGS 61 and, having been ignored
once more, FINDINGS 65. `boot llvm` prints a `; === <path>` marker before each
module so that several can share a run even though they cannot share a file.
"""

import contextlib
import functools
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from turkey.driver import run

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOT_MAIN = REPO_ROOT / "boot" / "Main.tl"
PROGRAMS = REPO_ROOT / "tests" / "programs"
RUNTIME = REPO_ROOT / "runtime" / "turkey_runtime.c"

# `system.tl` is the one program that does not compile yet, and it says why:
# `Prim.args` and `Prim.readFileBytes` answer raw storage that the lowering
# must wrap in an `Array`. Named here rather than silently skipped, so that
# making it work is a test that starts passing.
UNSUPPORTED = {"system.tl"}

CORPUS = sorted(
    path.name for path in PROGRAMS.glob("*.tl")
    if not path.name.startswith("err_")
)
COMPILABLE = [name for name in CORPUS if name not in UNSUPPORTED]


def _cc() -> str | None:
    return shutil.which("cc")


@functools.lru_cache(maxsize=None)
def _modules() -> dict[str, str]:
    """Every corpus program's LLVM IR, from one `boot` run."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        run(BOOT_MAIN.read_text(encoding="utf-8"), str(BOOT_MAIN),
            ["llvm", *(str(PROGRAMS / name) for name in CORPUS)])
    modules, current, name = {}, [], None
    for line in out.getvalue().splitlines(keepends=True):
        if line.startswith("; === "):
            if name is not None:
                modules[name] = "".join(current)
            name = Path(line[6:].strip()).name
            current = []
        else:
            current.append(line)
    if name is not None:
        modules[name] = "".join(current)
    assert set(modules) == set(CORPUS), sorted(set(CORPUS) - set(modules))
    return modules


@functools.lru_cache(maxsize=None)
def _workspace() -> Path:
    """One directory for every module and binary in this module's tests.

    Not a `tmp_path` fixture: those are per test, and `_binary` is cached
    across tests precisely so that linking happens once per program.
    """
    return Path(tempfile.mkdtemp(prefix="turkey-native-"))


@functools.lru_cache(maxsize=None)
def _binary(name: str) -> Path:
    """One program, linked against the runtime."""
    directory = _workspace()
    source = directory / (name + ".ll")
    source.write_text(_modules()[name], encoding="utf-8")
    binary = directory / (name + ".bin")
    result = subprocess.run(
        ["cc", "-std=c11", "-O1", "-o", str(binary), str(source), str(RUNTIME)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    return binary


@functools.lru_cache(maxsize=None)
def _reference(name: str) -> str:
    out = io.StringIO()
    source = PROGRAMS / name
    with contextlib.redirect_stdout(out):
        with contextlib.suppress(SystemExit):
            run(source.read_text(encoding="utf-8"), str(source), [])
    return out.getvalue()


@pytest.mark.parametrize("name", COMPILABLE)
def test_the_corpus_compiles_and_agrees_with_the_reference(name):
    """The whole property, in one assertion per program.

    A difference here is a miscompile: the same source, two backends, and the
    reference is the one that has been diffed against a second implementation
    at every stage above Core.
    """
    if _cc() is None:
        pytest.skip("no C compiler")
    binary = _binary(name)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.stdout == _reference(name), (
        f"{name}: native output differs from the reference implementation")


@pytest.mark.parametrize("name", sorted(UNSUPPORTED))
def test_what_does_not_compile_says_so(name):
    """An unsupported program is refused, not silently miscompiled.

    A primitive with no emission rule used to become a zero of the wrong type,
    which `cc` rejected somewhere unrelated -- and which would have become a
    wrong program wherever the types happened to line up (FINDINGS 63).
    """
    text = _modules()[name]
    assert "; FAILED:" in text, text[:400]
    assert "no rule for" in text


def test_block_parameters_became_phis():
    """The one structural conversion this emitter does.

    Worth asserting because it is the piece with an invariant behind it: only
    a `Jump` carries arguments, so a block's phis are read off its
    predecessors and no critical edge has to be split.
    """
    text = _modules()["loops.tl"]
    assert " = phi " in text


def test_symbols_are_the_compilers_own_names():
    """No mangling scheme. LLVM's quoted form takes every character Core uses."""
    text = _modules()["adt.tl"]
    assert '@"Main#main"' in text or '@"Main#main@' in text, text[:400]
