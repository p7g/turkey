"""`boot repl`, driven through a pipe.

Each test writes a session to the REPL's standard input and compares what it
printed, standard output then standard error, with what the session should
print. With a pipe for its input the REPL prints no prompts.
"""

from __future__ import annotations

import os
import subprocess

from tests import bootc

REPO_ROOT = bootc.REPO_ROOT


def _session(text: str) -> tuple[str, str, int]:
    ran = subprocess.run(
        [str(bootc.binary()), "repl"], input=text.encode(), capture_output=True,
        timeout=300, env=dict(os.environ, TURKEY_LIB=str(REPO_ROOT / "lib")))
    return (ran.stdout.decode(errors="replace"), ran.stderr.decode(errors="replace"),
            ran.returncode)


def test_an_expression_is_shown():
    out, err, status = _session("1 + 2\n\"text\"\n[1, 2, 3]\n")
    assert (out, err, status) == ("3\ntext\n[1, 2, 3]\n", "", 0)


def test_an_expression_without_show_prints_its_type():
    out, err, status = _session("fun(x : Int) = x + 1\n")
    assert (out, err, status) == ("it : fun(Int) -> Int\n", "", 0)


def test_a_unit_expression_prints_only_what_it_prints():
    out, err, status = _session("print(\"hello\")\n")
    assert (out, err, status) == ("hello\n", "", 0)


def test_definitions_are_seen_by_later_inputs():
    out, err, status = _session(
        "fun double(x : Int) -> Int = x * 2\n"
        "let ten = double(5)\n"
        "type Shape = Circle(Int) | Square(Int)\n"
        "fun area(s : Shape) -> Int = match s {\n"
        "    Circle(r) -> 3 * r * r\n"
        "    Square(w) -> w * w\n"
        "}\n"
        "area(Square(ten))\n")
    assert (out, err, status) == ("100\n", "", 0)


def test_a_redefinition_shadows_and_earlier_uses_keep_the_old_one():
    out, err, status = _session(
        "let x = 1\n"
        "fun f() -> Int = x\n"
        "let x = \"two\"\n"
        "x\n"
        "f()\n"
        "fun f() -> String = x + \"!\"\n"
        "f()\n")
    assert (out, err, status) == ("two\n1\ntwo!\n", "", 0)


def test_a_redefined_type_and_its_constructor():
    out, err, status = _session(
        "type Box = Box(Int)\n"
        "fun open(b : Box) -> Int = match b { Box(n) -> n }\n"
        "type Box = Box(String)\n"
        "match Box(\"new\") { Box(s) -> s }\n"
        "open(Box(1))\n"
        "open\n")
    assert status == 0, err
    # Only the new `Box` is in scope, and the old one is named for which
    # declaration of `Box` it is.
    assert out == "new\nit : fun(Box@1) -> Int\n"
    assert err == ("<input>:1:1: type error: expected 'Box@1', found 'Box' "
                   "in a function call\n")


def test_an_earlier_value_is_not_computed_again():
    out, err, status = _session(
        "let once = { print(\"computed\"); 7 }\n"
        "once + 1\n"
        "once + 2\n")
    assert (out, err, status) == ("computed\n8\n9\n", "", 0)


def test_a_destructured_value_is_not_computed_again():
    out, err, status = _session(
        "let (a, b) = { print(\"computed\"); (1, 2) }\n"
        "a + b\n"
        "b\n")
    assert (out, err, status) == ("computed\n3\n2\n", "", 0)


def test_a_type_error_is_reported_and_the_session_goes_on():
    out, err, status = _session(
        "let n = 1\n"
        "n + \"one\"\n"
        "n + 1\n")
    assert (out, status) == ("2\n", 0)
    assert err.startswith("<input>:1:"), err
    assert "type error" in err
    assert "ReplInput" not in err


def test_imports_hold_for_later_inputs():
    out, err, status = _session(
        "import Data.Set as Set\n"
        "let s = Set.new()\n"
        "Set.add(s, 3)\n"
        "Set.contains(s, 3)\n")
    assert (out, err, status) == ("True\n", "", 0)


def test_an_input_reads_the_same_standard_input():
    out, err, status = _session(
        "import System.IO as IO\n"
        "match IO.readLine(IO.stdin()) { Right(Some(line)) -> line + \"?\"\n _ -> \"none\" }\n"
        "a line for the program\n"
        "1 + 1\n")
    assert (out, err, status) == ("a line for the program?\n2\n", "", 0)
