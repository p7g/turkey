"""The interpreter's table of compiled bindings, `lib/Interpreter/Boundary.gob`.

`boot boundary HOST` generates it from the library, and `scripts/build.sh`
fails when that is not what is committed. These check what the generator
refuses (`src/Turkey/Hosted.gob`): a library change that interpreted code
could not cross, or that would split state between the host and the
interpreted program, must stop the build.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from tests import bootc

REPO_ROOT = bootc.REPO_ROOT


def _boundary(lib: Path, host: Path = REPO_ROOT / "src" / "Main.gob"):
    return subprocess.run(
        [str(bootc.binary()), "boundary", str(host)], cwd=REPO_ROOT,
        env=dict(os.environ, TURKEY_LIB=str(lib)), capture_output=True, text=True)


def _library(tmp_path: Path) -> Path:
    lib = tmp_path / "lib"
    shutil.copytree(REPO_ROOT / "lib", lib)
    return lib


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def test_the_committed_table_is_what_the_library_generates():
    ran = _boundary(REPO_ROOT / "lib")
    assert ran.returncode == 0, ran.stderr
    committed = (REPO_ROOT / "lib" / "Interpreter" / "Boundary.gob").read_text()
    assert ran.stdout == committed


def test_a_generic_entry_fails_the_build(tmp_path):
    lib = _library(tmp_path)
    poll = lib / "Turkey" / "Poll.gob"
    text = poll.read_text(encoding="utf-8")
    poll.write_text(text.replace("export (\n", "export (\n    identity,\n", 1)
                    + "\nfun identity(x : a) -> a = x\n", encoding="utf-8")
    _append(lib / "Turkey" / "Wait.gob", "\nfun probe() -> Int = Poll.identity(1)\n")
    ran = _boundary(lib)
    assert ran.returncode == 1
    assert "'Turkey.Poll.identity' is generic" in ran.stderr


def test_a_var_in_a_module_a_host_module_imports_fails_the_build(tmp_path):
    lib = _library(tmp_path)
    _append(lib / "Turkey" / "Sys.gob", "\nvar calls : Int = 0\n")
    ran = _boundary(lib)
    assert ran.returncode == 1
    assert "'Turkey.Sys.calls' is a module-level 'var'" in ran.stderr


def test_a_host_that_reads_standard_input_fails_the_build(tmp_path):
    host = tmp_path / "Host.gob"
    host.write_text("import System.File (stdin)\n\nfun main() { let _ = stdin() }\n",
                    encoding="utf-8")
    ran = _boundary(REPO_ROOT / "lib", host)
    assert ran.returncode == 1
    assert "the host reaches 'System.File.stdin'" in ran.stderr
