"""The typed Core, and the checker that makes its evidence checked.

`tests/test_dicts.py` asks whether the *elaboration* is right by looking at
`Evidence` objects. This file asks the same questions of the datatype those
became, and adds the one a golden can never ask: whether a **wrong** Core term
is rejected. A checker exercised only on correct input is asserted to accept,
which is the half that cannot fail.

The programs here are small on purpose. Every golden in the suite is already
checked by `coretc` on every run -- `driver.check` runs it unconditionally --
so breadth is covered elsewhere, and what is left for this file is the shapes
that are hard to see and the failures that must happen.
"""

from __future__ import annotations

import pytest

from turkey import core, coretc
from turkey.core import CApp, CBind, CField, CLam, CTyApp, CVar
from turkey.driver import check
from turkey.types import TCon, show_scheme

ORD = """
class Egal a {
    fun egal(a, a) -> Bool
}

class Rank a : Egal a {
    fun below(a, a) -> Bool
}

instance Egal Int {
    fun egal(x, y) = x == y
}

instance Rank Int {
    fun below(x, y) = x < y
}

-- Uses both: `below` is `Rank`'s own method and `egal` is its superclass's,
-- so this is the function whose Core shows a superclass being *selected*
-- rather than a second dictionary being passed.
fun atLeast(x, y) = if below(x, y) { y } else { if egal(x, y) { x } else { y } }
fun same(x, y) = egal(x, y)
"""


def lowered(src: str):
    checked = check(src)
    return checked, checked.core


def named(program: core.CProgram, name: str) -> CBind:
    for bind in program.dicts + program.binds:
        if bind.name.endswith(name):
            return bind
    raise AssertionError(f"no binding named '{name}'")


def rendered(program: core.CProgram, name: str) -> str:
    return core.show_bind(named(program, name))


def walk(e):
    """Every node under one, so a test can ask what a term contains."""
    if e is None:
        return
    yield e
    for attr in ("fn", "body", "value", "target", "index", "cond", "then",
                 "otherwise", "scrutinee", "seq", "iter_fn", "next_fn",
                 "init", "step", "rest"):
        yield from walk(getattr(e, attr, None))
    for attr in ("args", "elems", "params"):
        for item in getattr(e, attr, []) or []:
            if isinstance(item, core.CExpr):
                yield from walk(item)
    for _, value in getattr(e, "fields", []) or []:
        yield from walk(value)
    for alt in getattr(e, "alts", []) or []:
        yield from walk(alt.body)
    for bind in getattr(e, "binds", []) or []:
        yield from walk(bind.value)


# -- what the elaboration turned into ----------------------------------------


def test_a_class_becomes_a_record_type_and_an_instance_a_binding():
    _, program = lowered(ORD + "fun main() { print(same(1, 2)) }")
    text = rendered(program, "%inst.Egal.Int")
    assert text.startswith("%inst.Egal.Int : %Dict.Egal Int =")
    assert "%Dict.Egal {" in text


def test_a_superclass_is_a_field_and_not_a_second_dictionary():
    """The question `tests/test_dicts.py` exists to ask, asked of a term.

    `noBigger` has a `Rank` context and calls `egal`, which needs `Egal`. It
    must reach it by projecting the superclass out of the `Rank` dictionary it
    already has -- one parameter, not two -- and the projection is an ordinary
    field access now rather than a `FromDict` path nobody checked.
    """
    _, program = lowered(ORD + "fun main() { print(atLeast(1, 2)) }")
    bind = named(program, "Main#atLeast")
    lam = bind.value
    assert isinstance(lam, CLam)
    assert len(lam.params) == 1, "one dictionary, not one per predicate"
    assert lam.params[0].name.endswith(".Rank")
    fields = [n.name for n in walk(lam) if isinstance(n, CField)]
    assert "%super.Egal" in fields


def test_an_instance_with_a_context_is_a_function_from_dictionaries():
    src = """
class Display a { fun display(a) -> String }
instance Display Int { fun display(n) = Int.toString(n) }
instance [Display a] Display (Array a) {
    fun display(xs) = "?"
}
fun main() { print(display([1, 2])) }
"""
    _, program = lowered(src)
    bind = named(program, "%inst.Display.Array")
    assert isinstance(bind.value, CLam), "a context makes the instance a function"
    assert len(bind.binders) == 1, "and it is polymorphic in the element"
    # And the use site applies it: at the type first, then the dictionary.
    text = rendered(program, "Main#main")
    assert "%inst.Display.Array[Int](%inst.Display.Int)" in text


