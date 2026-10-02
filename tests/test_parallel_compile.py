"""`boot` with several workers (`TURKEY_WORKERS`) compiles the parts of a
program that are each function's own -- the passes after lowering, the
checks, and the backend -- as tasks in parallel, and hands them on in order.
What it emits must be what one worker emits, byte for byte.
"""

import os
import subprocess

import pytest

from tests import bootc, toolchain

REPO_ROOT = bootc.REPO_ROOT
PROGRAMS = REPO_ROOT / "tests" / "programs"


def _emit(path, workers: str) -> str:
    env = dict(os.environ, TURKEY_WORKERS=workers)
    result = subprocess.run(toolchain.command(bootc.binary(), *bootc.argv("asm"), str(path)),
                            cwd=REPO_ROOT, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr[:4000]
    return result.stdout


@pytest.mark.parametrize("name", ["adt.gob", "closure_abi.gob", "pressure.gob"])
def test_a_program_compiles_the_same_on_several_workers(name):
    path = PROGRAMS / name
    assert _emit(path, "4") == _emit(path, "1")


@pytest.mark.bootstrap
def test_boot_compiles_itself_the_same_on_several_workers():
    assert _emit(bootc.BOOT_MAIN, "8") == _emit(bootc.BOOT_MAIN, "1")
