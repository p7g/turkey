"""Exercise Turkey.Entry's argument, exit, panic and crash handling.

Both boot backends produce standalone binaries. Panic sites are not emitted,
so panic reports contain the message alone; crashes also expose root frames.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

REPO_ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = REPO_ROOT / "tests" / "programs"
RUNTIME = REPO_ROOT / "runtime" / "turkey_runtime.c"

# The corpus programs that compile and then panic, which are the ones with a
# trace to print.
PANICS = ["err_out_of_bounds", "err_string_boundary", "err_uninitialized_read"]

pytestmark = pytest.mark.skipif(toolchain.missing(),
                                reason="no C compiler")


def _boot_binary(backend: str, source: Path, tmp_path: Path) -> Path:
    """`boot native` or `boot llvm`, linked against the runtime."""
    # From the repository, where `boot` finds `lib/`.
    text = subprocess.run(
        toolchain.command(bootc.binary(), backend, str(source)),
        cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    code = tmp_path / (source.stem + (".s" if backend == "native" else ".ll"))
    code.write_text(text, encoding="utf-8")
    runtime = tmp_path / "runtime.o"
    if not runtime.exists():
        subprocess.run([*toolchain.cc(), "-std=c11", "-O1", "-c", "-o",
                        str(runtime), str(RUNTIME)], check=True)
    output = tmp_path / f"{source.stem}-{backend}"
    subprocess.run([*toolchain.cc(), "-O1", "-Wno-override-module", "-o",
                    str(output), str(code), str(runtime)], check=True)
    return output


@pytest.mark.parametrize("backend", ["native", "llvm"])
@pytest.mark.parametrize("name", PANICS)
def test_a_boot_binary_reports_the_panic(name, backend, tmp_path):
    binary = _boot_binary(backend, PROGRAMS / f"{name}.gob", tmp_path)
    ran = subprocess.run(toolchain.command(binary), capture_output=True,
                         text=True)
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
    over, and an exit's status rather than a panic's."""
    source = tmp_path / "args.gob"
    source.write_text(ARGS_AND_EXIT, encoding="utf-8")
    binary = _boot_binary(backend, source, tmp_path)
    ran = subprocess.run(toolchain.command(binary, "one", "t w o"),
                         capture_output=True, text=True)
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
    return subprocess.run(toolchain.command(binary), capture_output=True,
                          text=True,
                          env=dict(os.environ, TURKEY_SEGV_FRAMES="1"))


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
    ran = subprocess.run(
        toolchain.command(binary), capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "TURKEY_SEGV_FRAMES"})
    assert ran.returncode < 0
    assert "SIGSEGV in generated code" not in ran.stderr
