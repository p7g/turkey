"""`turkey doc` over a fixture, against the JSON recorded for it.

`tests/doc/Fixture.gob` declares one of each kind of entity `turkey doc`
describes, re-exports from `tests/doc/Shapes.gob`, and places `---` doc
comments on each edge of the attachment rule (`src/Turkey/DocComments.gob`):
above a field, a constructor, a method, an associated type and an instance;
the module comment; and doc comments that document nothing, beside ordinary,
rule and block comments that are never docs.

`tests/doc/Hidden.gob` is imported but never documented, so the modules that
import from it show what they re-export of it in full: `Fixture` its type,
class and function, and `tests/doc/Board.gob` the same type and the class's
method alone. `Board` also re-exports `Fixture`'s copy of the function, which
stays a link. `Hidden`'s instances go to the pages of their head types --
`Board`, the first by name of the two that show `Tile`, and `Shapes` -- and the
one on `Int`, which has no page, to its class's, `Fixture`. Its instance of a
class no module exports is left out. `tests/doc/Yard.gob` re-exports `Tile`
from `Fixture`, and links to `Fixture`'s copy rather than `Board`'s.

`turkey doc --html` writes the same description as a website, recorded file by
file in `tests/doc/site/`, with the JSON among its files.

To record new output, run with `TURKEY_BLESS_DOC=1`, and read the diff of
`tests/doc/docs.json` and `tests/doc/site/` before committing it.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import urllib.parse

import pytest

from tests import bootc, toolchain

FIXTURES = bootc.REPO_ROOT / "tests" / "doc"
GOLDEN = FIXTURES / "docs.json"
SITE = FIXTURES / "site"
FILES = ("tests/doc/Board.gob", "tests/doc/Fixture.gob", "tests/doc/Shapes.gob",
         "tests/doc/Yard.gob")


def _doc(*files: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(toolchain.command(bootc.binary(), "doc", *files),
                          cwd=bootc.REPO_ROOT, capture_output=True, text=True,
                          timeout=120)


def test_the_fixture_matches_its_golden() -> None:
    result = _doc(*FILES)
    assert result.returncode == 0, result.stderr
    if os.environ.get("TURKEY_BLESS_DOC"):
        GOLDEN.write_text(result.stdout)
    assert result.stdout == GOLDEN.read_text()


def _site(out) -> dict[str, str]:
    result = _doc("--html", str(out), *FILES)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    return {p.name: p.read_text() for p in sorted(out.iterdir())}


def test_the_fixture_site_matches_its_golden(tmp_path) -> None:
    site = _site(tmp_path / "site")
    assert site.pop("docs.json") == _doc(*FILES).stdout
    if os.environ.get("TURKEY_BLESS_DOC"):
        SITE.mkdir(exist_ok=True)
        for stale in SITE.iterdir():
            stale.unlink()
        for name, text in site.items():
            (SITE / name).write_text(text)
    assert site == {p.name: p.read_text() for p in sorted(SITE.iterdir())}


def test_every_link_in_the_site_reaches_an_element(tmp_path) -> None:
    site = _site(tmp_path / "site")
    ids = {name: [html.unescape(i) for i in re.findall(r'id="([^"]*)"', text)]
           for name, text in site.items() if name.endswith(".html")}
    for name, found in ids.items():
        assert len(found) == len(set(found)), name
    # The search index holds its results' HTML as JavaScript strings.
    results = json.loads(site["search-index.js"].removeprefix("const searchIndex = ")
                         .rstrip().removesuffix(";").replace(",\n]", "\n]"))
    sources = [text for name, text in site.items() if name.endswith(".html")]
    sources += [entry[2] for entry in results]
    links = [html.unescape(h) for text in sources for h in re.findall(r'href="([^"]*)"', text)]
    assert any("%" in link for link in links)
    for link in links:
        page, _, fragment = link.partition("#")
        assert page in site, link
        assert not fragment or urllib.parse.unquote(fragment) in ids[page], link


def test_the_output_does_not_depend_on_the_order_of_the_files() -> None:
    forwards = _doc(*FILES)
    backwards = _doc(*reversed(FILES))
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


def _items(tmp_path, sources: dict[str, str], documented: list[str],
           library: tuple[str, ...] = ()) -> dict[str, dict[str, dict]]:
    """Writes `sources` into `tmp_path` and documents the modules `documented`
    names there, with the library files `library`. Returns each module's items
    by name."""
    for name, text in sources.items():
        (tmp_path / f"{name}.gob").write_text(text)
    result = _doc(*library, *(str(tmp_path / f"{name}.gob") for name in documented))
    assert result.returncode == 0, result.stderr
    return {m["name"]: {item["name"]: item for item in m["items"]}
            for m in json.loads(result.stdout)["modules"]}


def test_an_explicit_import_shadows_the_implicit_prelude(tmp_path) -> None:
    # The Prelude, undocumented here, gives `Option` too.
    items = _items(tmp_path, {"M": "export (Option(..))\nimport Data.Option (Option(..))\n"},
                   ["M"], ("lib/Data/Option.gob",))
    assert items["M"]["Option"]["kind"] == "reexport"
    assert items["M"]["Option"]["home"] == "Data.Option"


def test_a_directive_between_a_doc_comment_and_its_declaration_keeps_it(tmp_path) -> None:
    items = _items(tmp_path, {"M": (
        "--- The module.\n\nexport (f, g)\n\n"
        "--- Returns one.\n--@expect(unreachable-code) a return after a return\n"
        "fun f() -> Int {\n    return 1\n    return 2\n}\n\n"
        "--- Returns two.\n    --@expect(empty-return-value)\n--@expect(unreachable-code)\n"
        "fun g() -> Int {\n    return 2\n    return 3\n}\n")}, ["M"])
    assert items["M"]["f"]["comment"] == "Returns one."
    assert items["M"]["g"]["comment"] == "Returns two."


def test_a_directive_between_two_doc_comments_joins_neither_to_the_other(tmp_path) -> None:
    (tmp_path / "M.gob").write_text(
        "--- The module.\n\nexport (f)\n\n"
        "--- Cut off.\n--@expect(unreachable-code)\n--- Returns one.\n"
        "fun f() -> Int {\n    return 1\n    return 2\n}\n")
    result = _doc(str(tmp_path / "M.gob"))
    assert result.returncode == 0, result.stderr
    module = json.loads(result.stdout)["modules"][0]
    assert module["comment"] == "The module."
    assert {item["name"]: item["comment"] for item in module["items"]} == {"f": "Returns one."}


def test_a_name_an_import_hides_is_not_shown_from_it(tmp_path) -> None:
    items = _items(tmp_path, {
        "H": "export (T(..))\n\ntype T = T(Int)\n",
        "D": "export (T(..))\n\nimport H (T(..))\n",
        "W": "export (T(..))\n\nimport H (T as _, ..)\nimport D (T(..))\n",
    }, ["D", "W"])
    assert items["D"]["T"]["from"] == "H"
    assert items["W"]["T"]["kind"] == "reexport"
    assert items["W"]["T"]["home"] == "D"


def test_a_name_a_qualifier_passes_on_links_to_that_module(tmp_path) -> None:
    # `Data.Array` shows `threeWay` too, and comes first by name.
    items = _items(tmp_path, {"Q": "export (module Vec (threeWay))\n"}, ["Q"],
                   ("lib/Data/Array.gob", "lib/Data/Vec.gob"))
    assert items["Q"]["threeWay"]["kind"] == "reexport"
    assert items["Q"]["threeWay"]["home"] == "Data.Vec"


def _shadowing(tmp_path) -> tuple[str, str]:
    """A program `Prelude` that exports its own `Deque` as a qualifier: two
    modules the library has namesakes of. Returns their paths."""
    (tmp_path / "Deque.gob").write_text("export (T(..), one)\n\ntype T = T\n\nlet one = T\n")
    (tmp_path / "Prelude.gob").write_text(
        "export (one, module Deque)\n\nimport Deque\nimport Deque (one)\n")
    return str(tmp_path / "Prelude.gob"), str(tmp_path / "Deque.gob")


def test_a_program_module_is_named_as_it_is_written(tmp_path) -> None:
    prelude, _ = _shadowing(tmp_path)
    result = _doc(prelude)
    assert result.returncode == 0, result.stderr
    assert "Deque:" not in result.stdout and "Prelude:" not in result.stdout
    [module] = json.loads(result.stdout)["modules"]
    items = {item["name"]: item for item in module["items"]}
    assert module["name"] == "Prelude"
    assert items["Deque"] == {"kind": "qualifier", "name": "Deque", "module": "Deque"}
    assert items["one"]["from"] == "Deque"


def test_a_link_goes_to_the_module_meant_not_its_namesake(tmp_path) -> None:
    """The program's `Deque` and the library's are written alike. A qualifier
    that means the program's links to no page when only the library's is
    documented, and to the program's page when that is."""
    prelude, deque = _shadowing(tmp_path)
    out = tmp_path / "site"
    result = _doc("--html", str(out), "lib/Deque.gob", prelude)
    assert result.returncode == 0, result.stderr
    page = (out / "Prelude.html").read_text().split("<main")[1]
    assert "Deque:" not in page and 'href="Deque.html' not in page
    out = tmp_path / "own"
    result = _doc("--html", str(out), deque, prelude)
    assert result.returncode == 0, result.stderr
    page = (out / "Prelude.html").read_text().split("<main")[1]
    assert '<a href="Deque.html">Deque</a>' in page
    assert "type</span>" in (out / "Deque.html").read_text()


