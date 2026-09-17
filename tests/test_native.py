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
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import bootc
from turkey.driver import run

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOT_MAIN = REPO_ROOT / "boot" / "Main.gob"
PROGRAMS = REPO_ROOT / "tests" / "programs"
RUNTIME = REPO_ROOT / "runtime" / "turkey_runtime.c"
RUNTIME_HEADER = REPO_ROOT / "runtime" / "turkey_runtime.h"

# Every corpus program compiles and runs. The set is kept because naming what
# does not work is how the previous gaps got closed: a program listed here is a
# test that starts passing.
UNSUPPORTED: set[str] = set()

CORPUS = sorted(
    path.name for path in PROGRAMS.glob("*.gob")
    if not path.name.startswith("err_")
)
COMPILABLE = [name for name in CORPUS if name not in UNSUPPORTED]

# Binaries and the runtime object, shared by every worker and every session.
# Each file is keyed by the hash of what it was built from, so a stale one is
# never served and a warm one is never rebuilt.
CACHE = Path(tempfile.gettempdir()) / "turkey-native"


def _cc() -> str | None:
    return shutil.which("cc")


def _split_llvm(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "; === ")
    assert set(modules) == {p.name for p in paths}, (
        sorted({p.name for p in paths} - set(modules)))
    return [modules[p.name] for p in paths]


@functools.lru_cache(maxsize=None)
def _modules() -> dict[str, str]:
    """Every corpus program's LLVM IR, cached on disk per program.

    Interpreting `boot` here instead cost this module forty-six minutes to do
    about ten seconds of work; see `tests.bootc`. And keeping the result only in
    this process cost one corpus run per `pytest -n auto` worker.
    """
    paths = [PROGRAMS / name for name in CORPUS]
    texts = bootc.boot_each("llvm", paths, _split_llvm)
    return {path.name: texts[path] for path in paths}


def _digest(*parts: bytes) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return h.hexdigest()[:24]


def _replace_built(command: list[str], output: Path) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:4000]
    os.replace(command[command.index("-o") + 1], output)


@functools.lru_cache(maxsize=None)
def _runtime_object() -> Path:
    """`turkey_runtime.c`, compiled once rather than once per program.

    Every test binary used to compile the runtime from source beside its module
    -- the largest C file here, forty-odd times per worker.
    """
    key = _digest(RUNTIME.read_bytes(), RUNTIME_HEADER.read_bytes())
    output = CACHE / f"runtime-{key}.o"
    if not output.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        staging = CACHE / f"runtime-{key}.{os.getpid()}.o"
        _replace_built(["cc", "-std=c11", "-O1", "-c", "-o", str(staging),
                        str(RUNTIME)], output)
    return output


@functools.lru_cache(maxsize=None)
def _binary(name: str) -> Path:
    """One program, linked against the runtime, cached by what it is built from."""
    module = _modules()[name].encode("utf-8")
    runtime = _runtime_object()
    output = CACHE / f"{Path(name).stem}-{_digest(module, runtime.read_bytes())}.bin"
    if not output.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        stem = output.with_suffix(f".{os.getpid()}")
        source = stem.with_suffix(stem.suffix + ".ll")
        source.write_bytes(module)
        staging = stem.with_suffix(stem.suffix + ".bin")
        try:
            _replace_built(["cc", "-O1", "-o", str(staging), str(source),
                            str(runtime)], output)
        finally:
            source.unlink(missing_ok=True)
    return output


@functools.lru_cache(maxsize=None)
def _reference(name: str) -> str:
    """What the reference implementation prints, cached like `test_boot`'s."""
    source = PROGRAMS / name

    def compute() -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with contextlib.suppress(SystemExit):
                run(source.read_text(encoding="utf-8"), str(source), [])
        return out.getvalue()
    return bootc.reference("run", source, compute)


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


def test_nothing_is_refused():
    """No module reports a form or a primitive it could not emit.

    The refusal itself is still there and still matters -- a primitive with no
    rule would become a zero of the wrong type, which `cc` rejects somewhere
    unrelated and which would compile a *wrong program* wherever the types
    happened to line up (FINDINGS 63). This asserts it never fires.
    """
    refused = {name: text for name, text in _modules().items()
               if "; FAILED:" in text}
    assert not refused, sorted(refused)


def test_integer_overflow_panics():
    """`Int` arithmetic traps; `Prim.int*Wrapping` does not.

    Both are `Bin` in the low IR and they are *different opcodes*, because a
    backend that could not tell them apart put an overflow check on
    `Data.Map`'s hashing and panicked on a subtraction the language says wraps.
    """
    text = _modules()["operators.gob"]
    assert "llvm.sadd.with.overflow.i64" in text
    assert "integer overflow in +" in text


def test_a_panic_in_a_callee_stops_the_caller():
    """`turkey_panic` sets a flag and returns -- there is no unwinding.

    So a caller that did not look would carry on with a value the callee never
    produced. Every call is followed by a check.
    """
    text = _modules()["adt.gob"]
    assert "@turkey_has_panicked" in text


def _under_stress(name: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(_binary(name))], capture_output=True, text=True,
                          env={"TURKEY_GC_STRESS": "1", "PATH": "/usr/bin"})


@pytest.mark.parametrize("name", COMPILABLE)
def test_the_corpus_survives_collection(name):
    """The same programs, collecting at every allocation.

    A program allocating fewer than 1024 objects never collects, which is why
    the corpus passed for a while with no root frames at all -- the entire
    obligation was untested by an ordinary run. Under stress the collector runs
    between every pair of allocations, so anything the compiler failed to root
    is freed while still in use.

    Four things had to be rooted and the count found each one: the values live
    *across* a safepoint in every frame, the pointer-shaped globals, the
    interned string literals, and the slots past 64 that the live mask has no
    bits for. 0 of 28 -> 6 -> 11 -> 22 -> 27 -> 28.
    """
    if _cc() is None:
        pytest.skip("no C compiler")
    result = _under_stress(name)
    assert result.returncode == 0, result.stderr[-400:]
    assert result.stdout == _reference(name)



def test_block_parameters_became_phis():
    """The one structural conversion this emitter does.

    Worth asserting because it is the piece with an invariant behind it: only
    a `Jump` carries arguments, so a block's phis are read off its
    predecessors and no critical edge has to be split.
    """
    text = _modules()["loops.gob"]
    assert " = phi " in text


def test_symbols_are_the_compilers_own_names():
    """No mangling scheme. LLVM's quoted form takes every character Core uses."""
    text = _modules()["adt.gob"]
    assert '@"Main#main"' in text or '@"Main#main@' in text, text[:400]


def test_pointer_array_initialization_does_not_allocate_boxes():
    result = subprocess.run([str(_binary("shared_nullaries.gob"))],
                            env=dict(os.environ, TURKEY_GC_STATS="1",
                                     TURKEY_GC_STRESS="1"),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == _reference("shared_nullaries.gob")
    assert ", box 0," in result.stderr, result.stderr


def test_typed_record_stores_do_not_call_the_generic_runtime_setter():
    text = _modules()["record_stores.gob"]
    assert "call void @turkey_object_set(" not in text
    assert "store i64 " in text
