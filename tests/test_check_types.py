"""`turkey check --types`: the type of each top-level binding, on stdout.

The playground's Types view parses the JSON form, so its members, their order
and the positions are checked here exactly, for a program of two files.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests import bootc, toolchain

MAIN = (
    "import Shapes.Circle as Circle\n"
    "\n"
    "let (width, name) = (3, \"box\")\n"
    "\n"
    "class Sized s {\n"
    "\tfun size(s) -> Int\n"
    "}\n"
    "\n"
    "fun twice(f, x) = f(f(x))\n"
    "\n"
    "fun main() {\n"
    "\tprint(Circle.describe(2.0))\n"
    "\tprint(twice(\\(_ + width), 1))\n"
    "\tprint(name)\n"
    "}\n"
)

CIRCLE = (
    "export (describe)\n"
    "\n"
    "fun describe(r : Float) -> String = \"a circle of radius \" + Float.toString(r)\n"
)


def check(tmp_path: Path, *flags: str, main: str = MAIN) -> subprocess.CompletedProcess[str]:
    (tmp_path / "main.gob").write_text(main)
    (tmp_path / "Shapes").mkdir(exist_ok=True)
    (tmp_path / "Shapes" / "Circle.gob").write_text(CIRCLE)
    # The suite names the library relative to the repository root, and this
    # runs elsewhere.
    environment = dict(os.environ, TURKEY_LIB=str(bootc.REPO_ROOT / "lib"))
    return subprocess.run(
        toolchain.command(bootc.binary(), *flags, "main.gob"),
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120)


def test_json_lists_every_file_of_the_program_and_no_library_module(tmp_path: Path) -> None:
    result = check(tmp_path, "check", "--types=json")
    assert result.returncode == 0, result.stderr
    # A module comes before the modules that import it, and a pattern's
    # variables are in the order of their names.
    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        {"file": "Shapes/Circle.gob", "line": 3, "column": 1, "name": "describe",
         "type": "fun(Float) -> String"},
        {"file": "main.gob", "line": 3, "column": 13, "name": "name", "type": "String"},
        # A binding of a literal is generalized over the types the literal
        # can have.
        {"file": "main.gob", "line": 3, "column": 6, "name": "width",
         "type": "[OneOf a {Int, Float}] a"},
        {"file": "main.gob", "line": 6, "column": 2, "name": "size",
         "type": "[Sized a] fun(a) -> Int"},
        {"file": "main.gob", "line": 9, "column": 1, "name": "twice",
         "type": "fun(fun(a) -> a, a) -> a"},
        {"file": "main.gob", "line": 11, "column": 1, "name": "main", "type": "fun() -> Unit"},
    ]


def test_the_members_are_in_a_fixed_order(tmp_path: Path) -> None:
    result = check(tmp_path, "check", "--types=json")
    assert result.stdout.splitlines()[0] == (
        '{"file":"Shapes/Circle.gob","line":3,"column":1,"name":"describe",'
        '"type":"fun(Float) -> String"}')


def test_text_is_a_line_per_binding(tmp_path: Path) -> None:
    result = check(tmp_path, "check", "--types=text")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "main.gob:11:1: main : fun() -> Unit"


def test_a_program_that_does_not_check_lists_nothing(tmp_path: Path) -> None:
    result = check(tmp_path, "check", "--types=json", main="fun main() {\n\tprint(1 + \"one\")\n}\n")
    assert result.returncode == 1
    assert result.stdout == ""


def test_only_check_takes_it(tmp_path: Path) -> None:
    result = check(tmp_path, "build", "--types=json")
    assert result.returncode == 2
    assert result.stderr == "turkey: only 'turkey check' takes --types\n"
    result = check(tmp_path, "check", "--types=yaml")
    assert result.stderr == "turkey: unknown types format 'yaml'; the formats are 'text' and 'json'\n"
