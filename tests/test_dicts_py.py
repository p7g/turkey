"""The shape of the evidence the Python elaborator records: which instance or dictionary each method use resolved to, and how many dictionary parameters a function gains. `dicts.core` pins the same elaboration as Core, through `boot`.

Internal: split from `test_dicts.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations

from dataclasses import fields


from turkey import ast
from turkey.driver import check
from turkey.evidence import FromDict, FromInstance

SHOW = """
class Display a {
    fun display(a) -> String
}

instance Display Int {
    fun display(n) = Int.toString(n)
}

instance Display Bool {
    fun display(b) = if b { "true" } else { "false" }
}

instance Display (Array a) : Display a {
    fun display(xs) {
        var s = ""
        for x in xs {
            s = s + display(x)
        }
        return s
    }
}
"""

ORD = """
class Egal a {
    fun egal(a, a) -> Bool
}

class Rank a : Egal a {
    fun underEq(a, a) -> Bool
}

instance Egal Int {
    fun egal(x, y) = x == y
}

instance Rank Int {
    fun underEq(x, y) = x <= y
}
"""


def _short(name: str) -> str:
    """A top-level binding is `Module#name` after resolution (M11a); the tests
    ask for it the way it was written."""
    return name.rpartition(".")[2].rpartition("#")[2]

def _decl(checked, name: str) -> ast.FunDecl:
    for item in checked.program.decls:
        if isinstance(item, ast.SFun) and _short(item.decl.name) == name:
            return item.decl
    raise AssertionError(f"no top-level fun '{name}'")


def uses(checked, fn: str) -> dict[str, list]:
    """The use sites inside one top-level function, by name, in source order.

    Scoped to a single body because a method's name occurs inside the instance
    that defines it as well as at every call, and the two are not the same
    question.
    """
    found: dict[str, list] = {}
    seen: set[int] = set()

    def walk(node) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
            return
        if not isinstance(node, ast.Node) or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, ast.EVar) and node.use is not None:
            found.setdefault(_short(node.name), []).append(node.use)
        for f in fields(node):
            walk(getattr(node, f.name))

    walk(_decl(checked, fn).body)
    return found


def params_of(checked, name: str) -> list[str]:
    """The dictionary parameters the binding of `name` gained."""
    decl = _decl(checked, name)
    return decl.dicts.params if decl.dicts else []


# -- what the evidence is -----------------------------------------------------


def test_a_known_type_resolves_to_its_instance():
    checked = check(SHOW + "fun main() { print(display(1)) }")
    (evidence,) = uses(checked, "main")["display"][0].evidence
    assert isinstance(evidence, FromInstance)
    assert evidence.inst.con == "Int"
    assert evidence.args == []


def test_an_unknown_type_resolves_to_the_dictionary_in_scope():
    """Inside a constrained function the evidence is a *parameter*, not a choice.

    This is the whole reason resolution waits for solving: at generation time
    `display(x)` looks exactly the same either way.
    """
    checked = check(SHOW + "fun twice[Display a](x : a) -> String = display(x) + display(x)")
    first, second = uses(checked, "twice")["display"]
    assert isinstance(first.evidence[0], FromDict)
    # Both occurrences take the same dictionary, and it is the one the
    # declaration abstracted over.
    assert first.evidence[0].name == second.evidence[0].name
    assert params_of(checked, "twice") == [first.evidence[0].name]


def test_an_instance_context_is_applied_to_the_dictionary_it_needs():
    checked = check(SHOW + "fun main() { print(display([1, 2])) }")
    (evidence,) = uses(checked, "main")["display"][0].evidence
    assert isinstance(evidence, FromInstance)
    assert evidence.inst.con == "Data.Array#Array"
    (arg,) = evidence.args
    assert isinstance(arg, FromInstance) and arg.inst.con == "Int"


def test_a_nested_instance_nests_its_evidence():
    checked = check(SHOW + "fun main() { print(display([[1], [2]])) }")
    (outer,) = uses(checked, "main")["display"][0].evidence
    (middle,) = outer.args
    (inner,) = middle.args
    assert [e.inst.con for e in (outer, middle, inner)] == [
        "Data.Array#Array", "Data.Array#Array", "Int",
    ]


def test_a_superclass_is_selected_rather_than_passed():
    """`[Rank a]` gives `egal` for free -- one dictionary, walked into.

    A `Monoid` dictionary carrying its `Semigroup` is what makes a superclass
    an implication rather than a second obligation on the caller.
    """
    checked = check(ORD + "fun same[Rank a](x : a, y : a) -> Bool = egal(x, y)")
    (evidence,) = uses(checked, "same")["egal"][0].evidence
    assert isinstance(evidence, FromDict)
    assert evidence.path == ("Main#Egal",)
    assert params_of(checked, "same") == [evidence.name]


# -- what abstracts over it ---------------------------------------------------


def test_a_field_access_carries_evidence_like_any_other_method():
    """A `HasField` is a class predicate, and so it does become a parameter.

    It used to be discharged by a declaration lookup and *erased*, which is
    what left a record-polymorphic body knowing a field's type and not its
    position -- uncompilable, and at worst compiled into a read at the wrong
    width. `get` is handed a dictionary of accessors instead. See FINDINGS 45.

    `OneOf` is still erased, and `bump` still takes nothing: it is a decision
    about which numeric type a literal has, and nothing at run time depends on
    the answer.
    """
    checked = check("fun get(r) = r.x\nfun bump(a : Int) = a + 1")
    (parameter,) = params_of(checked, "get")
    assert parameter.endswith("HasField.x")
    assert params_of(checked, "bump") == []


def test_a_function_gains_one_parameter_per_retained_predicate():
    src = SHOW + ORD + """
fun both[Display a, Rank a](x : a, y : a) -> String {
    if underEq(x, y) { return display(x) }
    return display(y)
}
"""
    assert len(params_of(check(src), "both")) == 2


def test_an_unconstrained_function_has_no_parameters():
    assert params_of(check("fun identity(x) = x"), "identity") == []


def test_a_mutually_recursive_group_shares_its_context():
    """One member's body may call another's, so the context cannot be per name.

    A per-name split would leave the call inside `even` needing a dictionary
    that `even`'s own signature never promised. Haskell 98 shares a group's
    context for exactly this reason.
    """
    src = SHOW + """
fun even(x, n : Int) {
    if n == 0 { return display(x) }
    return odd(x, n - 1)
}

fun odd(x, n) = even(x, n - 1)
"""
    checked = check(src)
    assert params_of(checked, "even") == params_of(checked, "odd")
    assert len(params_of(checked, "even")) == 1


# -- what it makes run --------------------------------------------------------


