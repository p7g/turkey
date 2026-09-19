"""What the Python checker's schemes and elaborated declarations hold for a family.

Internal: split from `test_families.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations

from turkey import ast
from turkey.driver import check
from turkey.types import TFam

CONTAINER = """
class Container c {
    type Elem c

    fun first(c) -> Elem c
}

instance Container (Array a) {
    type Elem = a

    fun first(xs) = xs[0]
}

type Box = Box { it : Int }

instance Container Box {
    type Elem = Int

    fun first(b) = b.it
}
"""


# -- declaring one ------------------------------------------------------------


# -- defining one -------------------------------------------------------------


# -- reducing one -------------------------------------------------------------


# -- what it makes run --------------------------------------------------------


def test_a_family_is_erased_before_the_evaluator_sees_it():
    checked = check(CONTAINER + "fun main() { print(Int.toString(first([1]))) }")
    scheme_ = next(s for n, s in checked.signatures if n == "main")
    assert not _has_family(scheme_.body)


def _has_family(t) -> bool:
    from turkey.types import TApp, TFun, TTuple, prune

    t = prune(t)
    if isinstance(t, TFam):
        return True
    if isinstance(t, TApp):
        return _has_family(t.fn) or _has_family(t.arg)
    if isinstance(t, TFun):
        return any(_has_family(p) for p in t.params) or _has_family(t.ret)
    if isinstance(t, TTuple):
        return any(_has_family(e) for e in t.elems)
    return False


# -- equality constraints (delta 39) ------------------------------------------

OPS = """
type Op = Inc(Int) | Loop(Array Op)

fun useOp(o : Op) -> Int = 1
"""


def test_an_equality_costs_no_dictionary():
    """`~` is not a class, so the filters that erase `HasField` erase it too."""
    src = OPS + """
    fun c[Iterator s, Item s ~ Op](ops : s) -> Int {
        var n = 0
        for op in ops { n = n + 1 }
        n
    }
    """
    checked = check(src)
    decl = next(s.decl for s in checked.ordered
                if isinstance(s, ast.SFun) and s.decl.name.endswith("#c"))
    assert [p.name for p in decl.dicts.preds] == ["Std.Classes#Iterator"]
    assert len(decl.dicts.params) == 1