def test_a_use_of_a_polymorphic_name_is_a_type_application():
    _, program = lowered("fun twice(x) = [x, x]\nfun main() { print(twice(1)) }")
    assert "Main#twice[Int]" in rendered(program, "Main#main")


def test_a_recursive_call_is_type_applied_at_its_own_binders():
    """Inference binds a name monomorphically inside its own definition, so
    nothing is recorded at the recursive occurrence. System-F still wants the
    application, at the binding's own variables."""
    src = """
fun length(xs : Array a, i : Int) -> Int =
    if i >= xs.length { 0 } else { 1 + length(xs, i + 1) }
fun main() { print(length([1], 0)) }
"""
    _, program = lowered(src)
    text = rendered(program, "Main#length")
    assert "Main#length[a]" in text


def test_a_var_is_a_reference_cell_and_a_let_is_not():
    src = """
fun main() {
    var n = 0
    let m = 1
    n = n + m
    print(n)
}
"""
    _, program = lowered(src)
    text = rendered(program, "Main#main")
    assert "let n : %Ref Int =\n      ref(0)" in text
    # `let m = 1` is non-expansive, so it generalizes: a numeric
    # literal is polymorphic until a use decides it (delta 48).
    assert "let m : forall a. a =" in text
    assert "n := " in text
    assert "!n" in text


def test_a_parameter_nothing_writes_is_not_a_cell():
    """Parameters are reassignable (delta 35), but one nothing writes is
    indistinguishable from a `let`, and a cell for it would be an indirection
    on every argument of every function for no observable gain."""
    _, program = lowered("fun f(n : Int) -> Int = n + 1\nfun main() { print(f(1)) }")
    assert "%Ref" not in rendered(program, "Main#f")


def test_a_default_method_is_lowered_once_and_shared():
    """A default body is checked once, against the *class* variable, so the
    types recorded in it are about that variable and not about any instance.
    Copying it into each dictionary would put `a` where `Int` belongs."""
    _, program = lowered(ORD + "fun main() { print(same(1, 2)) }")
    names = [b.name for b in program.dicts]
    assert "%default.Eq.ne" in names
    bind = named(program, "%default.Eq.ne")
    assert len(bind.binders) == 1, "polymorphic in the class variable"
    assert isinstance(bind.value, CLam)
    assert bind.value.params[0].ty is not None


# -- that a wrong term is rejected -------------------------------------------


def test_the_checker_rejects_a_dictionary_of_the_wrong_class():
    """The failure the whole milestone exists to make impossible.

    Before this, a dictionary in the wrong position was not a compile error.
    It was a wrong answer, or an `AttributeError` from inside the evaluator,
    at whatever later moment the missing method was reached.
    """
    checked, program = lowered(ORD + "fun main() { print(atLeast(1, 2)) }")
    bind = named(program, "Main#main")
    swapped = False
    for node in walk(bind.value):
        if isinstance(node, CVar) and node.name == "%inst.Rank.Int":
            node.name = "%inst.Egal.Int"
            swapped = True
    assert swapped, "the fixture should pass a Rank dictionary"

    with pytest.raises(coretc.CoreError) as exc:
        coretc.check_program(program, checked.decls, checked.classes,
                             coretc.globals_of(checked.env))
    assert "%Dict.Rank Int" in exc.value.message
    assert "%Dict.Egal Int" in exc.value.message


def test_the_checker_rejects_a_method_a_class_does_not_have():
    checked, program = lowered(ORD + "fun main() { print(same(1, 2)) }")
    bind = named(program, "Main#same")
    renamed = False
    for node in walk(bind.value):
        if isinstance(node, CField) and node.name == "egal":
            node.name = "nosuchmethod"
            renamed = True
    assert renamed

    with pytest.raises(coretc.CoreError) as exc:
        coretc.check_program(program, checked.decls, checked.classes,
                             coretc.globals_of(checked.env))
    assert "nosuchmethod" in exc.value.message


