"""`turkey fmt`, the formatter in `src/Turkey/Format.gob`.

The style is the pairs in `tests/fmt/`: each `NAME.gob` and `NAME.expected.gob`,
which is exactly what `turkey fmt` must make of it. The corpus -- `lib/`,
`src/`, `examples/` and every example in `docs/ref/` -- checks the formatter's
safety net instead: each file's output must parse to the same tree, hold the
same comments and format to itself, or the formatter refuses it with an
internal error. The corpus is never a reference for the style.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

ROOT = bootc.REPO_ROOT
GOLDENS = ROOT / "tests" / "fmt"
PAIRS = sorted(p.name[: -len(".expected.gob")] for p in GOLDENS.glob("*.expected.gob"))


def fmt(*argv: str, cwd: Path = ROOT, **variables: str) -> subprocess.CompletedProcess:
    return subprocess.run(toolchain.command(bootc.binary(), "fmt", *argv), cwd=cwd,
                          env=dict(os.environ, **variables), capture_output=True, text=True,
                          timeout=600)


@pytest.mark.parametrize("name", PAIRS)
def test_golden(tmp_path: Path, name: str) -> None:
    target = tmp_path / f"{name}.gob"
    shutil.copy(GOLDENS / f"{name}.gob", target)
    result = fmt(str(target))
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == (GOLDENS / f"{name}.expected.gob").read_bytes()


def test_every_expected_output_is_already_formatted() -> None:
    expected = [str(GOLDENS / f"{name}.expected.gob") for name in PAIRS]
    result = fmt("--check", *expected)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_check_names_the_files_it_would_change_and_writes_nothing(tmp_path: Path) -> None:
    messy = tmp_path / "messy.gob"
    tidy = tmp_path / "tidy.gob"
    messy.write_text("fun main() { print(1) ; print(2) }\n")
    tidy.write_text("fun main() {\n\tprint(1)\n}\n")
    result = fmt("--check", "messy.gob", "tidy.gob", cwd=tmp_path)
    assert (result.returncode, result.stdout) == (1, "messy.gob\n"), result.stderr
    assert messy.read_bytes() == b"fun main() { print(1) ; print(2) }\n"


def test_a_file_that_does_not_parse_is_reported_and_the_rest_are_done(tmp_path: Path) -> None:
    (tmp_path / "a.gob").write_text("fun main() { print(1) ; print(2) }\n")
    (tmp_path / "broken.gob").write_text("fun main( {\n")
    (tmp_path / "b.gob").write_text("fun main() {print(3)}\n")
    result = fmt("a.gob", "broken.gob", "b.gob", cwd=tmp_path)
    assert result.returncode == 3
    assert result.stderr.startswith("broken.gob:1:11: parse error: ")
    assert (tmp_path / "a.gob").read_bytes() == b"fun main() {\n\tprint(1)\n\tprint(2)\n}\n"
    assert (tmp_path / "broken.gob").read_bytes() == b"fun main( {\n"
    assert (tmp_path / "b.gob").read_bytes() == b"fun main() { print(3) }\n"
    # Under `--check` the status says the file is broken, not that it would
    # change.
    result = fmt("--check", "broken.gob", cwd=tmp_path)
    assert result.returncode == 3


def test_a_refused_file_is_an_internal_error_and_the_rest_are_done(tmp_path: Path) -> None:
    (tmp_path / "a.gob").write_text("fun main() {print(1)}\n")
    (tmp_path / "b.gob").write_text("fun main() {print(2)}\n")
    result = fmt("a.gob", "b.gob", cwd=tmp_path, TURKEY_TEST_FMT="refuse")
    assert result.returncode == 2
    assert result.stderr == (
        "a.gob: internal error: the formatter refuses this file: refused for a test\n"
        "b.gob: internal error: the formatter refuses this file: refused for a test\n")
    assert (tmp_path / "a.gob").read_bytes() == b"fun main() {print(1)}\n"
    # Formatted text that does not parse is the formatter's fault too.
    result = fmt("--check", "a.gob", cwd=tmp_path, TURKEY_TEST_FMT="garble")
    assert result.returncode == 2
    assert result.stderr.startswith(
        "a.gob: internal error: the formatter refuses this file: the formatted text does not parse: ")


def test_a_line_ending_in_a_directive_keeps_its_code(tmp_path: Path) -> None:
    # The directive covers its line. The long call would otherwise be broken
    # across lines, and the two-line `let` joined onto one.
    source = (
        "fun main() {\n"
        "    report(diagnostics, \"unexpected token while parsing the arms of a match\", span, Severity.Error, hint) --@expect(unreachable-code)\n"
        "    let x = add(1,\n"
        "        2) --@expect(unreachable-code)\n"
        "}\n"
    )
    target = tmp_path / "directives.gob"
    target.write_text(source)
    result = fmt(str(target))
    assert result.returncode == 0, result.stderr
    assert target.read_text() == (
        "fun main() {\n"
        "\treport(diagnostics, \"unexpected token while parsing the arms of a match\", span, Severity.Error, hint) --@expect(unreachable-code)\n"
        "\tlet x = add(1,\n"
        "\t    2) --@expect(unreachable-code)\n"
        "}\n"
    )


def _fences() -> list[tuple[str, str, bool]]:
    """Every `kotlin` example in `docs/ref/`: its name, its source, and
    whether the compiler is meant to reject it."""
    out = []
    for chapter in sorted((ROOT / "docs" / "ref").glob("*.md")):
        lines = chapter.read_text().split("\n")
        i = 0
        while i + 1 < len(lines):
            directive = re.match(r"\s*<!--\s*(.*?)\s*-->\s*$", lines[i])
            if directive and lines[i + 1].strip() == "```kotlin":
                j = i + 2
                while lines[j].strip() != "```":
                    j += 1
                out.append((f"{chapter.stem}_{i + 2}", "\n".join(lines[i + 2:j]) + "\n",
                            directive.group(1).startswith("error")))
                i = j
            i += 1
    return out


def test_the_corpus_formats_safely(tmp_path: Path) -> None:
    files = sorted(str(p.relative_to(ROOT)) for top in ("lib", "src", "examples")
                   for p in (ROOT / top).rglob("*.gob"))
    accepted = []
    rejected = []
    for name, source, error in _fences():
        path = tmp_path / f"{name}.gob"
        path.write_text(source)
        (rejected if error else accepted).append(str(path))
    assert len(files) > 100 and len(accepted) > 100
    # `--check` writes nothing; it exits 1 because the corpus is not formatted.
    result = fmt("--check", *files, *accepted)
    assert result.returncode in (0, 1) and result.stderr == "", result.stderr
    # A file that does not parse is reported, and the run goes on.
    result = fmt("--check", *rejected)
    assert "internal error" not in result.stderr, result.stderr


def _columns(line: str) -> int:
    return sum(4 if c == "\t" else 1 for c in line)


def _may_run_long(line: str) -> bool:
    """Whether a formatted line longer than the width is long for a reason the
    layout cannot break: comments, which do not count toward the width, or a
    string literal."""
    code = re.sub(r"\{-.*?-\}", "", line)
    if code.lstrip().startswith("--"):
        return True
    code = re.sub(r"\s--.*$", "", code).rstrip()
    if _columns(code) <= 100:
        return True
    return _columns(re.sub(r'"(\\.|[^"\\])*"', '""', code)) <= 100


def test_the_corpus_fits_the_width(tmp_path: Path) -> None:
    # A line over the width that could have broken is a layout that measured
    # wrongly, such as a head whose `{` went uncounted.
    copies = []
    for top in ("lib", "src", "examples"):
        for p in (ROOT / top).rglob("*.gob"):
            target = tmp_path / p.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, target)
            copies.append(str(target))
    result = fmt(*copies)
    assert result.returncode == 0, result.stderr
    long = []
    for name in copies:
        for n, line in enumerate(Path(name).read_text().split("\n"), 1):
            if _columns(line) > 100 and not _may_run_long(line):
                long.append(f"{Path(name).relative_to(tmp_path)}:{n}: {line.strip()}")
    assert long == []


def _with_block_comments(text: str) -> str:
    """`text` with a block comment in every space between two tokens of each
    code line. A line with a quote or a comment on it is left alone, so no
    comment is put inside a literal or another comment."""
    lines = text.split("\n")
    n = 0
    for i, line in enumerate(lines):
        if any(t in line for t in ('"', "'", "--", "{-", "-}")):
            continue
        start = len(line) - len(line.lstrip())
        pieces = [line[:start]]
        for ch in line[start:]:
            if ch == " ":
                n += 1
                pieces.append(f" {{- z{n} -}} ")
            else:
                pieces.append(ch)
        lines[i] = "".join(pieces)
    return "\n".join(lines)


def test_block_comments_all_over_the_corpus_format_safely(tmp_path: Path) -> None:
    # A block comment can go between any two tokens, so one in every space
    # reaches nearly every place the layout puts code, over a quarter of a
    # million of them. None may make the formatter refuse a file.
    paths = []
    for top in ("lib", "src", "examples"):
        for source in sorted((ROOT / top).rglob("*.gob")):
            target = tmp_path / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_with_block_comments(source.read_text()))
            paths.append(str(target))
    result = fmt("--check", *paths)
    assert result.returncode in (0, 1) and result.stderr == "", result.stderr


# Comments and directives in the places that are hardest to keep: between two
# end-of-line comments, inside types and argument lists, around `else`, and on
# lines a directive covers. Each must format without a refusal, and its output
# must be formatted already.
AWKWARD = {
    "comment_in_type": "fun f() -> (Int,\n    -- c\n    Int) {\n    (1, 2)\n}\n",
    "comment_after_rest": "import Data.Vec (..) -- c\n",
    "two_end_comments_let": "fun main() {\n    let s = -- a\n        \"x\" -- b\n}\n",
    "two_end_comments_arm": "fun f(t : Option Int) -> Int = match t {\n    Some(x) -> -- a\n        x -- b\n    None -> 0\n}\n",
    "two_end_comments_signature": "fun f(x : Int) -- a\n        -> Int { -- b\n    x\n}\n",
    "block_comment_before_arguments": "fun main() { let y = f {- c -} (1) }\n",
    "block_comment_in_arguments": "fun main() { foo(1, {- c -} 2) }\n",
    "block_comment_in_chain": "fun main() { let x = 1 {- c -} + 2 }\n",
    "block_comments_around_else": "fun main() {\n    if {- c -} a {\n        b()\n    } {- d -} else {- e -} {\n        c()\n    }\n}\n",
    "block_comment_after_fun": "fun {- c -} f() = 1\n",
    "directive_after_broken_signature": "fun f(a : Int,\n      b : Int) -> Int { --@expect(unreachable-code)\n    1\n}\n",
    "directive_after_long_signature": "fun f(aaaaaaaaaaaaaaaaaaaa : Int, bbbbbbbbbbbbbbbbbbbbbbbbbb : Int, cccccccccccccccccccc : Int) -> Int { --@expect(unreachable-code)\n    1\n}\n",
    "directive_after_statements": "fun main() {\n    let a = 1; let b = 2 --@expect(unreachable-code)\n    print(a + b)\n}\n",
    "directive_after_block": "fun main() {\n    let a = { print(1); 2 } --@expect(unreachable-code)\n    print(a)\n}\n",
    "end_comment_after_equals": "fun main() {\n    let s = -- a\n        f(x)\n}\n",
    "end_comment_after_arrow": "fun g(t : Option Int) -> Int = match t {\n    Some(x) -> -- a\n        f(x)\n    None -> 0\n}\n",
    "block_comment_first_argument": "fun f() {\n  h({- first -} x)\n}\n",
    "block_comment_in_array": "let xs = [1, {- c -} 2]\n",
    "block_comment_before_equals": "fun f() {\n  let x {- c -} = 1\n}\n",
    "block_comment_under_directive": "fun f() {\n  foo(1, {- c -} 2) --@expect(unreachable-code)\n}\n",
    "directive_after_if": "fun main() {\n    if c { --@expect(unreachable-code)\n        a()\n    }\n}\n",
    "broken_value_after_end_comment": "fun main() {\n    let s = -- a\n        f(aaaaaaaaaaaaaaaaaaaaaa, bbbbbbbbbbbbbbbbbbbbbbbbbbbb, cccccccccccccccccccccccc, dddddddddddddddddd)\n    let t = -- b\n        {print(1); 2}\n}\n",
    "broken_arm_after_end_comment": "fun g(t : Option Int) -> Int = match t {\n    Some(x) -> -- a\n        f(aaaaaaaaaaaaaaaaaaaaaa, bbbbbbbbbbbbbbbbbbbbbbbbbbbb, cccccccccccccccccccccccc, dddddddddddddddddd)\n    None -> 0\n}\n",
    "block_comment_in_broken_list": "fun main() {\n    h(\n        a, {- z -} b,\n        c,\n    )\n}\n",
    "block_comment_in_sub_list": "import M (T({- c -} A, B), g)\n",
    "block_comment_in_export_sub_list": "export (Status(A, {- c -} B, C), f)\n",
    "block_comment_in_class": "class C a { {- c -} fun m(a) -> Int }\n",
    "block_comment_in_record": "fun f() -> P = P { {- c -} x = 1, y = 2 }\nfun g() -> P = P { x = 1, {- c -} y = 2 }\ntype R = R { {- c -} x : Int, y : Int }\n",
    "block_comment_around_body_equals": "fun f(x : Int) -> Int {- z -} = x\nfun g(x : Int) -> Int = {- z -} x\n",
    "block_comment_in_infix": "--- Doc.\ninfix {- z -} Sequence (|>>) = bind\n",
    "block_comment_in_one_line_if": "fun at(xs : Array Int, i : Int) -> Option Int =\n\tif i < 0 { {- z -} None } else { Some(xs[i]) }\n",
    "block_comment_in_empty_block": "fun f() { {- c -} }\n",
    "block_comment_before_equals_type": "type Section {- a -} =\n    --- Code.\n    Text\n    | {- b -} Data\n",
    "end_comment_before_equals": "fun g() {\n    let x -- c\n        = 1\n    x\n}\n",
    "two_block_comments_one_token": "fun main() {\n    f(a, b {- c1 -}, {- c2 -})\n    let xs = [1, 2 {- a -}, {- b -}]\n    g({- a -} {- b -})\n}\n",
    "block_comment_on_moved_rest": "import N (g as _, {- c -} .., h as _)\nimport Q (.. {- c -}, g as _)\n",
    "block_comment_before_dropped_comma": "fun f() -> P = P {\n    x = 1 {- c -}, y = 2\n    z = 3\n}\n",
    "block_comment_in_aligned_rows": "fun f(x : Int) -> Int = match x {\n    1 -> 0\n    {- c -} 22 -> 1\n    3 {- d -} | 4 -> 2\n    _ -> 3\n}\ntype F = F {\n    {- c -} lines : Vec String\n    tokens : Int\n}\n",
    "comment_before_aligned_arrow": "fun f(x : Int) -> Int = match x {\n    1 {- c -} -> 0\n    22 -> {- d -} 1\n    333 {- e -} -> 2\n    _ -> 3\n}\n",
    "comment_in_aligned_nested_alternatives": "fun f(x : Option Int) -> Int = match x {\n    Some(1 | 2) -> 0\n    Some(5 | 6 | 7) -> 2\n    Some(1 {- f -} | 2) -> 3\n    _ -> 5\n}\n",
    "comment_before_aligned_close": "fun f(x : Option Int) -> Int = match x {\n    Some(0) -> 0\n    Some({- f -} 1) -> 3\n    Some(2 {- f -}) -> 3\n    _ -> 5\n}\n",
    "comment_before_aligned_equals": "type R = R { x : Int, yyyy : Int, z : Int }\nfun k() -> R = R {\n    x {- a -} = 1, yyyy = {- b -} 2\n    z {- c -} = 3\n}\n",
    "comment_before_aligned_colon": "type R = R {\n    x {- a -} : Int\n    yyyy : Int, z : Int\n}\n",
    "comment_before_aligned_trailing_comment": "fun f(x : Int) -> Int = x\nfun main() {\n    let a = f(1 {- c -}) -- one\n    let bb = 2 -- two\n}\n",
    "comment_before_instance_class": "class C a { fun s(a) -> Int }\ninstance -- c\nC Int {\n    fun s(x) = x\n}\n",
    "comment_before_block_brace": "fun f(second : Int) -> Int = 1\n    + (if second == 60 -- z\n{ 59 } else { second })\n",
    "comment_after_variant_bar": "type T\n    = A\n    --- Doc.\n    | -- z\nB(Int)\n    | C\n",
    "foreign_blocks": "foreign \"env\" {\n    \"log\" fun log(x : Int) -> Unit\n}\nforeign \"ssl\" 3 { \"f\" fun g(x : Int) -> Int }\n",
    "comments_before_aligned_trailing_comments": "fun main() {\n    let a = f(1) {- c -} -- one\n    let bb = 2 -- two\n    let c = 3 {- x -} {- y -} -- three\n}\n",
    "no_hug_after_same_shape": "fun build(obj : Object) {\n    let byLibrary = fun(a : Symbol, b : Symbol) =\n        Array.threeWay((ordinal(a), a.name), (ordinal(b), b.name))\n    send(socket, [headerLength, payloadLength, checksumOfThePayload], [sequenceNumber, flagsOfThePacket])\n}\n",
    "row_too_wide_to_align": "fun clashing(s : Solver, a : Ty.Type, b : Ty.Type, open : Set.Set Int) -> Bool {\n    if opened(a, open) || opened(b, open) { return False }\n    match (Ty.prune(a), Ty.prune(b)) {\n        (TVar(v), TCon(con)) | (TCon(con), TVar(v)) -> !numeralAdmits(s, v, con)\n        (TVar(_), _) | (_, TVar(_)) -> False\n        (TFam(_, _, _), _) | (_, TFam(_, _, _)) -> False\n        (TBottom, _) | (_, TBottom) -> False\n        (TCon(c1), TCon(c2)) -> c1.name != c2.name || c1.level != c2.level || c1.uid != c2.uid\n        (TApp(f1, a1, _), TApp(f2, a2, _)) -> clashing(s, f1, f2, open) || clashing(s, a1, a2, open)\n        (TFun(p1, r1), TFun(p2, r2)) -> clashingAll(s, p1, p2, open) || clashing(s, r1, r2, open)\n        (TTuple(e1), TTuple(e2)) -> clashingAll(s, e1, e2, open)\n        (x, y) -> !Ty.sameType(x, y)\n    }\n}\n",
}

# What some of them must format to, exactly.
AWKWARD_OUTPUT = {
    "end_comment_after_equals": "fun main() {\n\tlet s = -- a\n\t\tf(x)\n}\n",
    "end_comment_after_arrow":
        "fun g(t : Option Int) -> Int = match t {\n\tSome(x) -> -- a\n\t\tf(x)\n\tNone -> 0\n}\n",
    "two_end_comments_let": "fun main() {\n\tlet s = -- a\n\t\t\"x\" -- b\n}\n",
    "block_comment_first_argument": "fun f() {\n\th({- first -} x)\n}\n",
    "block_comment_in_arguments": "fun main() { foo(1, {- c -} 2) }\n",
    "block_comment_in_array": "let xs = [1, {- c -} 2]\n",
    "block_comment_in_chain": "fun main() { let x = 1 {- c -} + 2 }\n",
    "block_comment_before_equals": "fun f() {\n\tlet x {- c -} = 1\n}\n",
    "block_comment_under_directive": "fun f() {\n\tfoo(1, {- c -} 2) --@expect(unreachable-code)\n}\n",
    "directive_after_if": "fun main() {\n\tif c { --@expect(unreachable-code)\n\t    a()\n\t}\n}\n",
    "broken_value_after_end_comment": (
        "fun main() {\n\tlet s = -- a\n\t\tf(\n"
        + "".join(f"\t\t\t{x},\n" for x in "aaaaaaaaaaaaaaaaaaaaaa, bbbbbbbbbbbbbbbbbbbbbbbbbbbb, cccccccccccccccccccccccc, dddddddddddddddddd".split(", "))
        + "\t\t)\n\tlet t = -- b\n\t\t{\n\t\t\tprint(1)\n\t\t\t2\n\t\t}\n}\n"),
    "broken_arm_after_end_comment": (
        "fun g(t : Option Int) -> Int = match t {\n\tSome(x) -> -- a\n\t\tf(\n"
        + "".join(f"\t\t\t{x},\n" for x in "aaaaaaaaaaaaaaaaaaaaaa, bbbbbbbbbbbbbbbbbbbbbbbbbbbb, cccccccccccccccccccccccc, dddddddddddddddddd".split(", "))
        + "\t\t)\n\tNone -> 0\n}\n"),
    "block_comment_in_broken_list": "fun main() {\n\th(\n\t\ta,\n\t\t{- z -} b,\n\t\tc,\n\t)\n}\n",
    "block_comment_in_sub_list": "import M (T({- c -} A, B), g)\n",
    "block_comment_in_export_sub_list": "export (Status(A, {- c -} B, C), f)\n",
    "block_comment_in_class": "class C a { {- c -} fun m(a) -> Int }\n",
    "block_comment_in_record": "fun f() -> P = P { {- c -} x = 1, y = 2 }\nfun g() -> P = P { x = 1, {- c -} y = 2 }\ntype R = R { {- c -} x : Int, y : Int }\n",
    "block_comment_around_body_equals": "fun f(x : Int) -> Int {- z -} = x\nfun g(x : Int) -> Int = {- z -} x\n",
    "block_comment_in_infix": "--- Doc.\ninfix {- z -} Sequence (|>>) = bind\n",
    "block_comment_in_empty_block": "fun f() { {- c -} }\n",
    "block_comment_before_equals_type": "type Section {- a -}\n\t--- Code.\n\t= Text\n\t| {- b -} Data\n",
    "end_comment_before_equals": "fun g() {\n\tlet x -- c\n\t\t= 1\n\tx\n}\n",
    "two_block_comments_one_token":
        "fun main() {\n\tf(a, b {- c1 -} {- c2 -})\n\tlet xs = [1, 2 {- a -} {- b -}]\n\tg({- a -} {- b -})\n}\n",
    "block_comment_on_moved_rest": "import N (g as _, h as _, {- c -} ..)\nimport Q (g as _, .. {- c -})\n",
    "block_comment_before_dropped_comma": "fun f() -> P = P {\n\tx = 1 {- c -}\n\ty = 2\n\tz = 3\n}\n",
    "block_comment_in_aligned_rows": (
        "fun f(x : Int) -> Int = match x {\n\t1             -> 0\n\t{- c -} 22    -> 1\n"
        "\t3 {- d -} | 4 -> 2\n\t_             -> 3\n}\n"
        "type F = F {\n\t{- c -} lines : Vec String\n\ttokens        : Int\n}\n"),
    "comment_before_aligned_arrow": (
        "fun f(x : Int) -> Int = match x {\n"
        "\t1   {- c -} -> 0\n"
        "\t22          -> {- d -} 1\n"
        "\t333 {- e -} -> 2\n"
        "\t_           -> 3\n"
        "}\n"
    ),
    "comment_in_aligned_nested_alternatives": (
        "fun f(x : Option Int) -> Int = match x {\n"
        "\tSome(1 | 2)         -> 0\n"
        "\tSome(5 | 6 | 7)     -> 2\n"
        "\tSome(1 {- f -} | 2) -> 3\n"
        "\t_                   -> 5\n"
        "}\n"
    ),
    "comment_before_aligned_close": (
        "fun f(x : Option Int) -> Int = match x {\n"
        "\tSome(0)         -> 0\n"
        "\tSome({- f -} 1) -> 3\n"
        "\tSome(2 {- f -}) -> 3\n"
        "\t_               -> 5\n"
        "}\n"
    ),
    "comment_before_aligned_equals": (
        "type R = R { x : Int, yyyy : Int, z : Int }\n"
        "fun k() -> R = R {\n"
        "\tx    {- a -} = 1\n"
        "\tyyyy         = {- b -} 2\n"
        "\tz    {- c -} = 3\n"
        "}\n"
    ),
    "comment_before_aligned_colon": (
        "type R = R {\n"
        "\tx    {- a -} : Int\n"
        "\tyyyy         : Int\n"
        "\tz            : Int\n"
        "}\n"
    ),
    "comment_before_aligned_trailing_comment": (
        "fun f(x : Int) -> Int = x\n"
        "fun main() {\n"
        "\tlet a = f(1 {- c -}) -- one\n"
        "\tlet bb = 2           -- two\n"
        "}\n"
    ),
    "comment_before_instance_class": (
        "class C a { fun s(a) -> Int }\n"
        "instance -- c\n"
        "\tC Int {\n"
        "\tfun s(x) = x\n"
        "}\n"
    ),
    "comment_before_block_brace": (
        "fun f(second : Int) -> Int =\n"
        "\t1\n"
        "\t\t+ (if second == 60 { -- z\n"
        "\t\t\t59\n"
        "\t\t} else {\n"
        "\t\t\tsecond\n"
        "\t\t})\n"
    ),
    "comment_after_variant_bar": (
        "type T\n"
        "\t= A\n"
        "\t--- Doc.\n"
        "\t| -- z\n"
        "\t\tB(Int)\n"
        "\t| C\n"
    ),
    "foreign_blocks": (
        "foreign \"env\" {\n"
        "\t\"log\" fun log(x : Int) -> Unit\n"
        "}\n"
        "foreign \"ssl\" 3 {\n"
        "\t\"f\" fun g(x : Int) -> Int\n"
        "}\n"
    ),
    "comments_before_aligned_trailing_comments": (
        "fun main() {\n"
        "\tlet a = f(1) {- c -}         -- one\n"
        "\tlet bb = 2                   -- two\n"
        "\tlet c = 3    {- x -} {- y -} -- three\n"
        "}\n"
    ),
    "no_hug_after_same_shape": (
        "fun build(obj : Object) {\n"
        "\tlet byLibrary = fun(a : Symbol, b : Symbol) = Array.threeWay(\n"
        "\t\t(ordinal(a), a.name),\n"
        "\t\t(ordinal(b), b.name),\n"
        "\t)\n"
        "\tsend(\n"
        "\t\tsocket,\n"
        "\t\t[headerLength, payloadLength, checksumOfThePayload],\n"
        "\t\t[sequenceNumber, flagsOfThePacket],\n"
        "\t)\n"
        "}\n"
    ),
    "row_too_wide_to_align": (
        "fun clashing(s : Solver, a : Ty.Type, b : Ty.Type, open : Set.Set Int) -> Bool {\n"
        "\tif opened(a, open) || opened(b, open) { return False }\n"
        "\tmatch (Ty.prune(a), Ty.prune(b)) {\n"
        "\t\t(TVar(v), TCon(con)) | (TCon(con), TVar(v)) -> !numeralAdmits(s, v, con)\n"
        "\t\t(TVar(_), _) | (_, TVar(_))                 -> False\n"
        "\t\t(TFam(_, _, _), _) | (_, TFam(_, _, _))     -> False\n"
        "\t\t(TBottom, _) | (_, TBottom)                 -> False\n"
        "\t\t(TCon(c1), TCon(c2)) -> c1.name != c2.name || c1.level != c2.level || c1.uid != c2.uid\n"
        "\t\t(TApp(f1, a1, _), TApp(f2, a2, _)) -> clashing(s, f1, f2, open) || clashing(s, a1, a2, open)\n"
        "\t\t(TFun(p1, r1), TFun(p2, r2)) -> clashingAll(s, p1, p2, open) || clashing(s, r1, r2, open)\n"
        "\t\t(TTuple(e1), TTuple(e2))                    -> clashingAll(s, e1, e2, open)\n"
        "\t\t(x, y)                                      -> !Ty.sameType(x, y)\n"
        "\t}\n"
        "}\n"
    ),
}


@pytest.mark.parametrize("name", sorted(AWKWARD))
def test_awkward_input_formats(tmp_path: Path, name: str) -> None:
    target = tmp_path / f"{name}.gob"
    target.write_text(AWKWARD[name])
    result = fmt(str(target))
    assert (result.returncode, result.stderr) == (0, "")
    if name in AWKWARD_OUTPUT:
        assert target.read_bytes() == AWKWARD_OUTPUT[name].encode()
    result = fmt("--check", str(target))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_a_file_whose_name_starts_with_a_dash(tmp_path: Path) -> None:
    (tmp_path / "-x.gob").write_text("fun main() {print(1)}\n")
    result = fmt("--", "-x.gob", cwd=tmp_path)
    assert (result.returncode, result.stderr) == (0, "")
    assert (tmp_path / "-x.gob").read_bytes() == b"fun main() { print(1) }\n"


def test_a_command_line_it_cannot_read_has_its_own_status(tmp_path: Path) -> None:
    result = fmt("--bogus", "a.gob", cwd=tmp_path)
    assert (result.returncode, result.stderr) == (4, "turkey: unknown option --bogus\n")


def test_a_directive_line_keeps_its_code(tmp_path: Path) -> None:
    target = tmp_path / "directives.gob"
    target.write_text(AWKWARD["directive_after_statements"] + AWKWARD["directive_after_broken_signature"])
    assert fmt(str(target)).returncode == 0
    lines = target.read_text().split("\n")
    assert "\tlet a = 1; let b = 2 --@expect(unreachable-code)" in lines
    assert "      b : Int) -> Int { --@expect(unreachable-code)" in lines


def test_parentheses_and_literals_stay_as_written(tmp_path: Path) -> None:
    source = (
        "fun f(x : (Int)) -> Option (Vec Int) = None\n"
        "fun g(pair : (Int, Int)) -> Int {\n"
        "\tlet (a, _) = pair\n"
        "\tlet (b) = a\n"
        "\tif let ((x, _) | (_, x)) = pair { x } else { 0x10 + 1_000 + b }\n"
        "}\n"
        "let s = \"\\u{41}\"\n"
        "let h = \\Iter.map(_, g)\n"
    )
    target = tmp_path / "written.gob"
    target.write_text(source)
    result = fmt("--check", str(target))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
