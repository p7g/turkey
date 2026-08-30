"""M11a: a program is a graph of modules (design.md section 9).

The golden programs under `tests/programs/modules/` cover the happy path
end to end. What is here is the scoping rules themselves -- what an import
brings, what an export list withholds, and which name wins when two could
apply -- written against sources small enough that the answer is the whole
test.

Each test writes its dependencies into a temporary directory and hands the
driver that directory as the search path, which is exactly what the CLI does
with the entry file's own directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from turkey.driver import check, run
from turkey.errors import TurkeyError
from turkey.types import show_scheme

HELPER = """
module Helper (twice, greet) where

fun twice(n : Int) -> Int = n + n

fun greet(who : String) -> String = "hello, " ++ who

fun secret() -> Int = 7
"""


def write(tmp_path: Path, **modules: str) -> list[Path]:
    for name, source in modules.items():
        (tmp_path / f"{name}.tl").write_text(source, encoding="utf-8")
    return [tmp_path]


def sigs(src: str, search: list[Path]) -> dict[str, str]:
    checked = check(src, None, search)
    return {name: show_scheme(scheme) for name, scheme in checked.signatures}


def fails(src: str, search: list[Path]) -> str:
    with pytest.raises(TurkeyError) as exc:
        check(src, None, search)
    return exc.value.message


def output(src: str, search: list[Path], capsys) -> list[str]:
    checked = check(src, None, search)
    from turkey.builtins import initial_values
    from turkey.eval import Evaluator

    Evaluator(checked.decls, initial_values()).run(checked.ordered, checked.main)
    return capsys.readouterr().out.splitlines()


# -- what an import brings ----------------------------------------------------


def test_a_plain_import_brings_a_name_both_ways(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    src = "import Helper\nfun f() -> Int = twice(1) + Helper.twice(2)"
    assert sigs(src, search)["f"] == "fun() -> Int"


def test_a_qualified_import_brings_only_the_qualified_name(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    src = "import qualified Helper\nfun f() -> Int = Helper.twice(1)"
    assert sigs(src, search)["f"] == "fun() -> Int"
    bad = "import qualified Helper\nfun f() -> Int = twice(1)"
    assert fails(bad, search) == "'twice' is not defined"


def test_an_alias_renames_the_module(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    src = "import qualified Helper as H\nfun f() -> Int = H.twice(1)"
    assert sigs(src, search)["f"] == "fun() -> Int"
    assert fails("import qualified Helper as H\nfun f() = Helper.twice(1)",
                 search) == "'Helper.twice' is not defined"


def test_an_alias_and_a_selective_list_are_independent(tmp_path):
    """`import qualified M as S (f)` did not even parse before M11a: the
    parser's `elif` chain made `as` and a list mutually exclusive."""
    search = write(tmp_path, Helper=HELPER)
    src = "import qualified Helper as H (twice)\nfun f() -> Int = H.twice(1)"
    assert sigs(src, search)["f"] == "fun() -> Int"
    assert fails("import qualified Helper as H (twice)\nfun f() = H.greet(\"x\")",
                 search) == "'H.greet' is not defined"