def test_the_checker_rejects_a_superclass_a_class_does_not_have():
    checked, program = lowered(ORD + "fun main() { print(atLeast(1, 2)) }")
    bind = named(program, "Main#atLeast")
    for node in walk(bind.value):
        if isinstance(node, CField) and node.name == "%super.Egal":
            node.name = "%super.Nonesuch"

    with pytest.raises(coretc.CoreError) as exc:
        coretc.check_program(program, checked.decls, checked.classes,
                             coretc.globals_of(checked.env))
    assert "Nonesuch" in exc.value.message


def test_the_checker_rejects_a_dictionary_with_a_method_missing():
    checked, program = lowered(ORD + "fun main() { print(same(1, 2)) }")
    bind = named(program, "%inst.Egal.Int")
    record = bind.value
    assert isinstance(record, core.CRecord)
    record.fields = []

    with pytest.raises(coretc.CoreError) as exc:
        coretc.check_program(program, checked.decls, checked.classes,
                             coretc.globals_of(checked.env))
    assert "has no 'egal'" in exc.value.message


def test_the_checker_rejects_a_type_application_of_the_wrong_arity():
    checked, program = lowered(
        "fun twice(x) = [x, x]\nfun main() { print(twice(1)) }")
    bind = named(program, "Main#main")
    for node in walk(bind.value):
        if isinstance(node, CTyApp):
            node.args = node.args + [TCon("Int")]

    with pytest.raises(coretc.CoreError) as exc:
        coretc.check_program(program, checked.decls, checked.classes,
                             coretc.globals_of(checked.env))
    assert "binders" in exc.value.message


def test_the_checker_rejects_a_pattern_that_does_not_fit_its_scrutinee():
    """What `CAlt` deliberately does not record, the checker derives -- so a
    constructor of the wrong type is a rejected term rather than an agreement
    nobody verified."""
    src = """
type Colour = Red | Green
fun f(o : Option Int) -> Int = match o {\n    Some(x) -> x\n    None -> 0\n}
fun main() { print(f(Some(1))) }
"""
    checked, program = lowered(src)
    bind = named(program, "Main#f")
    patched = False
    for node in walk(bind.value):
        for alt in getattr(node, "alts", []) or []:
            if getattr(alt.pat, "name", "").endswith("None"):
                alt.pat.name = "Main#Red"
                patched = True
    assert patched

    with pytest.raises(coretc.CoreError) as exc:
        coretc.check_program(program, checked.decls, checked.classes,
                             coretc.globals_of(checked.env))
    # Written as the author wrote it: `TurkeyError` strips the module a name
    # was resolved into, and a Core error is one of those too.
    assert "'Red' is a constructor of 'Colour'" in exc.value.message


def test_the_checker_rejects_an_argument_of_the_wrong_type():
    checked, program = lowered(
        'fun f(n : Int) -> Int = n\nfun main() { print(f(1)) }')
    bind = named(program, "Main#main")
    for node in walk(bind.value):
        if isinstance(node, CApp) and isinstance(node.fn, CVar) \
                and node.fn.name.endswith("Main#f"):
            node.args = [core.CLit(TCon("String"), None, "String", "no")]

    with pytest.raises(coretc.CoreError) as exc:
        coretc.check_program(program, checked.decls, checked.classes,
                             coretc.globals_of(checked.env))
    assert "argument 1" in exc.value.message


def test_a_field_of_a_record_polymorphic_target_keeps_its_inferred_type():
    """A `HasField` the solver discharged leaves the target a variable.

    The field's type is then not recoverable from the target, but it is not
    unknown either: inference recorded it on the selection. Handing back a
    fresh variable instead makes every use of the field a type error -- a
    `step` field holding a function stops being callable -- so the checker
    falls back to the type on the node, the way `CIndex` already does.
    """
    src = """
type Auto = Auto {
    tag  : Int,
    step : fun(Char) -> Auto
}

fun choice(x, y) = Auto {
    tag  = x.tag + y.tag,
    step = fun(c) = choice(x.step(c), y.step(c))
}

fun main() { print(choice(Auto(1, fail), Auto(2, fail)).tag) }
fun fail(c : Char) -> Auto = Auto(0, fail)
"""
    scheme = next(s for n, s in check(src).signatures if n == "choice")
    rendered = show_scheme(scheme)
    assert 'HasField "step" a (fun(Char) -> a)' in rendered
    assert rendered.endswith("fun(a, b) -> Auto")
