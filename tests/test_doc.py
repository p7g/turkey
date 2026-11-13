"""`boot doc` over a fixture, against the JSON recorded for it.

`tests/doc/Fixture.gob` declares one of each kind of entity `boot doc`
describes, re-exports from `tests/doc/Shapes.gob`, and places `---` doc
comments on each edge of the attachment rule (`src/Turkey/DocComments.gob`):
above a field, a constructor, a method, an associated type and an instance;
the module comment; and doc comments that document nothing, beside ordinary,
rule and block comments that are never docs.

To record new output, run with `TURKEY_BLESS_DOC=1`, and read the diff of
`tests/doc/docs.json` before committing it.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tests import bootc, toolchain

FIXTURES = bootc.REPO_ROOT / "tests" / "doc"
GOLDEN = FIXTURES / "docs.json"


def _doc(*files: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(toolchain.command(bootc.binary(), "doc", *files),
                          cwd=bootc.REPO_ROOT, capture_output=True, text=True,
                          timeout=120)


def test_the_fixture_matches_its_golden() -> None:
    result = _doc("tests/doc/Fixture.gob", "tests/doc/Shapes.gob")
    assert result.returncode == 0, result.stderr
    if os.environ.get("TURKEY_BLESS_DOC"):
        GOLDEN.write_text(result.stdout)
    assert result.stdout == GOLDEN.read_text()


def test_the_output_does_not_depend_on_the_order_of_the_files() -> None:
    forwards = _doc("tests/doc/Fixture.gob", "tests/doc/Shapes.gob")
    backwards = _doc("tests/doc/Shapes.gob", "tests/doc/Fixture.gob")
    assert forwards.stdout == backwards.stdout


def test_every_library_function_has_a_type() -> None:
    files = sorted(str(p.relative_to(bootc.REPO_ROOT))
                   for p in (bootc.REPO_ROOT / "lib").rglob("*.gob")
                   if "Turkey" not in p.relative_to(bootc.REPO_ROOT / "lib").parts[:1])
    result = _doc(*files)
    assert result.returncode == 0, result.stderr
    modules = json.loads(result.stdout)["modules"]
    assert len(modules) == len(files)
    untyped = [(m["name"], item["name"]) for m in modules for item in m["items"]
               if item["kind"] in ("function", "value") and item["type"] is None]
    assert untyped == []


def test_modules_outside_the_library_must_share_a_directory(tmp_path) -> None:
    other = tmp_path / "Other.gob"
    other.write_text("export (x)\nlet x = 1\n")
    result = _doc("tests/doc/Fixture.gob", str(other))
    assert result.returncode == 2
    assert "document each directory separately" in result.stderr


@pytest.mark.parametrize("source", [
    "fun f() -> Int = 1\n\n--- Cut off by a blank line.\n\nfun main() {}\n",
    "fun main() {}\n---\n",
    "--- The module.\n\n--- Above an import.\nimport Data.Set as Set\n\nfun main() {}\n",
    "fun main() {\n    --- Inside a body.\n    let x = 1\n    print(x)\n}\n",
    "type T = T(Int)\n\ninstance Show T {\n    --- An instance's method.\n"
    "    fun show(t : T) -> String = \"T\"\n}\n\nfun main() {}\n",
    "fun main() {} --- After code.\n",
], ids=["blank line", "at the end", "import", "body", "instance method", "trailing"])
def test_a_doc_comment_that_documents_nothing_is_an_ordinary_comment(tmp_path,
                                                                   source: str) -> None:
    entry = tmp_path / "main.gob"
    entry.write_text(source)
    result = subprocess.run(toolchain.command(bootc.binary(), "check", str(entry)),
                            cwd=bootc.REPO_ROOT, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("spelling", [
    "lib/Data/Option.gob",
    "./lib/Data/Option.gob",
    "lib/../lib/Data/Option.gob",
    str(bootc.REPO_ROOT / "lib" / "Data" / "Option.gob"),
], ids=["plain", "dot", "dot-dot", "absolute"])
def test_a_library_file_is_its_module_however_it_is_spelled(spelling: str) -> None:
    result = _doc(spelling)
    assert result.returncode == 0, result.stderr
    [module] = json.loads(result.stdout)["modules"]
    assert module["name"] == "Data.Option"


def test_an_absolute_library_root_names_the_same_modules() -> None:
    environment = dict(os.environ, TURKEY_LIB=str(bootc.REPO_ROOT / "lib"))
    result = subprocess.run(toolchain.command(bootc.binary(), "doc", "lib/Data/Option.gob"),
                            cwd=bootc.REPO_ROOT, env=environment, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["modules"][0]["name"] == "Data.Option"


@pytest.mark.parametrize("path, message", [
    ("tests/doc/Missing.gob", "Missing.gob"),
    ("lib/Turkey/Heap.gob", "'lib/Turkey/Heap.gob' is part of the library's implementation"),
    ("tests/doc/docs.json", "'tests/doc/docs.json' is not a module"),
], ids=["missing", "implementation", "not a module"])
def test_an_argument_that_names_no_module_is_refused_by_name(path: str, message: str) -> None:
    result = _doc(path)
    assert result.returncode == 2
    assert message in result.stderr
    assert "doc-entry" not in result.stderr


def test_a_file_whose_name_is_no_module_name_is_refused(tmp_path) -> None:
    module = tmp_path / "my module.gob"
    module.write_text("export (x)\nlet x = 1\n")
    result = _doc(str(module))
    assert result.returncode == 2
    assert "'my module' is not a module name" in result.stderr


def _doc_with_lib(lib, *files: str, cwd=bootc.REPO_ROOT) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ, TURKEY_LIB=str(lib))
    return subprocess.run(toolchain.command(bootc.binary(), "doc", *files), cwd=cwd,
                          env=environment, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("through", ["file", "library", "both"])
def test_symbolic_links_name_the_same_library_module(tmp_path, through: str) -> None:
    link = tmp_path / "link"
    link.symlink_to(bootc.REPO_ROOT / "lib")
    real = bootc.REPO_ROOT / "lib"
    lib = link if through in ("library", "both") else real
    base = link if through in ("file", "both") else real
    result = _doc_with_lib(lib, str(base / "Data" / "Option.gob"))
    assert result.returncode == 0, result.stderr
    [module] = json.loads(result.stdout)["modules"]
    assert module["name"] == "Data.Option"


def test_a_prelude_beside_a_users_module_makes_it_no_library_module(tmp_path) -> None:
    (tmp_path / "Prelude.gob").write_text("export (..)\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "Foo.gob").write_text("export (x)\nlet x = 1\n")
    result = _doc(str(tmp_path / "sub" / "Foo.gob"))
    assert result.returncode == 0, result.stderr
    [module] = json.loads(result.stdout)["modules"]
    assert module["name"] == "Foo"


def test_a_compiler_module_is_not_refused_as_a_library_module() -> None:
    # `src/Turkey/` holds a `Prelude.gob` of the compiler's own. Its directory
    # is searched before the library, as `check` searches it, so its
    # `Target.gob` stands in for the library's and the check fails; what this
    # asserts is that the file is not refused before it is checked.
    result = _doc("src/Turkey/Target.gob")
    assert "is in the library" not in result.stderr
    assert "src/Turkey/Target.gob" in result.stderr or result.returncode == 0


def test_the_working_directory_cannot_stand_in_for_a_library_module(tmp_path) -> None:
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "Option.gob").write_text("export (impostor)\nlet impostor = 1\n")
    lib = bootc.REPO_ROOT / "lib"
    environment = dict(os.environ, TURKEY_LIB=str(lib))
    result = subprocess.run(toolchain.command(bootc.binary(), "doc",
                                              str(lib / "Data" / "Option.gob")),
                            cwd=tmp_path, env=environment, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    [module] = json.loads(result.stdout)["modules"]
    assert module["file"] == str(lib / "Data" / "Option.gob")
