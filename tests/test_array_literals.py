"""Array literals: typed by use, among the containers the program declares.

A literal's type is one of a closed set of containers applied to its element,
as a numeral's is one of a closed set of numeric types: decided by how the
literal is used, and `Array` when nothing decides. The set holds `Array`, and
`Vec` when the program declares `Data.Vec#Vec`. A set of one is plain `Array`.

The shipped library declares no `Data.Vec`, so the tests of a two-member set
give the compiler a library that does: `lib/` linked entry by entry into a
directory of its own, beside a `Data/Vec.gob` holding a growable array's
declarations and one function to decide a literal with. Only `boot check` and
its dumps run against it; nothing is linked or run.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests import bootc, lang, toolchain
from tests.lang import CompileError

LIB = lang.LIB

VEC = """\
module Data.Vec (Vec, push)

type VecStorage a = VecStorage {
    var storage : Prim.Array a,
    var length  : Int,
}

type Vec a = Vec(VecStorage a)

fun push(xs : Vec a, x : a) -> Unit = {}
"""


@pytest.fixture(scope="module")
def vec_lib(tmp_path_factory) -> Path:
    """`lib/` with a `Data.Vec` added."""
    lib = tmp_path_factory.mktemp("lib")
    for entry in LIB.iterdir():
        if entry.name != "Data":
            (lib / entry.name).symlink_to(entry)
    (lib / "Data").mkdir()
    for entry in (LIB / "Data").iterdir():
        (lib / "Data" / entry.name).symlink_to(entry)
    (lib / "Data" / "Vec.gob").write_text(VEC, encoding="utf-8")
    return lib


def _boot(lib: Path, directory: Path, src: str,
          stage: str) -> subprocess.CompletedProcess[str]:
    (directory / "Main.gob").write_text("import Data.Vec as Vec\n" + src,
                                        encoding="utf-8")
    return subprocess.run(
        toolchain.command(bootc.binary(), *bootc.argv(stage), "Main.gob"),
        cwd=directory, env=dict(os.environ, TURKEY_LIB=str(lib)),
        capture_output=True, text=True, timeout=600)


@pytest.fixture
def with_vec(vec_lib, tmp_path):
    """`types(src)` and `fails(src)` for a program that imports `Data.Vec`
    as `Vec`, checked against the library that has it."""

    class Checker:
        @staticmethod
        def types(src: str) -> dict[str, str]:
            result = _boot(vec_lib, tmp_path, src, "types")
            if result.returncode != 0:
                raise CompileError(result.stderr, result.returncode)
            return dict(line.split(" : ", 1)
                        for line in result.stdout.splitlines())

        @staticmethod
        def fails(src: str) -> str:
            result = _boot(vec_lib, tmp_path, src, "check")
            assert result.returncode != 0, "the program was accepted"
            return CompileError(result.stderr, result.returncode).message

        @staticmethod
        def core(src: str) -> str:
            result = _boot(vec_lib, tmp_path, src, "core")
            assert result.returncode == 0, result.stderr
            return result.stdout

    return Checker


# ----------------------------------------------------- a set of one: `Array`


def test_without_vec_a_literal_is_an_array() -> None:
    assert lang.types('let names = ["a", "b"]\nfun empty() = []\n') == {
        "names": "Array String",
        "empty": "fun() -> Array a",
    }


def test_without_vec_a_mismatch_is_the_equation_with_array() -> None:
    """No set to name, and no container the program does not have."""
    with pytest.raises(CompileError) as e:
        lang.check('fun f(n : Int) -> Int = n\nlet y = f(["a"])\n')
    assert e.value.message == \
        "expected Int, found Array String in a function call"


# ------------------------------------------------------ deciding and defaulting


def test_a_literal_nothing_decides_is_an_array(with_vec) -> None:
    assert with_vec.types('let names = ["a", "b"]\n')["names"] == "Array String"


def test_a_use_decides_the_container(with_vec) -> None:
    src = ("fun built() {\n"
           "    let out = []\n"
           "    Vec.push(out, \"a\")\n"
           "    out\n"
           "}\n")
    assert with_vec.types(src)["built"] == "fun() -> Vec String"


def test_an_annotation_decides_the_container(with_vec) -> None:
    src = ("let v : Vec.Vec Int = [1]\n"
           "let a : Array Int = [2]\n")
    assert with_vec.types(src) == {"v": "Vec Int", "a": "Array Int"}


def test_two_literals_joined_are_one_container(with_vec) -> None:
    src = ("fun f(b : Bool) {\n"
           "    let v = if b { [] } else { [\"a\"] }\n"
           "    Vec.push(v, \"b\")\n"
           "    v\n"
           "}\n")
    assert with_vec.types(src)["f"] == "fun(Bool) -> Vec String"


def test_the_literal_is_built_as_the_container_chosen(with_vec) -> None:
    core = with_vec.core("fun main() {\n"
                         "    let v : Vec.Vec Int = [1]\n"
                         "    let a : Array Int = [2]\n"
                         "}\n")
    assert "Data.Vec#VecStorage" in core
    assert "Data.Array#ArrayStorage" in core


# ------------------------------------------------------ never quantified


def test_a_literal_in_a_generic_function_is_defaulted_not_quantified(
        with_vec) -> None:
    """Each literal is built as one container, so a binding whose type
    mentions the container takes `Array` rather than being generic in it."""
    assert with_vec.types("fun empty() = []\n")["empty"] == "fun() -> Array a"


def test_a_use_wanting_another_container_is_a_mismatch_with_array(
        with_vec) -> None:
    message = with_vec.fails("fun empty() = []\n"
                             "fun takes(v : Vec.Vec Int) -> Unit = {}\n"
                             "let u = takes(empty())\n")
    assert message == "expected Vec, found Array in a function call"


def test_an_annotation_cannot_make_the_container_generic(with_vec) -> None:
    message = with_vec.fails("fun empty() -> c a = []\n")
    assert message == ("an array literal cannot have type 'c a'; it must be "
                       "one of Array, Vec")


# ------------------------------------------------------ what a mismatch says


@pytest.mark.parametrize("annotation", ["Int", "Option String",
                                        "Map String Int"])
def test_a_literal_where_no_container_fits(with_vec, annotation) -> None:
    message = with_vec.fails(f'let x : {annotation} = ["a"]\n')
    assert message == (f"an array literal cannot have type '{annotation}'; "
                       "it must be one of Array, Vec")


def test_a_wrong_element_is_reported_against_the_element(with_vec) -> None:
    assert with_vec.fails('let v : Vec.Vec Int = ["a"]\n') == \
        "expected Int, found String in an array literal"


@pytest.mark.parametrize("src", ["let x : String = 1\n",
                                 "let x : Option Int = 1\n"])
def test_a_numeric_literal_reads_as_it_does_without_vec(with_vec, src) -> None:
    with pytest.raises(CompileError) as e:
        lang.check(src)
    assert with_vec.fails(src) == e.value.message
    assert re.fullmatch(r"a numeric literal cannot have type '[^']+'; it must "
                        r"be one of Int, Float", e.value.message)
