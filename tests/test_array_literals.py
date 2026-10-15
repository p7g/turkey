"""Array literals: typed by use, among the containers the program declares.

A literal's type is one of a closed set of containers applied to its element,
as a numeral's is one of a closed set of numeric types: decided by how the
literal is used, and `Array` when nothing decides. The set holds `Array`, and
`Vec` when the program declares `Data.Vec#Vec`, which the Prelude does.
"""

from __future__ import annotations

import re

import pytest

from tests import lang


# ------------------------------------------------------ deciding and defaulting


def test_a_literal_nothing_decides_is_an_array() -> None:
    assert lang.types('let names = ["a", "b"]\n')["names"] == "Array String"


def test_a_use_decides_the_container() -> None:
    src = ("fun built() {\n"
           "    let out = []\n"
           "    Vec.push(out, \"a\")\n"
           "    out\n"
           "}\n")
    assert lang.types(src)["built"] == "fun() -> Vec String"


def test_an_annotation_decides_the_container() -> None:
    src = ("let v : Vec Int = [1]\n"
           "let a : Array Int = [2]\n")
    assert lang.types(src) == {"v": "Vec Int", "a": "Array Int"}


def test_two_literals_joined_are_one_container() -> None:
    src = ("fun f(b : Bool) {\n"
           "    let v = if b { [] } else { [\"a\"] }\n"
           "    Vec.push(v, \"b\")\n"
           "    v\n"
           "}\n")
    assert lang.types(src)["f"] == "fun(Bool) -> Vec String"


def test_the_literal_is_built_as_the_container_chosen() -> None:
    result = lang.dump("core", "fun main() {\n"
                               "    let v : Vec Int = [1]\n"
                               "    let a : Array Int = [2]\n"
                               "}\n")
    assert result.code == 0, result.stderr
    core = result.stdout
    assert "Data.Vec#Vec(Data.Vec#VecStorage([1], 1))" in core
    # An `Array` is the primitive array under its constructor.
    assert "Data.Array#Array([2])" in core


def test_a_top_level_binding_is_decided_by_its_own_definition() -> None:
    """A later function's use does not reach back: the binding's container is
    defaulted once its own definition is solved."""
    message = lang.fails("let pending = []\n"
                         "fun remember(x : String) = Vec.push(pending, x)\n")
    assert message == "expected Vec, found Array in a function call"


def test_a_top_level_binding_is_decided_the_same_for_an_importer() -> None:
    message = lang.fails("import Queue (pending)\n"
                         "fun main() = Vec.push(pending, 1)\n",
                         {"Queue.gob": "module Queue (pending)\n"
                                       "let pending = []\n"})
    assert message == "expected Vec, found Array in a function call"


def test_an_annotated_top_level_binding_is_a_vec() -> None:
    src = ("let pending : Vec String = []\n"
           "fun remember(x : String) = Vec.push(pending, x)\n")
    assert lang.types(src)["pending"] == "Vec String"


# ------------------------------------------------------ running them


def test_a_literal_pushed_to_is_a_vec_and_grows() -> None:
    assert lang.output("fun main() {\n"
                       "    let out = []\n"
                       "    for x in [3, 1, 2] { Vec.push(out, x * 10) }\n"
                       "    Vec.push(out, 0)\n"
                       "    print(len(out))\n"
                       "    print(out)\n"
                       "}\n") == "4\n[30, 10, 20, 0]\n"


def test_a_vec_literal_runs() -> None:
    assert lang.output("fun main() {\n"
                       "    let v : Vec String = [\"a\", \"b\"]\n"
                       "    v[0] = \"z\"\n"
                       "    Vec.push(v, \"c\")\n"
                       "    print(len(v))\n"
                       "    for s in v { print(s) }\n"
                       "    print(Vec.pop(v))\n"
                       "}\n") == "3\nz\nb\nc\nSome(c)\n"


def test_the_generic_readers_take_a_vec() -> None:
    """The library functions that only read take any container, so a literal
    that has become a `Vec` needs no conversion to reach them."""
    assert lang.output(
        "import System.Parallel as Parallel\n"
        "fun main() {\n"
        "    let parts = []\n"
        "    Vec.push(parts, \"x\")\n"
        "    Vec.push(parts, \"y\")\n"
        "    print(String.join(parts, \",\"))\n"
        "    let bytes = []\n"
        "    for b in String.toBytes(\"hi\") { Vec.push(bytes, b) }\n"
        "    print(String.fromBytes(bytes))\n"
        "    let xs = []\n"
        "    for i in [1, 2, 3] { Vec.push(xs, i) }\n"
        "    print(Parallel.map(xs, fun(x) = x * x))\n"
        "}\n") == "x,y\nSome(hi)\n[1, 4, 9]\n"


# ------------------------------------------------------ never quantified


def test_a_literal_in_a_generic_function_is_defaulted_not_quantified() -> None:
    """Each literal is built as one container, so a binding whose type
    mentions the container takes `Array` rather than being generic in it."""
    assert lang.types("fun empty() = []\n")["empty"] == "fun() -> Array a"


def test_a_use_wanting_another_container_is_a_mismatch_with_array() -> None:
    message = lang.fails("fun empty() = []\n"
                         "fun takes(v : Vec Int) -> Unit = {}\n"
                         "let u = takes(empty())\n")
    assert message == "expected Vec, found Array in a function call"


def test_an_annotation_cannot_make_the_container_generic() -> None:
    message = lang.fails("fun empty() -> c a = []\n")
    assert message == ("an array literal cannot have type 'c a'; it must be "
                       "one of Array, Vec")


# ------------------------------------------------------ what a mismatch says


@pytest.mark.parametrize("annotation", ["Int", "Option String",
                                        "Map String Int"])
def test_a_literal_where_no_container_fits(annotation) -> None:
    message = lang.fails(f'let x : {annotation} = ["a"]\n')
    assert message == (f"an array literal cannot have type '{annotation}'; "
                       "it must be one of Array, Vec")


def test_a_wrong_element_is_reported_against_the_element() -> None:
    assert lang.fails('let v : Vec Int = ["a"]\n') == \
        "expected Int, found String in an array literal"


@pytest.mark.parametrize("src", ["let x : String = 1\n",
                                 "let x : Option Int = 1\n"])
def test_a_numeric_literal_names_only_the_numbers(src) -> None:
    assert re.fullmatch(r"a numeric literal cannot have type '[^']+'; it must "
                        r"be one of Int, Float", lang.fails(src))
