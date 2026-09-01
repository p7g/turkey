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
module Helper (twice, greet)

fun twice(n : Int) -> Int = n + n

fun greet(who : String) -> String = "hello, " + who

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

    Evaluator(checked.decls, initial_values()).run(checked.mono, checked.main)
    return capsys.readouterr().out.splitlines()


# -- what an import brings ----------------------------------------------------


def test_a_plain_import_brings_a_name_both_ways(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    src = "import Helper\nfun f() -> Int = twice(1) + Helper.twice(2)"
    assert sigs(src, search)["f"] == "fun() -> Int"


def test_a_qualified_import_brings_only_the_qualified_name(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    src = "import Helper as Helper\nfun f() -> Int = Helper.twice(1)"
    assert sigs(src, search)["f"] == "fun() -> Int"
    bad = "import Helper as Helper\nfun f() -> Int = twice(1)"
    assert fails(bad, search) == "'twice' is not defined"


def test_an_alias_renames_the_module(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    src = "import Helper as H\nfun f() -> Int = H.twice(1)"
    assert sigs(src, search)["f"] == "fun() -> Int"
    assert fails("import Helper as H\nfun f() = Helper.twice(1)",
                 search) == "'Helper.twice' is not defined"


def test_an_alias_and_a_selective_list_are_independent(tmp_path):
    """`import M as S (f)` did not even parse before M11a: the
    parser's `elif` chain made `as` and a list mutually exclusive."""
    search = write(tmp_path, Helper=HELPER)
    src = "import Helper as H (twice)\nfun f() -> Int = H.twice(1)"
    assert sigs(src, search)["f"] == "fun() -> Int"
    assert fails("import Helper as H (twice)\nfun f() = H.greet(\"x\")",
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


def test_import_prelude_empty_disables_the_implicit_prelude(tmp_path):
    src = "import Prelude ()\nfun identity(n : Int) -> Int = n"
    assert sigs(src, [tmp_path])["identity"] == "fun(Int) -> Int"
    assert fails(
        "import Prelude ()\nfun main() { print(1) }", [tmp_path]
    ) == "'print' is not defined"


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
    search = write(tmp_path, Broken="module Broken (nope)\nfun yes() = 1")
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
    src = "import Helper\nfun twice(s : String) -> String = s + s"
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
    src = 'fun add(x : String, y : String) -> String = x + y\n' \
          'fun main() { print(add("a", "b")); print(1 + 2) }'
    assert output(src, [tmp_path], capsys) == ["ab", "3"]


# -- the graph ----------------------------------------------------------------


def test_an_import_is_transitive_only_through_its_own_names(tmp_path):
    """Importing `Middle` does not re-export what `Middle` imported."""
    search = write(
        tmp_path,
        Helper=HELPER,
        Middle="module Middle (thrice)\nimport Helper (twice)\n"
               "fun thrice(n : Int) -> Int = twice(n) + n",
    )
    assert sigs("import Middle\nfun f() -> Int = thrice(1)", search)["f"] == \
        "fun() -> Int"
    assert fails("import Middle\nfun f() = twice(1)", search) == \
        "'twice' is not defined"


def test_a_cycle_is_rejected(tmp_path):
    search = write(
        tmp_path,
        A="module A (a)\nimport B (b)\nfun a() -> Int = b()",
        B="module B (b)\nimport A (a)\nfun b() -> Int = a()",
    )
    assert "imports form a cycle" in fails("import A", search)


def test_a_missing_module_is_reported(tmp_path):
    assert "cannot find module 'Nowhere'" in fails("import Nowhere", [tmp_path])


def test_a_type_declared_in_another_module_is_usable(tmp_path):
    """Section 7's alias-vs-data pre-pass is per file, so the loader has to
    hand the parser the type names an import already put in scope."""
    search = write(
        tmp_path,
        Shape="module Shape (Circle(..))\ntype Circle = Circle(Int)",
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
    search = write(tmp_path, Wrong="module Wrong (w)\nfun w() -> Int = \"s\"")
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
        Middle="module Middle (loud)\nimport Helper (greet)\n"
               "fun loud(who : String) -> String = greet(who) + \"!\"",
    )
    src = 'import Middle\nfun main() { print(loud("world")) }'
    assert output(src, search, capsys) == ["hello, world!"]


def test_two_modules_may_each_define_the_same_name(tmp_path, capsys):
    search = write(
        tmp_path,
        One="module One (name)\nfun name() -> String = \"one\"",
        Two="module Two (name)\nfun name() -> String = \"two\"",
    )
    src = ('import One as One\nimport Two as Two\n'
           'fun main() { print(One.name()); print(Two.name()) }')
    assert output(src, search, capsys) == ["one", "two"]


def test_run_from_a_file_searches_beside_it(tmp_path, capsys):
    write(tmp_path, Helper=HELPER)
    entry = tmp_path / "Main.tl"
    entry.write_text('import Helper\nfun main() { print(greet("you")) }',
                     encoding="utf-8")
    run(entry.read_text(), str(entry))
    assert capsys.readouterr().out.splitlines() == ["hello, you"]


# -- the library is written in the language (M11b) ----------------------------


def test_the_bare_names_the_library_uses_are_free(tmp_path, capsys):
    """`plan.txt` item 3, the other half: the built-in `Array.push` used to
    claim `push` too. A *module* re-export claims no bare name."""
    src = """
fun push(n : Int) -> Int = n + 1
fun new(n : Int) -> Int = n + 2
fun pop(n : Int) -> Int = n + 3
fun toString(n : Int) -> Int = n + 4

fun main() {
    print(push(0) + new(0) + pop(0) + toString(0))
    let xs = Array.new(2)
    Array.push(xs, 9)
    print(Array.pop(xs))
}
"""
    assert output(src, [tmp_path], capsys) == ["10", "Some(9)"]


def test_the_library_is_reachable_without_an_import(tmp_path):
    src = ('fun f(s : String) -> Int = String.length(s)\n'
           'fun g(n : Int) -> String = Int.toString(n)\n'
           'fun h(c : Char) -> Int = Char.toInt(c)\n'
           'fun i(b : Bool) -> String = Bool.toString(b)\n'
           'fun j(x : Float) -> String = Float.toString(x)')
    got = sigs(src, [tmp_path])
    assert got["f"] == "fun(String) -> Int"
    assert got["i"] == "fun(Bool) -> String"


def test_the_long_spelling_is_available_by_importing_the_module(tmp_path):
    """The Prelude re-exports `Data.Array` under the short alias. A program
    that wants section 8.3's spelling asks for the module itself."""
    src = ("import Data.Array\n"
           "fun f(xs : Array Int) -> Unit = Data.Array.push(xs, 1)")
    assert sigs(src, [tmp_path])["f"] == "fun(Array Int) -> Unit"
    assert fails("fun f(xs : Array Int) = Data.Array.push(xs, 1)", [tmp_path]) == \
        "'Data.Array.push' is not defined"


def test_the_library_functions_are_ordinary_turkey(tmp_path, capsys):
    src = """
fun main() {
    print(Bool.not(True))
    print(Option.isSome(Some(1)))
    print(Option.unwrapOr(None, 5))
}
"""
    assert output(src, [tmp_path], capsys) == ["False", "True", "5"]


def test_a_re_export_needs_the_module_to_be_imported(tmp_path):
    search = write(tmp_path, Nope="module Nope (module Missing)\nfun f() = 1")
    assert fails("import Nope", search) == \
        "module 'Nope' re-exports 'Missing', which is not imported here"


def test_module_is_an_export_form_not_an_import_one(tmp_path):
    search = write(tmp_path, Helper=HELPER)
    assert "'module' may appear in an export list" in \
        fails("import Helper (module Helper)", search)


# -- a type and an instance know which module made them (M11c) ----------------

SHAPE = """
module Shape (Node(..), leaf)

type Node = Leaf | Fork(Node, Node)

fun leaf() -> Node = Leaf
"""


def test_two_modules_may_each_declare_the_same_type(tmp_path):
    """The stated outcome of delta 43: two libraries that each have a `Node`
    can be used together, because the two are different type constructors."""
    search = write(tmp_path, Shape=SHAPE,
                   Graph="module Graph (Node(..))\ntype Node = Node(Int)")
    src = ("import Shape as S\nimport Graph as G\n"
           "fun a() -> S.Node = S.Leaf\nfun b() -> G.Node = G.Node(1)")
    got = sigs(src, search)
    # Both print qualified, because printing `Node` twice would say less.
    assert got["a"] == "fun() -> Shape.Node"
    assert got["b"] == "fun() -> Graph.Node"


def test_a_type_with_one_declaration_prints_short(tmp_path):
    search = write(tmp_path, Shape=SHAPE)
    assert sigs("import Shape\nfun f() -> Node = leaf()", search)["f"] == \
        "fun() -> Node"


def test_a_constructor_is_qualified_under_a_qualified_import(tmp_path):
    search = write(tmp_path, Shape=SHAPE)
    src = "import Shape as S\nfun f() -> S.Node = S.Leaf"
    assert sigs(src, search)["f"] == "fun() -> Node"
    assert fails("import Shape as S\nfun f() = Leaf", search) == \
        "unknown constructor 'Leaf'"


def test_an_export_list_may_withhold_the_constructors(tmp_path):
    """`T` exports the type; `T(..)` exports its constructors too. That is the
    difference between an abstract type and a transparent one."""
    search = write(
        tmp_path,
        Opaque="module Opaque (Token, make)\n"
               "type Token = Token(Int)\n"
               "fun make(n : Int) -> Token = Token(n)",
    )
    assert sigs("import Opaque\nfun f() -> Token = make(1)", search)["f"] == \
        "fun() -> Token"
    assert "unknown constructor 'Token'" in \
        fails("import Opaque\nfun f() = Token(1)", search)


def test_a_selective_import_of_a_type_brings_its_constructors_with_dots(tmp_path):
    search = write(tmp_path, Shape=SHAPE)
    assert sigs("import Shape (Node(..), leaf)\nfun f() -> Node = Fork(Leaf, Leaf)",
                search)["f"] == "fun() -> Node"
    assert "unknown constructor 'Fork'" in \
        fails("import Shape (Node, leaf)\nfun f() = Fork(Leaf, Leaf)", search)


# -- coherence ----------------------------------------------------------------


def test_an_instance_may_live_with_its_type(tmp_path):
    search = write(tmp_path, Shape=SHAPE)
    src = ("import Shape\n"
           "type Tree = Tree(Int)\n"
           "instance Show Tree { fun show(t) = \"tree\" }\n"
           "fun f(t : Tree) -> String = show(t)")
    assert sigs(src, search)["f"] == "fun(Tree) -> String"


def test_an_instance_may_live_with_its_class(tmp_path):
    src = ("class Sized a { fun size(a) -> Int }\n"
           "instance Sized Int { fun size(n) = n }\n"
           "fun f(n : Int) -> Int = size(n)")
    assert sigs(src, [tmp_path])["f"] == "fun(Int) -> Int"


def test_an_imported_qualified_class_can_have_a_local_type_instance(tmp_path):
    search = write(
        tmp_path,
        Rules=("module Rules (Render(..))\n"
               "class Render a { fun render(a) -> String }"),
    )
    src = ("import Rules as R\n"
           "type Thing = Thing\n"
           "instance R.Render Thing { fun render(x) = \"thing\" }\n"
           "fun f(x : Thing) -> String = R.render(x)")
    checked = check(src, None, search)
    assert "Rules#Render" in checked.classes.classes
    assert sigs(src, search)["f"] == "fun(Thing) -> String"


def test_an_orphan_instance_is_rejected(tmp_path):
    """Neither `Show` nor `Node` is this module's, so nothing stops another
    module from declaring the same instance -- and one of the two would be an
    error in a file neither author wrote."""
    search = write(tmp_path, Shape=SHAPE)
    message = fails(
        'import Shape\ninstance Show Node { fun show(n) = "node" }', search)
    assert message.startswith("orphan instance: 'Show Node' is declared in 'Main'")
    assert "'Show' belongs to 'Std.Classes'" in message
    assert "'Node' to 'Shape'" in message


def test_an_instance_for_a_built_in_type_over_a_library_class_is_an_orphan(tmp_path):
    """`Neg` is the Prelude's and `String` is the language's, so this module
    owns neither end of it."""
    message = fails("instance Neg String { fun neg(s) = s }", [tmp_path])
    assert message.startswith("orphan instance: 'Neg String'")
    assert "the language itself" in message


def test_two_instances_for_one_head_still_overlap(tmp_path):
    search = write(
        tmp_path,
        Shape=SHAPE,
        Extra="module Extra ()\nimport Shape (Node(..))\n"
              'instance Show Node { fun show(n) = "a" }',
    )
    assert "orphan instance" in fails("import Extra", search)
