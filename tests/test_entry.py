"""The entry of a compiled program, which is Turkey: `lib/Turkey/Entry.gob`.

TIX-67 moved `turkey_main`, the big-stack thread and the crash handler out of
the C runtime, and its done-when was that a program that panics prints the
same trace it did. The corpus's `err_*` programs hold those traces, but
`tests/test_programs.py` runs them under the JIT, whose host is Python and
never goes through the entry at all. So this builds each one as a binary, under
each backend, which is the only way to run the code that moved.

Two things about today's traces are pinned here rather than fixed:

* a binary names each frame by its internal name, `Data.Array#outOfBounds`,
  where the JIT's host prints what the author wrote. The C printed them that
  way too, and so the comparison with `.expected` is made after the JIT's own
  shortening;
* `boot`'s backends emit no panic sites, so their binaries print the message
  and no frames at all. That was true of the C as well.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import bootc
from turkey.errors import short

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = REPO_ROOT / "tests" / "programs"
RUNTIME = REPO_ROOT / "runtime" / "turkey_runtime.c"

# The corpus programs that compile and then panic, which are the ones with a
# trace to print.
PANICS = ["err_out_of_bounds", "err_string_boundary", "err_uninitialized_read"]

pytestmark = pytest.mark.skipif(shutil.which("cc") is None,
                                reason="no C compiler")


def _python_binary(source: Path, output: Path) -> Path:
    built = subprocess.run(
        # By its name, from its directory, as `.expected` names the file.
        [sys.executable, "-m", "turkey", "build", source.name, "-o", str(output)],
        cwd=source.parent, env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    return output


def _boot_binary(backend: str, source: Path, tmp_path: Path) -> Path:
    """`boot native` or `boot llvm`, linked against the runtime."""
    # From the repository, where `boot` finds `lib/`.
    text = subprocess.run([str(bootc.binary()), backend, str(source)],
                          cwd=REPO_ROOT, capture_output=True, text=True,
                          check=True).stdout
    code = tmp_path / (source.stem + (".s" if backend == "native" else ".ll"))
    code.write_text(text, encoding="utf-8")
    runtime = tmp_path / "runtime.o"
    if not runtime.exists():
        subprocess.run(["cc", "-std=c11", "-O1", "-c", "-o", str(runtime),
                        str(RUNTIME)], check=True)
    output = tmp_path / f"{source.stem}-{backend}"
    subprocess.run(["cc", "-O1", "-Wno-override-module", "-o", str(output),
                    str(code), str(runtime)], check=True)
    return output


@pytest.mark.parametrize("name", PANICS)
def test_a_built_program_prints_the_trace_the_jit_does(name, tmp_path):
    """Frame for frame, file for file, line and column for line and column."""
    binary = _python_binary(PROGRAMS / f"{name}.gob", tmp_path / name)
    ran = subprocess.run([str(binary)], capture_output=True, text=True)
    assert ran.returncode == 1
    assert ran.stdout == ""
    expected = (PROGRAMS / f"{name}.expected").read_text(encoding="utf-8")
    assert short(ran.stderr) == expected


@pytest.mark.parametrize("backend", ["native", "llvm"])
@pytest.mark.parametrize("name", PANICS)
def test_a_boot_binary_reports_the_panic(name, backend, tmp_path):
    binary = _boot_binary(backend, PROGRAMS / f"{name}.gob", tmp_path)
    ran = subprocess.run([str(binary)], capture_output=True, text=True)
    assert ran.returncode == 1
    expected = (PROGRAMS / f"{name}.expected").read_text(encoding="utf-8")
    assert ran.stderr == expected.splitlines(keepends=True)[0]


ARGS_AND_EXIT = """\
import System.Env as Env

fun main() {
    let given = Env.args()
    print(Int.toString(len(given)))
    for a in given { print(a) }
    Env.exit(3)
}
"""


@pytest.mark.parametrize("backend", ["native", "llvm"])
def test_a_boot_binary_is_handed_its_arguments_and_exits_with_its_status(
        backend, tmp_path):
    """What the entry does before and after the program: `argv + 1` handed
    over, and an exit's status rather than a panic's. The Python backend's
    half is `test_llvmgen`'s."""
    source = tmp_path / "args.gob"
    source.write_text(ARGS_AND_EXIT, encoding="utf-8")
    binary = _boot_binary(backend, source, tmp_path)
    ran = subprocess.run([str(binary), "one", "t w o"], capture_output=True,
                         text=True)
    assert ran.stdout == "2\none\nt w o\n"
    assert ran.returncode == 3


FAULT = """\
import Unsafe.Ptr as Ptr

fun deeper(n : Int) -> Int {
    if n == 0 {
        let x : Int = Ptr.load(Ptr.fromInt(8), 0)
        return x
    }
    let xs = [n]
    deeper(n - 1) + xs[0]
}

fun main() {
    print(Int.toString(deeper(3)))
}
"""

HEADER = ("\n*** SIGSEGV in generated code\n  innermost call sites:\n")


def _fault(binary: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(binary)], capture_output=True, text=True,
                          env=dict(os.environ, TURKEY_SEGV_FRAMES="1"))


def test_a_fault_is_reported_from_the_shadow_stacks(tmp_path):
    """The crash handler, installed on request: a signal handler that is a
    Turkey function, called by the kernel on the stack that faulted."""
    source = tmp_path / "fault.gob"
    source.write_text(FAULT, encoding="utf-8")
    ran = _fault(_python_binary(source, tmp_path / "fault"))
    assert ran.returncode == 139
    assert ran.stderr.startswith(HEADER)
    # Both chains: the call sites, with positions, and the enclosing frames.
    assert "    Main#deeper (" in ran.stderr and "fault.gob:9:5)\n" in ran.stderr
    assert "  enclosing functions, innermost first:\n" in ran.stderr
    assert "    turkeyfn_Main_23_main\n" in ran.stderr


def test_a_fault_is_reported_under_the_jit_too(tmp_path):
    """The JIT's host installs the same handler, which is a Turkey function
    the engine compiled, looked up by its C symbol."""
    source = tmp_path / "fault.gob"
    source.write_text(FAULT, encoding="utf-8")
    ran = subprocess.run([sys.executable, "-m", "turkey", "run", str(source)],
                         capture_output=True, text=True,
                         env=dict(os.environ, TURKEY_SEGV_FRAMES="1",
                                  PYTHONPATH=str(REPO_ROOT)))
    assert ran.returncode == 139
    assert ran.stderr.startswith(HEADER)


@pytest.mark.parametrize("backend", ["native", "llvm"])
def test_a_boot_binary_reports_a_fault(backend, tmp_path):
    source = tmp_path / "fault.gob"
    source.write_text(FAULT, encoding="utf-8")
    ran = _fault(_boot_binary(backend, source, tmp_path))
    assert ran.returncode == 139
    assert ran.stderr.startswith(HEADER)
    # The globals' frame is at the bottom of every chain, on every backend.
    assert ran.stderr.endswith("    <globals>\n")


def test_without_the_request_a_fault_is_the_operating_systems(tmp_path):
    source = tmp_path / "fault.gob"
    source.write_text(FAULT, encoding="utf-8")
    binary = _boot_binary("native", source, tmp_path)
    ran = subprocess.run([str(binary)], capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items()
                              if k != "TURKEY_SEGV_FRAMES"})
    assert ran.returncode < 0
    assert "SIGSEGV in generated code" not in ran.stderr
