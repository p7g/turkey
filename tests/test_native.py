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

import functools
import os
import subprocess
from pathlib import Path

import pytest

from tests import bootc, lang, toolchain

REPO_ROOT = Path(__file__).resolve().parents[1]
BOOT_MAIN = REPO_ROOT / "src" / "Main.gob"
PROGRAMS = REPO_ROOT / "tests" / "programs"

# Every corpus program compiles and runs. The set is kept because naming what
# does not work is how the previous gaps got closed: a program listed here is a
# test that starts passing.
UNSUPPORTED: set[str] = set()

CORPUS = sorted(
    path.name for path in PROGRAMS.glob("*.gob")
    if not path.name.startswith("err_")
)
COMPILABLE = [name for name in CORPUS if name not in UNSUPPORTED]


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


# Shared with `test_arm64_native` and `test_bootstrap`; see `tests.bootc`.
CACHE = bootc.CACHE
_digest = bootc.digest
_replace_built = bootc.replace_built
_runtime_object = bootc.runtime_object


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
            _replace_built([*toolchain.cc(), "-O1", "-o", str(staging),
                            str(source), str(runtime)], output)
        finally:
            source.unlink(missing_ok=True)
    return output


@functools.lru_cache(maxsize=None)
def _reference(name: str) -> str:
    """What the same program prints compiled by `boot`'s arm64 backend.

    The two backends share everything above the low IR and nothing below it,
    so a disagreement is a miscompile by one of them. The arm64 output is in
    turn checked against the recorded `.expected` by `test_programs`. This used
    to be the Python implementation running the source (TIX-94).
    """
    return lang.output(PROGRAMS / name)


@pytest.mark.parametrize("name", COMPILABLE)
def test_the_corpus_compiles_and_agrees_with_the_reference(name):
    """The whole property, in one assertion per program.

    A difference here is a miscompile: the same source, two backends, and the
    reference is the one that has been diffed against a second implementation
    at every stage above Core.
    """
    if toolchain.missing():
        pytest.skip("no C compiler")
    binary = _binary(name)
    # From the program's own directory, as `test_programs` runs it: a program
    # may read a file by its bare name (`system.gob`).
    result = subprocess.run(toolchain.command(binary), cwd=PROGRAMS,
                            capture_output=True, text=True)
    assert result.stdout == _reference(name), (
        f"{name}: the LLVM backend's output differs from the arm64 backend's")


@pytest.mark.parametrize("name", COMPILABLE)
def test_the_corpus_agrees_under_gc_stress(name):
    if toolchain.missing():
        pytest.skip("no C compiler")
    result = subprocess.run(toolchain.command(_binary(name)), cwd=PROGRAMS,
                            env=dict(os.environ, TURKEY_GC_STRESS="1"),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[:2000]
    assert result.stdout == _reference(name)


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
    return subprocess.run(toolchain.command(_binary(name)), cwd=PROGRAMS,
                          capture_output=True, text=True,
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
    if toolchain.missing():
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
    result = subprocess.run(toolchain.command(_binary("shared_nullaries.gob")),
                            cwd=PROGRAMS,
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
