"""How the compiler finds its standard library: `$TURKEY_LIB` if it is set,
and otherwise the nearest `lib/` holding `Prelude.gob` at or above its own
executable, symbolic links resolved. The working directory is never searched.

The compiler under test is cached under the temporary directory, where no
`lib/` lies above it, so each test lays out a checkout of its own: a copy of
the compiler at `tree/build/stages/turkey` and `tree/lib` a link to the
repository's library. Every run here has `TURKEY_LIB` removed unless the test
is about it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

REPO_ROOT = Path(__file__).resolve().parents[1]

HELLO = 'fun main() { print("hi") }\n'

NOT_FOUND = "turkey: cannot find the standard library: "


def _environment(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "TURKEY_LIB"}
    env.update(extra)
    return env


def _run(argv: list[str], cwd: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=_environment(**extra),
                          capture_output=True, text=True, timeout=600)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A checkout's layout: the compiler two levels below a `lib`."""
    stages = tmp_path / "tree" / "build" / "stages"
    stages.mkdir(parents=True)
    shutil.copy2(bootc.binary(), stages / "turkey")
    (tmp_path / "tree" / "lib").symlink_to(REPO_ROOT / "lib")
    return tmp_path / "tree"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A program's own directory outside the checkout, with a `lib/` of its
    own that would break every compile if the compiler took it for the
    library."""
    directory = tmp_path / "project"
    (directory / "lib").mkdir(parents=True)
    (directory / "lib" / "Prelude.gob").write_text("this is not Turkey\n")
    (directory / "01.gob").write_text(HELLO)
    return directory


def test_symlink_on_path_from_another_directory(tree: Path, project: Path, tmp_path: Path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "turkey").symlink_to(tree / "build" / "stages" / "turkey")
    # By bare name through `PATH`, as a shell runs it, unless a prefix has to
    # run it, which needs a path.
    prefix = os.environ.get("TURKEY_RUN", "").strip()
    turkey = str(bin_ / "turkey") if prefix else "turkey"
    path = f"{bin_}{os.pathsep}{os.environ.get('PATH', '')}"
    built = _run(toolchain.command(Path(turkey), "build", "-o", "01", "01.gob"),
                 project, PATH=path)
    assert built.returncode == 0, built.stderr
    ran = _run(toolchain.command(project / "01"), project)
    assert (ran.returncode, ran.stdout) == (0, "hi\n"), ran.stderr


def test_root_under_working_directory_is_named_relative(tree: Path, project: Path):
    # The search list of a failed import shows the library root as the
    # compiler names it.
    (project / "missing.gob").write_text("import NoSuchModule\nfun main() {}\n")
    checked = _run(toolchain.command(tree / "build" / "stages" / "turkey",
                                     "check", str(project / "missing.gob")), tree)
    assert checked.returncode != 0
    assert f"(searched {project}, lib)" in checked.stderr, checked.stderr


def test_turkey_lib_overrides(tree: Path, project: Path, tmp_path: Path):
    library = tmp_path / "custom"
    shutil.copytree(REPO_ROOT / "lib", library)
    (library / "Probe_LibLookup.gob").write_text(
        'export (greeting)\n\nfun greeting() -> String = "from the override"\n')
    (project / "probe.gob").write_text(
        "import Probe_LibLookup (greeting)\nfun main() { print(greeting()) }\n")
    compiler = tree / "build" / "stages" / "turkey"
    without = _run(toolchain.command(compiler, "check", "probe.gob"), project)
    assert "cannot find module 'Probe_LibLookup'" in without.stderr, without.stderr
    built = _run(toolchain.command(compiler, "build", "-o", "probe", "probe.gob"),
                 project, TURKEY_LIB=str(library))
    assert built.returncode == 0, built.stderr
    ran = _run(toolchain.command(project / "probe"), project)
    assert ran.stdout == "from the override\n", ran.stderr


def test_bad_turkey_lib(tree: Path, project: Path, tmp_path: Path):
    checked = _run(toolchain.command(tree / "build" / "stages" / "turkey", "check", "01.gob"),
                   project, TURKEY_LIB=str(tmp_path / "nowhere"))
    assert checked.returncode == 2
    assert checked.stderr == (
        f"{NOT_FOUND}TURKEY_LIB is '{tmp_path / 'nowhere'}', which has no Prelude.gob; "
        "set TURKEY_LIB to the 'lib' directory of a Turkey checkout\n")


def test_no_library_above_the_executable(project: Path, tmp_path: Path):
    alone = tmp_path / "alone"
    alone.mkdir()
    shutil.copy2(bootc.binary(), alone / "turkey")
    checked = _run(toolchain.command(alone / "turkey", "check", "01.gob"), project)
    assert checked.returncode == 2
    # Everything above the copy is the temporary directory's ancestors, none
    # of which is expected to hold a library.
    assert checked.stderr == (
        f"{NOT_FOUND}no lib/Prelude.gob in '{alone.resolve()}' or a directory above it; "
        "set TURKEY_LIB to the 'lib' directory of a Turkey checkout\n")
    assert "type error" not in checked.stderr


def test_executable_path_is_absolute_and_resolved(tree: Path, tmp_path: Path):
    program = tmp_path / "where.gob"
    program.write_text(
        "import System.Env\n"
        "fun main() = match System.Env.executablePath() {\n"
        "    Some(path) -> print(path)\n"
        '    None -> print("none")\n'
        "}\n")
    real = tmp_path / "real" / "where"
    real.parent.mkdir()
    built = _run(toolchain.command(tree / "build" / "stages" / "turkey",
                                   "build", "-o", str(real), str(program)), tmp_path)
    assert built.returncode == 0, built.stderr
    link = tmp_path / "link"
    link.symlink_to(real)
    ran = _run(toolchain.command(link), tmp_path)
    assert ran.returncode == 0, ran.stderr
    path = ran.stdout.strip()
    assert os.path.isabs(path)
    assert path == os.path.realpath(real)


def test_empty_turkey_lib_is_unset(tree: Path, project: Path):
    checked = _run(toolchain.command(tree / "build" / "stages" / "turkey", "check", "01.gob"),
                   project, TURKEY_LIB="")
    assert checked.returncode == 0, checked.stderr


def test_sibling_with_working_directory_as_prefix_is_absolute(tree: Path, project: Path):
    # `tre` is a prefix of `tree` by characters but not by components, so the
    # root is not under it and keeps its absolute name.
    working = tree.parent / "tre"
    working.mkdir()
    (project / "missing.gob").write_text("import NoSuchModule\nfun main() {}\n")
    checked = _run(toolchain.command(tree / "build" / "stages" / "turkey",
                                     "check", str(project / "missing.gob")), working)
    assert checked.returncode != 0
    root = (tree / "lib").parent.resolve() / "lib"
    assert f"(searched {project}, {root})" in checked.stderr, checked.stderr


def _install(bindir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", str(REPO_ROOT / "scripts" / "install.sh"), str(bindir)],
                          capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(not (REPO_ROOT / "build" / "stages" / "turkey").exists(),
                    reason="install.sh links build/stages/turkey, which is not built")
def test_install_replaces_only_a_turkey_link(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "turkey").symlink_to("/elsewhere/build/stages/turkey")
    replaced = _install(bindir)
    assert replaced.returncode == 0, replaced.stderr
    assert os.readlink(bindir / "turkey") == str(REPO_ROOT / "build" / "stages" / "turkey")

    # A checkout whose `build.sh` names its result `stage2` leaves such a link.
    (bindir / "turkey").unlink()
    (bindir / "turkey").symlink_to("/elsewhere/build/stages/stage2")
    replaced = _install(bindir)
    assert replaced.returncode == 0, replaced.stderr
    assert os.readlink(bindir / "turkey") == str(REPO_ROOT / "build" / "stages" / "turkey")

    (bindir / "turkey").unlink()
    (bindir / "turkey").symlink_to("/usr/bin/true")
    refused = _install(bindir)
    assert refused.returncode == 1
    assert "not a Turkey compiler" in refused.stderr
    assert os.readlink(bindir / "turkey") == "/usr/bin/true"
