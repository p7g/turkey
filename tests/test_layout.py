"""Layout-keyed sharing (M25).

The pass only has work to do once monomorphization's cap binds, which no
program in `tests/programs` is large enough to do. So the cap is set to zero
here, which leaves *everything* generic and is the same situation `boot`
reaches by being large: a call site that kept its `CTyApp` and a body that is
still abstracted over the type it takes apart.
"""

from __future__ import annotations

import pytest

from turkey import driver, layout, mono
from turkey.types import show_scheme


@pytest.fixture
def capped(monkeypatch):
    monkeypatch.setattr(mono, "MAX_SPECIALIZATIONS", 0)


SOURCE = """
fun main() -> Unit {
    var ints = Array.new(0)
    Array.push(ints, 1)
    var texts = Array.new(0)
    Array.push(texts, "a")
    var floats = Array.new(0)
    Array.push(floats, 1.5)
    print(len(ints) + len(texts) + len(floats))
}
"""


def names(program):
    return [bind.name for bind in program.dicts + program.binds]


def test_a_capped_generic_body_is_copied_once_per_layout(capped):
    checked = driver.check(SOURCE)
    copies = [name for name in names(checked.opt)
              if name.startswith("Data.Array#push@[")]
    # One per layout its call sites reach, and no more: `Int` is `i64`,
    # `Float` is `f64`, and `String` is a pointer like every other heap value.
    assert sorted(copies) == ["Data.Array#push@[f64]", "Data.Array#push@[i64]",
                              "Data.Array#push@[ptr]"]


def test_a_copy_knows_the_layout_its_variable_stands_for(capped):
    checked = driver.check(SOURCE)
    by_name = {bind.name: bind for bind in checked.opt.binds}
    copy = by_name["Data.Array#push@[i64]"]
    # Still polymorphic -- the scheme is the original's, which is what lets
    # every call site type-check unchanged -- and carrying the one extra fact.
    assert copy.binders
    assert list(copy.layouts.values()) == ["i64"]
    assert copy.layouts.keys() == {variable.id for variable in copy.binders}


def test_sharing_is_what_makes_the_layout_check_pass(capped):
    # Without the pass this is exactly `FINDINGS 44`: a generic `#push` with
    # live call sites, and no way to know what width its elements are.
    checked = driver.check(SOURCE)
    mono.check_layouts(checked.opt)
    without = layout._Sharer(checked.mono, checked.decls)
    assert "Data.Array#push" in without.shared


def test_a_bare_type_variable_needs_no_copy(capped):
    # Parametricity: a body that only holds an `a` and passes it on needs no
    # layout for it, and gets none. `Option#map`'s `f : fun(a) -> b` is a
    # pointer whatever `a` is.
    source = """
    fun pick(x : a, y : a, first : Bool) -> a = if first { x } else { y }
    fun main() -> Unit {
        print(pick(1, 2, True))
    }
    """
    checked = driver.check(source)
    assert not [name for name in names(checked.opt) if "pick@[" in name]


def test_a_record_polymorphic_body_compiles(capped):
    """The case that made `HasField` a class.

    `capOf` is record-polymorphic: its receiver is a bare type variable, and
    what licenses `at.cap` is a `HasField` demand its scheme carries. That
    demand used to be *erased*, leaving a body that knew the field's type and
    not its position -- refused by the backend at best, and at worst compiled
    into a read at the boxed width whatever the field was written at.

    The cap is zero here, so nothing is specialized and the generic body is
    what runs. It is handed a dictionary of accessors rather than an offset it
    would have to guess. See FINDINGS 45.
    """
    source = """
    type Box = Box { cap : Int }
    fun capOf(at) = at.cap
    fun main() -> Unit {
        print(capOf(Box { cap = 3 }))
    }
    """
    checked = driver.check(source)
    scheme = dict(checked.signatures)["capOf"]
    assert show_scheme(scheme) == '[HasField "cap" a] fun(a) -> Field.cap a'
    # The accessors are an ordinary instance dictionary, and the call that
    # could not be compiled before is an ordinary call through it.
    assert "%inst.%HasField.cap.Main#Box" in names(checked.core)


def test_a_capped_field_access_reaches_the_right_field(capped, capsys):
    """And it runs. None of this is checkable by inspection alone: the hazard
    was a body that compiled and read the wrong bytes."""
    source = """
    type Box = Box { cap : Int, name : String }
    fun capOf(at) = at.cap
    fun nameOf(at) = at.name
    fun main() -> Unit {
        let b = Box { cap = 7, name = "seven" }
        print(capOf(b))
        print(nameOf(b))
    }
    """
    driver.run(source, backend="python")
    assert capsys.readouterr().out == "7\nseven\n"
