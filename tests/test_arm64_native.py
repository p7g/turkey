"""`boot native`: the corpus compiled by the independent arm64 backend, and run.

M28 phase 5b, and the first oracle this backend has had that checks *meaning*.
`tests/test_emit.py` checks that `as` accepts what is printed, which catches an
immediate out of range and a register spelled for the wrong file -- and accepts
a parallel copy that loses half its values (FINDINGS 95). Everything the
allocator, the frame layout and the emitter actually decide is invisible to it.

Whether each corpus program prints what it should is `test_programs`' check:
every program there is compiled by this backend and diffed against its
`.expected`. What is left here is what that cannot see -- the module's shape,
the frame table, and the same programs run collecting at every allocation,
which must print exactly what they print without it.
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import bootc, lang
from tests.bootc import CACHE, runtime_object
from tests.bootc import digest as _digest, replace_built as _replace_built

REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = REPO_ROOT / "tests" / "programs"

CORPUS = sorted(
    path.name for path in PROGRAMS.glob("*.gob")
    if not path.name.startswith("err_")
)

# Programs the arm64 path cannot run yet. Named rather than skipped silently,
# because a program listed here is a test that starts passing.
UNSUPPORTED: set[str] = set()

RUNNABLE = [name for name in CORPUS if name not in UNSUPPORTED]


def _split(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "// === ")
    assert set(modules) == {p.name for p in paths}, (
        sorted({p.name for p in paths} - set(modules)))
    return [modules[p.name] for p in paths]


@functools.lru_cache(maxsize=None)
def _all() -> dict[str, str]:
    """Every corpus program's assembly, from one `boot` process.

    Starting `boot` costs minutes and compiling a program costs a moment, so
    one process per program turns the corpus into an hour (FINDINGS 61, 65).
    """
    paths = [PROGRAMS / name for name in CORPUS]
    texts = bootc.boot_each("native", paths, _split)
    return {path.name: texts[path] for path in paths}


@functools.lru_cache(maxsize=None)
def _binary(name: str) -> Path:
    """One program, assembled and linked, cached by what it was built from."""
    assembly = _all()[name].encode("utf-8")
    runtime = runtime_object()
    output = CACHE / f"{Path(name).stem}-arm64-{_digest(assembly, runtime.read_bytes())}.bin"
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


def test_nothing_was_skipped():
    """A function the backend could not emit is a function that is not there.

    It would link -- the caller's `bl` resolves to nothing at all only at link
    time, and a missing *definition* is caught, but a function skipped in a
    module whose callers were also skipped is silent. This asserts the emitter
    refused nothing.
    """
    skipped = {name: [line for line in text.splitlines()
                      if line.startswith("// skipped ")]
               for name, text in _all().items()}
    assert not {k: v for k, v in skipped.items() if v}


def test_every_program_has_an_entry_and_a_root_array():
    """The module data is what makes the assembly a program rather than a file
    of functions, and each piece is silent when it is missing: an empty root
    array still links, and every string literal is then null."""
    for name, text in _all().items():
        assert "_turkey_module_roots:" in text, name
        assert "Lentry:" in text, name
        assert '.globl "_main"' in text, name


@pytest.mark.skipif(shutil.which("cc") is None, reason="no C compiler")
@pytest.mark.parametrize("name", RUNNABLE)
def test_the_corpus_agrees_under_gc_stress(name):
    """Collect at every allocation, which is what reads the frame table at all.

    Without stress a program may collect a handful of times or never, and the
    table is close to dead data: with the walker unwritten, 44 of 44 programs
    passed an unstressed run and **7** passed this one. It is also the only check
    that a root the compiler did not publish is caught -- `mark_grey` keeps the
    expensive membership test under stress, so a missed root becomes a panic on
    the first collection rather than a corruption later.
    """
    env = dict(os.environ, TURKEY_GC_STRESS="1")
    # From the program's own directory, as `test_programs` runs it: a program
    # may read a file by its bare name (`system.gob`).
    result = subprocess.run([str(_binary(name))], cwd=PROGRAMS,
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr[:2000]
    assert result.stdout == lang.output(PROGRAMS / name), (
        f"{name}: arm64 output under GC stress differs from the unstressed run")


def test_every_safepoint_label_is_in_the_frame_table():
    """The table's count against the labels the code actually carries.

    A table emitted empty -- or one entry short -- still links and still runs;
    what it does is lose an object under collection, somewhere else entirely.
    """
    for name, text in _all().items():
        lines = text.splitlines()
        at = lines.index("_turkey_frame_table:")
        declared = int(lines[at + 1].split()[1])
        labels = sum(1 for line in lines if line.startswith("Lsp"))
        assert declared == labels, (name, declared, labels)
        assert declared > 0, name
