"""What inference kept: every expression's type, and every use's type arguments.

`turkey/typed.py` records during generation what the solver decides later, and
this file is the part no golden can display -- a program's *output* is the same
whether or not a table was filled in behind it. The two questions are separate
on purpose: whether a program runs, and whether the compiler still knows what
each of its expressions was.

The hazards are the point. A recorded type is written while it is still a
variable, so the interesting failures are not "wrong type" but "not yet a type
at all": a numeric literal whose type was still a *decision* (`TSet`), and a
family application that never reduced (`TFam`). Both must be gone by the time
anything reads the table, and both have a test here.
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from turkey import ast
from turkey.driver import check
from turkey.modules import SEP
from turkey.typed import children
from turkey.types import TFam, Type, prune, show

PROGRAMS = pathlib.Path(__file__).resolve().parent / "programs"


def typed(src: str):
    checked = check(src)
    return checked, checked.types


def walk(node):
    """Every node under one, the entry module's own and no one else's.

    The table holds the whole program, prelude included -- it is one table for
    one program, the way `DeclTable` and the environment are. A test that asks
    "what is on line 3" has to say whose line 3, or it gets the Prelude's.
    """
    yield node
    for f in dataclasses.fields(node):
        value = getattr(node, f.name)
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, ast.Node):
                yield from walk(item)


def entry_nodes(checked) -> list[ast.Node]:
    out: list[ast.Node] = []
    for decl in checked.program.decls:
        out.extend(walk(decl))
    return out


def types_at(checked, kind: type, line: int) -> list[str]:
    """Every recorded type for a node of one kind on one line, deduplicated.

    Keyed by span rather than by identity because that is what a reader of the
    source can point at.
    """
    out = []
    for node in entry_nodes(checked):
        if isinstance(node, kind) and node.span and node.span.line == line:
            if node not in checked.types:
                continue
            shown = show(checked.types.of(node))
            if shown not in out:
                out.append(shown)
    return sorted(out)


def contains(ty: Type, kind: type) -> bool:
    ty = prune(ty)
    return isinstance(ty, kind) or any(contains(c, kind) for c in children(ty))


# -- that there is a type at all ---------------------------------------------


def test_every_expression_has_a_type():
    checked, table = typed("fun f(n : Int) -> Int = n + 1\nfun main() { print(f(2)) }")
    # Not a fixed number -- the prelude is compiled too, and its size is not
    # this test's business. What matters is that the table is not empty and
    # that a node picked out of the source is in it.
    assert len(table) > 100
    assert types_at(checked, ast.ELit, 1) == ["Int"]
    assert types_at(checked, ast.EBinary, 1) == ["Int"]


def test_a_type_is_read_back_after_solving_not_at_generation():
    # `x` is a bare variable when its type is recorded; only unification with
    # `f`'s parameter decides it. If the table held a snapshot rather than the
    # variable itself, this would come back as a variable.
    checked, _ = typed(
        "fun f(s : String) -> String = s\nfun g() { let x = \"hi\"\n f(x) }"
    )
    assert types_at(checked, ast.EVar, 3) == ["String", "fun(String) -> String"]


def test_a_lambda_body_knows_the_type_its_call_site_gave_it():
    checked, _ = typed(
        "fun apply(f : fun(Int) -> Int, n : Int) -> Int = f(n)\n"
        "fun main() { print(apply(fun(x) = x * 2, 3)) }"
    )
    assert "Int" in types_at(checked, ast.EBinary, 2)


# -- the first hazard: a numeric literal is a decision, not a type ------------


def test_no_recorded_type_is_still_an_undecided_literal_set():
    # `1` is a `TSet` of every numeric type until defaulting settles it at
    # generalization (delta 32). A Core term annotated with a set is not a
    # typed term, so this must be empty.
    checked, table = typed("fun main() { let n = 1\n let f = 1.5\n print(n) }")
    assert table.unresolved() == []
    # `f` is never used, so defaulting is the only thing that decides it.
    assert types_at(checked, ast.ELit, 2) == ["Float"]
    # And in a context that fixes it, the literal is the fixed type outright.
    checked, _ = typed("fun f(n : Int) -> Int = n + 1\nfun main() { print(f(2)) }")
    assert types_at(checked, ast.ELit, 1) == ["Int"]


def test_a_generalized_binding_records_its_own_bound_variable():
    """`let n = 1` is non-expansive, so it generalizes: `n` is polymorphic over
    the numeric types, and the literal's type is the variable the scheme binds.

    Not a gap. It is what a typed Core says out loud -- the definition becomes
    a type abstraction, and inside one, the bound variable *is* the type. The
    `Int` appears where it is decided, at the use site, which the second
    assertion checks. Recording anything else here would be recording a lie.
    """
    checked, _ = typed("fun main() { let n = 1\n print(n) }")
    assert types_at(checked, ast.ELit, 1) == ["a"]
    assert types_at(checked, ast.EVar, 2) == ["Int", "fun(Int) -> Unit"]


def test_a_literal_pushed_to_a_wider_type_records_the_wider_one():
    checked, _ = typed("fun f(x : Float) -> Float = x\nfun g() = f(2.0)")
    assert types_at(checked, ast.ELit, 2) == ["Float"]


@pytest.mark.parametrize("name", ["adt", "bf", "classes", "dicts", "families",
                                  "hkt", "iter", "monads", "operators",
                                  "question", "question_control", "stack"])
def test_no_golden_program_leaves_a_type_undecided(name):
    """The real check, over real programs rather than fixtures.

    This is the one that would have caught a `TSet` surviving somewhere the
    fixtures above do not reach -- inside a class method's default, say, or a
    lifted loop's generated arithmetic.
    """
    source = PROGRAMS / f"{name}.tl"
    checked = check(source.read_text(), str(source), [PROGRAMS])
    assert checked.types.unresolved() == []
    assert len(checked.types) > 0


# -- the second hazard: a family application must have reduced ---------------


def test_a_family_over_a_known_type_is_reduced_everywhere_not_only_at_the_head():
    """`types.normalize` reduces the head only, and says so. That is right for
    unification, which only ever compares heads. It is not enough for a table
    something reads whole, so `TypeTable.resolve` reduces throughout."""
    source = PROGRAMS / "adt.tl"
    checked = check(source.read_text(), str(source), [PROGRAMS])
    # `adt.tl:23` is `for x in xs`, which elaborates to `iter`/`next`
    # (design.md 6.5). `next` answers `Option (Item (Array Int))` and `iter`
    # answers `Cursor (Array Int)`: both families sit *under* a constructor, so
    # a head-only reduction would leave them both standing.
    shown = types_at(checked, ast.EVar, 23)
    assert "fun(Array Int, ArrayCursor) -> Option Int" in shown
    assert "fun(Array Int) -> ArrayCursor" in shown
    assert not any("Item" in s or "Cursor (" in s for s in shown)


def test_a_family_over_a_signature_variable_survives_and_should():
    """The other half, and the reason this is not an assertion in `resolve`.

    `Elem c` where `c` is bound by the enclosing signature is a type, as much
    as `Int` is: it is rigid, and a type abstraction binds the `c`. What must
    not survive is a family still *waiting* on an instance, which is a
    different thing and is rejected during solving, not here.
    """
    src = (PROGRAMS / "families.tl").read_text()
    checked = check(src, str(PROGRAMS / "families.tl"), [PROGRAMS])
    stuck = [
        show(checked.types.resolve(ty))
        for node, ty in checked.types._exprs.values()
        if contains(checked.types.resolve(ty), TFam)
    ]
    assert stuck, "families.tl should have at least one rigid family"
    # `Field.n` and `Elem.0` belong on this list too: a field access is a class
    # method whose result is an associated family, so a record-polymorphic
    # receiver leaves one behind exactly as `Container.Elem c` does.
    assert all(any(fam in s for fam in ("Elem", "Item", "Field."))
               for s in stuck)


# -- type arguments ----------------------------------------------------------


def uses_of(checked, name: str):
    """Every use of one name in the entry module, under the name it was written.

    Resolution qualifies a name with the module that declared it (`Main#pair`,
    delta 41), so the surface name is what is left after the separator.
    """
    out = []
    for node in entry_nodes(checked):
        if isinstance(node, ast.EVar) and node.use is not None:
            surface = node.name.rpartition(".")[2].rpartition(SEP)[2]
            if surface == name:
                out.append(node.use)
    return out


def test_a_use_records_the_types_it_instantiated_the_scheme_at():
    src = """
