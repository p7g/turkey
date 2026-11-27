"""`--diagnostics=json`: each diagnostic on stderr as one line of JSON.

The playground's editor reads these to mark the spans, so the objects are a
format other programs parse: every member, its order and the convention for
positions are checked here exactly, for one diagnostic of each shape.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests import bootc, toolchain


def check(tmp_path: Path, source: str, *flags: str) -> tuple[int, str]:
    (tmp_path / "main.gob").write_text(source)
    # The suite names the library relative to the repository root, and this
    # runs elsewhere.
    environment = dict(os.environ, TURKEY_LIB=str(bootc.REPO_ROOT / "lib"))
    result = subprocess.run(
        toolchain.command(bootc.binary(), "check", "--diagnostics=json", *flags, "main.gob"),
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120)
    return result.returncode, result.stderr


def diagnostics(stderr: str) -> list[dict]:
    return [json.loads(line) for line in stderr.splitlines()]


def test_lint_findings_carry_their_check_and_no_count_line(tmp_path: Path) -> None:
    status, stderr = check(tmp_path, (
        "fun main() {\n"
        "\tlet unused = 4\n"
        "\tmatch 3 {\n"
        "\t\tx -> print(x)\n"
        "\t}\n"
        "}\n"))
    assert status == 0, stderr
    assert stderr.endswith("}\n")
    assert diagnostics(stderr) == [
        {"file": "main.gob", "line": 2, "column": 6, "endLine": 2, "endColumn": 12,
         "severity": "warning", "stage": "warning", "code": "unused-variable",
         "message": "'unused' is never used; rename it '_unused' if that is meant",
         "notes": []},
        # A span over several lines ends after the closing brace.
        {"file": "main.gob", "line": 3, "column": 2, "endLine": 5, "endColumn": 3,
         "severity": "warning", "stage": "warning", "code": "irrefutable-match",
         "message": "this 'match' has one arm, whose pattern matches every value; use 'let'",
         "notes": []},
    ]


def test_the_members_are_in_a_fixed_order(tmp_path: Path) -> None:
    _, stderr = check(tmp_path, "fun main() {\n\tlet unused = 4\n}\n")
    assert stderr == (
        '{"file":"main.gob","line":2,"column":6,"endLine":2,"endColumn":12,'
        '"severity":"warning","stage":"warning","code":"unused-variable",'
        '"message":"\'unused\' is never used; rename it \'_unused\' if that is meant",'
        '"notes":[]}\n')


def test_a_denied_finding_is_an_error(tmp_path: Path) -> None:
    status, stderr = check(tmp_path, "fun main() {\n\tlet unused = 4\n}\n",
                           "--deny", "unused-variable")
    assert status == 1
    [found] = diagnostics(stderr)
    assert (found["severity"], found["stage"], found["code"]) == ("error", "error", "unused-variable")


def test_a_note_has_its_own_span(tmp_path: Path) -> None:
    status, stderr = check(tmp_path, (
        "type R = R { a : Int }\n"
        "\n"
        "fun main() {\n"
        "\tlet r = R { a = 1 }\n"
        "\tr.a = 2\n"
        "}\n"))
    assert status == 1
    assert diagnostics(stderr) == [
        {"file": "main.gob", "line": 5, "column": 2, "endLine": 5, "endColumn": 5,
         "severity": "error", "stage": "type error", "code": None,
         "message": "cannot assign to field 'a': it is not 'var' in 'R';"
                    " declare it 'var a' to make it assignable",
         "notes": [{"file": "main.gob", "line": 1, "column": 14, "endLine": 1, "endColumn": 15,
                    "message": "'a' is declared here"}]},
    ]


def test_columns_count_code_points(tmp_path: Path) -> None:
    # The emoji is one code point, two UTF-16 units and four bytes.
    status, stderr = check(tmp_path, 'fun main() {\n\tprint("\U0001F600" + 1)\n}\n')
    assert status == 1
    [found] = diagnostics(stderr)
    assert (found["line"], found["column"], found["endLine"], found["endColumn"]) == (2, 14, 2, 15)


def test_a_diagnostic_about_the_whole_file_has_no_positions(tmp_path: Path) -> None:
    status, stderr = check(tmp_path, "import No.Such\n\nfun main() {}\n")
    assert status == 1
    [found] = diagnostics(stderr)
    assert found["file"] == "main.gob"
    assert [found[k] for k in ("line", "column", "endLine", "endColumn")] == [None] * 4
    assert found["message"].startswith("cannot find module 'No.Such'")


def test_a_message_is_escaped(tmp_path: Path) -> None:
    status, stderr = check(tmp_path, 'fun main() { let s = "abc }\n')
    assert status == 1
    [found] = diagnostics(stderr)
    assert found["stage"] == "lex error"
    assert found["message"] == ("this string literal has no closing '\"' on its line;"
                                " write '\\n' for a line break")
    # The lexer points between two characters.
    assert (found["line"], found["column"], found["endLine"], found["endColumn"]) == (1, 22, 1, 22)


def test_text_is_the_default_and_names_the_note(tmp_path: Path) -> None:
    (tmp_path / "main.gob").write_text("type R = R { a : Int }\n\nfun main() {\n"
                                       "\tlet r = R { a = 1 }\n\tr.a = 2\n}\n")
    environment = dict(os.environ, TURKEY_LIB=str(bootc.REPO_ROOT / "lib"))
    result = subprocess.run(
        toolchain.command(bootc.binary(), "check", "--diagnostics=text", "main.gob"),
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120)
    assert result.stderr == (
        "main.gob:5:2: type error: cannot assign to field 'a': it is not 'var' in 'R';"
        " declare it 'var a' to make it assignable\n"
        "main.gob:1:14: note: 'a' is declared here\n")


def test_an_unknown_format_is_refused(tmp_path: Path) -> None:
    status, stderr = check(tmp_path, "fun main() {}\n", "--diagnostics=xml")
    assert status == 2
    assert stderr == "turkey: unknown diagnostics format 'xml'; the formats are 'text' and 'json'\n"
