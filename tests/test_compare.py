"""`src/Turkey/Compare.gob`: whether two parse trees hold the same syntax.

`src/CompareCases.gob` parses each pair of files it is given and prints `same`,
or where the first file's tree first differs from the second's.
"""

from pathlib import Path

import pytest

from tests import lang

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "src" / "CompareCases.gob"

# Each case: the first source, the second, and what the driver prints.
CASES = [
    # Layout, commas, `;`, comments and a block body's `=` are not in the tree.
    ("fun f() = { 1 }\n", "fun f() {\n    1\n}\n", "same"),
    ("let xs = [1, 2,]\n", "let xs = [\n    1,\n    2\n]\n", "same"),
    ("type P = P { x : Int, y : Int }\n", "type P = P {\n    x : Int\n    y : Int\n}\n", "same"),
    ("fun f() { let a = 1; a }\n", "fun f() {\n    -- one\n\n    let a = 1\n    a\n}\n", "same"),
    # The parameter `_` makes gets a fresh name in each parse.
    ("let f = Iter.map(xs, _ + 1)\n", "let f = Iter.map(xs, _ + 1)\n", "same"),
    ("let f = \\g(_, _)\n", "let f = \\g(_, _)\n", "same"),
    # Grouping parentheses are in the tree, even where they change nothing.
    ("let a = (b + c) * d\n", "let a = b + c * d\n", "differs at 1:9"),
    ("let a = (b)\n", "let a = b\n", "differs at 1:9"),
    ("let f = (_ + 1) * 2\n", "let f = _ + 1 * 2\n", "differs at 1:9"),
    # So is everything else the program says.
    ("let a = b + c\n", "let a = c + b\n", "differs at 1:9"),
    ("let a = b + c\n", "let a = b - c\n", "differs at 1:9"),
    ("fun f(x : Int) -> Int = x\n", "fun f(x : Int) -> Bool = x\n", "differs at 1:19"),
    ("import A (b)\nlet x = 1\n", "import A (c)\nlet x = 1\n", "differs at 1:1"),
    ("let f = Iter.map(xs, _ + 1)\n", "let f = Iter.map(xs, fun(y) = y + 1)\n", "differs at 1:22"),
]


def test_the_cases_compare_as_expected(tmp_path: Path) -> None:
    paths = []
    for i, (first, second, _) in enumerate(CASES):
        a = tmp_path / f"{i}a.gob"
        b = tmp_path / f"{i}b.gob"
        a.write_text(first, encoding="utf-8")
        b.write_text(second, encoding="utf-8")
        paths += [str(a), str(b)]
    printed = lang.run(DRIVER, args=tuple(paths), cwd=REPO_ROOT).stdout.splitlines()
    assert printed == [expected for _, _, expected in CASES]