def test_two_modules_written_alike_are_not_documented_together(tmp_path) -> None:
    _, deque = _shadowing(tmp_path)
    result = _doc("lib/Deque.gob", deque)
    assert result.returncode != 0
    assert "and a library module are both written 'Deque'; document them separately" \
        in result.stderr


def test_a_module_that_sees_no_copy_links_to_the_first_by_name(tmp_path) -> None:
    # `A` and `B` have `x` from each other, so following their imports finds
    # no copy, only the cycle. `C` and `D` each show `x`.
    items = _items(tmp_path, {
        "U": "export (x)\n\nlet x = 1\n",
        "A": "export (module Uq (x))\n\nimport U as Uq\nimport B (x)\n",
        "B": "export (x)\n\nimport A (x)\n",
        "C": "export (x)\n\nimport U (x)\n",
        "D": "export (x)\n\nimport U (x)\n",
    }, ["A", "B", "C", "D"])
    assert items["C"]["x"]["from"] == "U"
    assert items["D"]["x"]["from"] == "U"
    assert items["A"]["x"]["home"] == "C"
    assert items["B"]["x"]["home"] == "C"


def test_a_method_without_its_class_links_along_its_own_imports(tmp_path) -> None:
    # `D1` and `D2` each show `C`. `M` has `meth` from `D2`; its unrelated
    # qualifier for `D1` must not count.
    items = _items(tmp_path, {
        "U": "export (C(..))\n\nclass C a {\n    fun meth(a) -> Int\n}\n",
        "D1": "export (C(..))\n\nimport U (C(..))\n",
        "D2": "export (C(..))\n\nimport U (C(..))\n",
        "M": "export (meth)\n\nimport D2 (meth)\nimport D1 as Q\n",
    }, ["D1", "D2", "M"])
    assert items["M"]["meth"]["home"] == "D2"


def test_an_associated_type_links_where_its_class_is_renamed(tmp_path) -> None:
    files = {
        "U": "export (C(..))\n\nclass C a {\n    type Item a\n    fun first(a) -> Item a\n}\n",
        "D": "export (K(..))\n\nimport U (C(..) as K)\n",
        "M": "export (Item)\n\nimport D (K(..))\n",
    }
    items = _items(tmp_path, files, ["D", "M"])
    assert items["M"]["Item"]["home"] == "D"
    out = tmp_path / "site"
    result = _doc("--html", str(out), str(tmp_path / "D.gob"), str(tmp_path / "M.gob"))
    assert result.returncode == 0, result.stderr
    assert 'href="D.html#assoc.K.Item">D.K.Item</a>' in (out / "M.html").read_text()


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
