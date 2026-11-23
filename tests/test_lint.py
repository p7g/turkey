"""The checks' levels, and the code they run on.

What each check finds is recorded by the goldens under `tests/lint/`. These
tests are about what the goldens cannot say: the level flags, which a golden
has no way to pass, and which modules are own code, which depends on where
the library is.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

# A program with one finding of each of two checks.
TWO_FINDINGS = """\
fun stop(done : Bool) -> Unit {
    if done { return {} }
    print("going on")
}

fun main() {
    stop(False)
    return
    print("never")
}
"""

# A module with a finding, for the library or for the program's own root.
EXTRA = """\
export (stop)

fun stop(done : Bool) -> Unit {
    if done { return {} }
    print("going on")
}
"""

USES_EXTRA = """\
import Extra

fun main() {
    Extra.stop(False)
}
"""


def turkey(directory: Path, *argv: str, lib: Path = bootc.REPO_ROOT / "lib") -> subprocess.CompletedProcess:
    # The suite names the library relative to the repository root, and these
    # run elsewhere.
    environment = dict(os.environ, TURKEY_LIB=str(lib))
    return subprocess.run(toolchain.command(bootc.binary(), *argv), cwd=directory,
                          env=environment, capture_output=True, text=True, timeout=120)


@pytest.fixture
def program(tmp_path: Path) -> Path:
    (tmp_path / "main.gob").write_text(TWO_FINDINGS)
    return tmp_path


def test_warnings_print_with_their_names_and_a_count(program: Path) -> None:
    result = turkey(program, "check", "main.gob")
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "main.gob:2:22: warning: a bare 'return' already means 'return ()'; remove '{}'"
        " [empty-return-value]\n"
        "main.gob:9:5: warning: this statement is unreachable, since the one before it"
        " cannot complete; remove it [unreachable-code]\n"
        "2 warnings\n")


def test_build_prints_them_too(program: Path) -> None:
    result = turkey(program, "build", "-o", "main", "main.gob")
    assert result.returncode == 0, result.stderr
    assert result.stderr.endswith("2 warnings\n")


def test_allow_drops_a_check(program: Path) -> None:
    result = turkey(program, "check", "--allow", "unreachable-code", "main.gob")
    assert result.returncode == 0, result.stderr
    assert "[unreachable-code]" not in result.stderr
    assert result.stderr.endswith("1 warning\n")


def test_allow_takes_a_list(program: Path) -> None:
    result = turkey(program, "check", "--allow", "unreachable-code,empty-return-value", "main.gob")
    assert (result.returncode, result.stderr) == (0, "")


def test_deny_fails_the_compile(program: Path) -> None:
    result = turkey(program, "build", "-o", "main", "--deny", "empty-return-value", "main.gob")
    assert result.returncode == 1
    assert "main.gob:2:22: error: a bare 'return'" in result.stderr
    assert result.stderr.endswith("1 error, 1 warning\n")
    assert not (program / "main").exists()


def test_the_last_flag_for_a_name_wins(program: Path) -> None:
    result = turkey(program, "check", "--deny", "empty-return-value",
                    "--warn", "empty-return-value", "main.gob")
    assert result.returncode == 0, result.stderr


def test_deny_warnings_makes_every_warning_an_error(program: Path) -> None:
    result = turkey(program, "check", "--deny-warnings", "--allow", "unreachable-code", "main.gob")
    assert result.returncode == 1
    assert result.stderr.endswith("1 error\n")


def test_an_unknown_check_is_refused(program: Path) -> None:
    result = turkey(program, "check", "--allow", "no-such-check", "main.gob")
    assert result.returncode == 2
    assert "unknown check 'no-such-check'" in result.stderr


def test_build_does_not_fix(program: Path) -> None:
    result = turkey(program, "build", "--fix", "main.gob")
    assert result.returncode == 2
    assert "use 'turkey check --fix'" in result.stderr


def test_fix_rewrites_the_file_and_reports_what_is_left(program: Path) -> None:
    result = turkey(program, "check", "--fix", "main.gob")
    assert result.returncode == 0, result.stderr
    assert "    if done { return }\n" in (program / "main.gob").read_text()
    assert result.stderr.startswith("fixed 1 finding\n")
    assert result.stderr.endswith("[unreachable-code]\n1 warning\n")


def test_fix_leaves_an_allowed_check_alone(program: Path) -> None:
    result = turkey(program, "check", "--fix", "--allow", "empty-return-value", "main.gob")
    assert result.returncode == 0, result.stderr
    assert (program / "main.gob").read_text() == TWO_FINDINGS


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """A copy of the library with a module of our own in it."""
    lib = tmp_path / "lib"
    shutil.copytree(bootc.REPO_ROOT / "lib", lib)
    (lib / "Extra.gob").write_text(EXTRA)
    return lib


def test_the_library_is_not_linted(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "project" / "app"
    project.mkdir(parents=True)
    (project / "main.gob").write_text(USES_EXTRA)
    result = turkey(project, "check", "--deny-warnings", "main.gob", lib=library)
    assert (result.returncode, result.stderr) == (0, "")


def test_a_module_beside_the_entry_is(tmp_path: Path) -> None:
    (tmp_path / "Extra.gob").write_text(EXTRA)
    (tmp_path / "main.gob").write_text(USES_EXTRA)
    result = turkey(tmp_path, "check", "main.gob")
    assert result.returncode == 0, result.stderr
    assert result.stderr.startswith("Extra.gob:4:22: warning: a bare 'return'")


def test_the_library_is_linted_from_beside_it(tmp_path: Path, library: Path) -> None:
    # `src/` beside `lib/`, as the compiler's own source is: the library is
    # what is being built.
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.gob").write_text(USES_EXTRA)
    result = turkey(source, "check", "main.gob", lib=library)
    assert result.returncode == 0, result.stderr
    assert "Extra.gob:4:22: warning: a bare 'return'" in result.stderr
