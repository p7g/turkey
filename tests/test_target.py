"""`Target.os` and `Target.arch`, and the `--target` option that sets them.

What a program can observe is in `docs/ref/modules.md`, whose examples
`test_reference` runs. Here: the command line, the claim the reference makes
about compiling -- that only the chosen target's arm of a `match Target.os`
survives to be compiled -- and that each target's output is what its
assembler accepts.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests import bootc, lang, toolchain

MATCHES = """\
import Target (OS(..), Arch(..))
import Target as Target

fun signalName(n : Int) -> String = match Target.os {
    Darwin -> if n == 10 { "SIGBUS" } else { "other" }
    Linux -> if n == 7 { "SIGBUS" } else { "other" }
}

fun wordBytes() -> Int = match Target.arch {
    Arm64 -> 8
}

fun main() {
    print(signalName(10))
    print(signalName(7))
    print(wordBytes())
}
"""

# What `MATCHES` prints compiled for each target: `boot`'s default is the
# target it was built for, which is what `$TURKEY_CC` links for.
MATCHED = {"arm64-darwin": "SIGBUS\nother\n8\n",
           "arm64-linux": "other\nSIGBUS\n8\n"}

SOURCE = bootc.REPO_ROOT / "tests" / "programs" / "constant_globals.gob"


def _boot(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(toolchain.command(bootc.binary(), *args),
                          cwd=bootc.REPO_ROOT, capture_output=True)


def test_the_default_is_the_host() -> None:
    chosen = _boot("native", "--target", toolchain.target(), str(SOURCE))
    default = _boot("native", str(SOURCE))
    assert chosen.returncode == 0, chosen.stderr
    assert chosen.stdout == default.stdout


def test_an_unknown_target_is_refused() -> None:
    result = _boot("check", "--target", "x86_64-linux", str(SOURCE))
    assert result.returncode == 2
    assert result.stderr.decode() == (
        "boot: unknown target 'x86_64-linux'; supported: arm64-darwin, "
        "arm64-linux\n")


def test_target_without_a_value_is_a_usage_error() -> None:
    result = _boot("check", str(SOURCE), "--target")
    assert result.returncode == 2
    assert result.stderr.decode().startswith("boot: usage: ")


def test_a_target_match_runs_the_chosen_arm() -> None:
    assert lang.output(MATCHES) == MATCHED[toolchain.target()]


def test_a_target_match_is_decided_before_lowering() -> None:
    """Nothing reads `Target.os` or `Target.arch` once the optimizer has run,
    and no `match` on either is left to compile."""
    result = lang.dump("opt", MATCHES)
    assert result.code == 0, result.stderr
    assert "Target#os" not in result.stdout
    assert "Target#arch" not in result.stdout
    assert "match" not in result.stdout


# Each target's arm calls the errno accessor only that target's libc defines.
# A `foreign` declaration may appear only in the library's `Unsafe.` modules,
# so this one is written into `lib/` for the test's duration.
ERRNO = """\
module Unsafe.Probe (found)

import Target (OS(..))
import Target as Target

foreign "__error" fun darwinLocation() -> Prim.Ptr

foreign "__errno_location" fun linuxLocation() -> Prim.Ptr

fun location() -> Prim.Ptr = match Target.os {
    Darwin -> darwinLocation()
    Linux -> linuxLocation()
}

fun found() -> Bool = !Prim.ptrIsNull(location())
"""


@pytest.fixture
def errno(request: pytest.FixtureRequest) -> Iterator[Path]:
    """A program that asks for errno's address through `ERRNO`, whose module
    is named for the test so that parallel tests do not share it."""
    stem = "Probe_" + re.sub(r"[^A-Za-z0-9_]", "_", request.node.name)[:60]
    path = lang.LIB / "Unsafe" / f"{stem}.gob"
    path.write_text(ERRNO.replace("Unsafe.Probe", f"Unsafe.{stem}"),
                    encoding="utf-8")
    try:
        yield lang.program(f"import Unsafe.{stem} as Errno\n\n"
                           "fun main() {\n    print(Errno.found())\n}\n")
    finally:
        path.unlink(missing_ok=True)


def _native(entry: Path, target: str) -> str:
    result = subprocess.run(
        toolchain.command(bootc.binary(), "native", "--target", target,
                          entry.name),
        cwd=entry.parent, env=dict(os.environ, TURKEY_LIB=str(lang.LIB)),
        capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout.decode()


@pytest.mark.parametrize(("target", "kept", "dropped"), [
    ("arm64-darwin", '"___error"', "__errno_location"),
    ("arm64-linux", '"__errno_location"', '"__error"'),
])
def test_only_the_chosen_targets_foreign_symbol_is_emitted(
        errno: Path, target: str, kept: str, dropped: str) -> None:
    """The other target's arm is checked and then not compiled, so its
    `foreign` symbol never reaches the output, and a link against this
    target's libc does not look for it."""
    text = _native(errno, target)
    assert kept in text
    assert dropped not in text


def test_the_other_targets_symbol_does_not_reach_the_link(errno: Path) -> None:
    assert lang.output(errno) == "True\n"


@pytest.mark.skipif(shutil.which("clang") is None, reason="needs clang")
def test_linux_output_assembles(tmp_path: Path) -> None:
    """ELF's assembler syntax: no underscore before a C name, `:lo12:` and
    `:got:` relocations, ELF section names. Assembling needs no sysroot, so
    this runs on any machine with clang."""
    assembly = tmp_path / "program.s"
    assembly.write_text(_native(SOURCE, "arm64-linux"))
    result = subprocess.run(
        ["clang", "--target=aarch64-linux-gnu", "-c", str(assembly),
         "-o", str(tmp_path / "program.o")], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
