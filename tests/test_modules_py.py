"""The Python loader's module order.

Internal: split from `test_modules.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations

from turkey.driver import check

HELPER = """
module Helper (twice, greet)

fun twice(n : Int) -> Int = n + n

fun greet(who : String) -> String = "hello, " + who

fun secret() -> Int = 7
"""


# -- what an import brings ----------------------------------------------------


def test_an_implicit_prelude_is_a_real_dependency_edge(tmp_path):
    checked = check("fun identity(n : Int) -> Int = n", None, [tmp_path])
    names = [module.name for module in checked.modules]
    assert names.index("Prelude") < names.index("Main")


# -- what an export list withholds --------------------------------------------


# -- which name wins ----------------------------------------------------------


# -- the graph ----------------------------------------------------------------


# -- diagnostics --------------------------------------------------------------


# -- the library is written in the language (M11b) ----------------------------


# -- a type and an instance know which module made them (M11c) ----------------

SHAPE = """
module Shape (Node(..), leaf)

type Node = Leaf | Fork(Node, Node)

fun leaf() -> Node = Leaf
"""


# -- coherence ----------------------------------------------------------------


