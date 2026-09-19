"""Which evidence the Python elaborator records for an operator.

Internal: split from `test_operators.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations

from turkey import ast
from turkey.driver import check
from turkey.evidence import FromDict, FromInstance

MONEY = """
type Money = Money { cents : Int }

instance Add Money {
    fun add(a, b) = Money { cents = a.cents + b.cents }
}
"""


def _short(name: str) -> str:
    """A top-level binding is `Module#name` after resolution (M11a); the tests
    ask for it the way it was written."""
    return name.rpartition(".")[2].rpartition("#")[2]

def _uses(checked, fn: str) -> list[ast.EVar]:
    """Every `EVar` inside one top-level function, in source order."""
    from dataclasses import fields

    found: list[ast.EVar] = []
    seen: set[int] = set()

    def walk(node) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not isinstance(node, ast.Node) or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, ast.EVar):
            found.append(node)
        for f in fields(node):
            walk(getattr(node, f.name))

    for item in checked.program.decls:
        if isinstance(item, ast.SFun) and _short(item.decl.name) == fn:
            walk(item.decl.body)
    return found


# -- what an operator is ------------------------------------------------------


def test_an_operator_is_a_use_of_its_method():
    """`+` carries an ordinary `Use`, resolved by the ordinary machinery."""
    checked = check("fun f(x : Int) -> Int = x + 1")
    (use,) = [v.use for v in _uses(checked, "f") if _short(v.name) == "add"]
    (evidence,) = use.evidence
    assert isinstance(evidence, FromInstance)
    assert evidence.inst.cls == "Std.Classes#Add" and evidence.inst.con == "Int"


def test_an_operator_on_an_open_type_takes_a_dictionary():
    checked = check("fun twice(x) = x + x")
    (use,) = [v.use for v in _uses(checked, "twice")
              if _short(v.name) == "add"]
    assert isinstance(use.evidence[0], FromDict)


# -- literals, still open -----------------------------------------------------


# -- the for loop -------------------------------------------------------------


# -- the boundary the prelude draws -------------------------------------------


def test_a_program_may_declare_a_class_with_a_prelude_class_short_name():
    checked = check("class Add a { fun add(a, a) -> a }")
    assert "Main#Add" in checked.classes.classes
    assert "Std.Classes#Add" in checked.classes.classes