def test_a_selective_list_withholds_the_rest(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    assert fails("import Helper (twice)\nfun f() = greet(\"x\")",
                 search) == "'greet' is not defined"


def test_hiding_withholds_only_what_it_names(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    src = "import Helper hiding (greet)\nfun f() -> Int = twice(1)"
    assert sigs(src, search)["f"] == "fun() -> Int"
    assert fails("import Helper hiding (greet)\nfun f() = greet(\"x\")",
                 search) == "'greet' is not defined"


# -- what an export list withholds --------------------------------------------


def test_a_name_the_export_list_omits_is_not_importable(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    assert fails("import Helper\nfun f() = secret()", search) == \
        "'secret' is not defined"


def test_importing_a_name_that_is_not_exported_says_so(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    assert fails("import Helper (secret)", search) == \
        "module 'Helper' does not export 'secret'"


def test_a_module_may_not_export_what_it_does_not_have(tmp_path):
    search = write(tmp_path, Broken="module Broken (nope) where\nfun yes() = 1")
    assert fails("import Broken", search) == \
        "module 'Broken' exports 'nope', which is not defined or imported here"


def test_a_module_with_no_export_list_exports_everything(tmp_path):
    search = write(tmp_path, Open="fun one() -> Int = 1\nfun two() -> Int = 2")
    src = "import Open\nfun f() -> Int = one() + two()"
    assert sigs(src, search)["f"] == "fun() -> Int"


# -- which name wins ----------------------------------------------------------


def test_a_local_definition_shadows_an_import(tmp_path):
    """design.md section 9.3 rule 2. The two are different bindings, not a
    conflict: `twice` here is `Main#twice`."""
    search = write(tmp_path, Helper=HELPER)
    src = "import Helper\nfun twice(s : String) -> String = s ++ s"
    assert sigs(src, search)["twice"] == "fun(String) -> String"


def test_an_import_shadows_the_prelude(tmp_path):
    search = write(tmp_path, Shadow="fun print(x : Int) -> Int = x + 1")
    src = "import Shadow\nfun f() -> Int = print(1)"
    assert sigs(src, search)["f"] == "fun() -> Int"


def test_a_module_may_define_a_name_the_prelude_uses(tmp_path):
    """`plan.txt` item 3: seventeen names were unavailable to every program."""
    src = "fun show(x : Int) -> Int = x\nfun iter(x : Int) -> Int = x"
    assert sigs(src, [tmp_path])["show"] == "fun(Int) -> Int"


def test_an_operator_still_means_its_class_method(tmp_path, capsys):
    """The desugared node is marked, not looked up -- see turkey/resolve.py."""
    src = 'fun add(x : String, y : String) -> String = x ++ y\n' \
          'fun main() { print(add("a", "b")); print(1 + 2) }'
    assert output(src, [tmp_path], capsys) == ["ab", "3"]


# -- the graph ----------------------------------------------------------------


def test_an_import_is_transitive_only_through_its_own_names(tmp_path):
    """Importing `Middle` does not re-export what `Middle` imported."""
    search = write(
        tmp_path,
        Helper=HELPER,
        Middle="module Middle (thrice) where\nimport Helper (twice)\n"
               "fun thrice(n : Int) -> Int = twice(n) + n",
    )
    assert sigs("import Middle\nfun f() -> Int = thrice(1)", search)["f"] == \
        "fun() -> Int"
    assert fails("import Middle\nfun f() = twice(1)", search) == \
        "'twice' is not defined"


def test_a_cycle_is_rejected(tmp_path):
    search = write(
        tmp_path,
        A="module A (a) where\nimport B (b)\nfun a() -> Int = b()",
        B="module B (b) where\nimport A (a)\nfun b() -> Int = a()",
    )
    assert "imports form a cycle" in fails("import A", search)


def test_a_missing_module_is_reported(tmp_path):
    assert "cannot find module 'Nowhere'" in fails("import Nowhere", [tmp_path])


def test_a_type_declared_in_another_module_is_usable(tmp_path):
    """Section 7's alias-vs-data pre-pass is per file, so the loader has to
    hand the parser the type names an import already put in scope."""
    search = write(
        tmp_path,
        Shape="module Shape (Circle(..)) where\ntype Circle = Circle(Int)",
    )
    src = "import Shape\ntype Round = Circle\nfun f(c : Round) -> Round = c"
    assert sigs(src, search)["f"] == "fun(Circle) -> Circle"


def test_the_primitives_stay_out_of_a_user_module(tmp_path):
    """`Prim.*` is in the shared environment so the Prelude can be checked;
    what keeps it out of the language is the module's scope."""
    assert fails('fun f() { Prim.print("x") }', [tmp_path]) == \
        "'Prim.print' is not defined"


def test_the_prelude_is_imported_without_being_asked_for(tmp_path):
    assert sigs("fun f(x : Int) -> String = show(x)", [tmp_path])["f"] == \
        "fun(Int) -> String"


# -- diagnostics --------------------------------------------------------------


def test_a_diagnostic_in_an_imported_module_names_that_module(tmp_path):
    search = write(tmp_path, Wrong="module Wrong (w) where\nfun w() -> Int = \"s\"")
    with pytest.raises(TurkeyError) as exc:
        check("import Wrong", None, search)
    assert exc.value.span is not None
    assert exc.value.span.file == str(tmp_path / "Wrong.tl")


def test_a_diagnostic_never_shows_an_internal_name(tmp_path):
    """A top-level binding is `Main#f` after resolution; no message says so."""
    assert "#" not in fails("let a = b\nlet b = a", [tmp_path])
    assert fails("let a = b\nlet b = a", [tmp_path]).startswith(
        "cyclic definition: a, b")


def test_run_evaluates_every_module_in_dependency_order(tmp_path, capsys):
    search = write(
        tmp_path,
        Helper=HELPER,
        Middle="module Middle (loud) where\nimport Helper (greet)\n"
               "fun loud(who : String) -> String = greet(who) ++ \"!\"",
    )
    src = 'import Middle\nfun main() { print(loud("world")) }'
    assert output(src, search, capsys) == ["hello, world!"]


def test_two_modules_may_each_define_the_same_name(tmp_path, capsys):
    search = write(
        tmp_path,
        One="module One (name) where\nfun name() -> String = \"one\"",
        Two="module Two (name) where\nfun name() -> String = \"two\"",
    )
    src = ('import qualified One\nimport qualified Two\n'
           'fun main() { print(One.name()); print(Two.name()) }')
    assert output(src, search, capsys) == ["one", "two"]


def test_run_from_a_file_searches_beside_it(tmp_path, capsys):
    write(tmp_path, Helper=HELPER)
    entry = tmp_path / "Main.tl"
    entry.write_text('import Helper\nfun main() { print(greet("you")) }',
                     encoding="utf-8")
    run(entry.read_text(), str(entry))
    assert capsys.readouterr().out.splitlines() == ["hello, you"]