fun identity(x) = x
fun main() {
    print(identity(3))
    print(identity("hi"))
}
"""
    checked, table = typed(src)
    shown = sorted(
        ", ".join(show(table.resolve(a)) for a in use.type_args)
        for use in uses_of(checked, "identity")
    )
    # Two uses of one scheme, at two different types. This is exactly the
    # argument list the type application in a Core term needs.
    assert shown == ["Int", "String"]


def test_a_monomorphic_name_instantiates_at_nothing():
    checked, _ = typed("fun f(n : Int) -> Int = n\nfun main() { print(f(1)) }")
    assert [use.type_args for use in uses_of(checked, "f")] == [[]]


def test_type_arguments_are_in_the_schemes_own_order():
    """The order is not a separate agreement to keep -- it is
    `scheme.quantified`, which is what a type abstraction binds in too."""
    # No tuple is printed: there is no `instance Show (a, b)`, and this test is
    # about the argument order rather than about what `print` can take.
    src = """
fun second(a, b) = b
fun main() { print(second(1, "x")) }
"""
    checked, table = typed(src)
    use = uses_of(checked, "second")[0]
    args = [show(table.resolve(a)) for a in use.type_args]
    scheme = next(s for n, s in checked.signatures if n == "second")
    assert len(args) == len(scheme.quantified) == 2
    # `fun(a, b) -> b`: the quantified variables are in the order the
    # parameters introduce them, so the arguments read as the call does.
    assert args == ["Int", "String"]


def test_a_class_method_records_arguments_beside_its_predicates():
    src = """
class Display a {
    fun display(a) -> String
}
instance Display Int {
    fun display(n) = Int.toString(n)
}
fun main() { print(display(3)) }
"""
    checked, table = typed(src)
    use = uses_of(checked, "display")[0]
    assert [show(table.resolve(a)) for a in use.type_args] == ["Int"]
    # The two travel together and mean different things: what evidence this
    # site costs, and what it instantiated the scheme at.
    assert [p.name for p in use.preds] == ["Main#Display"]
