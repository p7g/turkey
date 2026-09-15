"""Existential constructors from source (SPEC-DELTAS 68).

Construction elaborates like a constrained function, a pattern opens one at a
rigid constant scoped to its arm or function, and what it hides may not leave.
The layout side -- every payload layout, generic code, GC -- is pinned in
`tests/test_existential_layout.py`; this file is the language.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from turkey import driver, llvmgen
from turkey.core import show_program
from turkey.errors import TurkeyError


def outputs(source: str, capfd) -> str:
    checked = driver.check(source)
    out = io.StringIO()
    with redirect_stdout(out):
        driver.run(source, backend="python")
    python = out.getvalue()
    capfd.readouterr()
    llvmgen.execute(checked.opt, checked.decls, checked.main)
    native = capfd.readouterr().out
    assert python == native
    return python


def fails(source: str) -> str:
    with pytest.raises(TurkeyError) as caught:
        driver.check(source)
    return caught.value.message


SHOWN = """
type Shown = Shown[Show a](Array a)

fun describe(Shown(xs)) -> String = show(xs[0])

fun main() {
    let items = [Shown([1, 2, 3]), Shown(["x", "y"]), Shown([True])]
    for item in items {
        print(describe(item))
    }
}
"""


def test_a_carried_instance_is_used_where_the_value_is_opened(capfd):
    assert outputs(SHOWN, capfd) == "1\nx\nTrue\n"


def test_construction_elaborates_to_the_dictionary_and_the_field():
    core = show_program(driver.check(SHOWN).core, "Main")
    assert "Main#Shown(\n  %inst.Std.Classes#Show.Int," in core
    assert "Main#Shown[a](%d1.Std.Classes#Show)(xs) ->" in core


def test_a_record_form_opens_by_its_field_names(capfd):
    source = """
type Counter = Counter[s] { state : s, step : fun(s) -> s, read : fun(s) -> Int }

fun run(c : Counter) -> Int = match c {
    Counter { state, step, read } -> read(step(step(state)))
}

fun main() {
    print(run(Counter { state = 0, step = fun(n : Int) -> Int = n + 1, read = fun(n : Int) -> Int = n }))
    print(run(Counter { read = String.byteLength, state = "a", step = fun(s : String) -> String = s + "b" }))
}
"""
    assert outputs(source, capfd) == "2\n3\n"


def test_openings_nest_and_stay_distinct(capfd):
    source = """
type Some = Some[Show a](a)

fun both(pair : (Some, Some)) -> String = match pair {
    (Some(x), Some(y)) -> show(x) + " " + show(y)
}

fun main() {
    print(both((Some(1), Some("two"))))
}
"""
    assert outputs(source, capfd) == "1 two\n"


def test_two_openings_are_different_types():
    message = fails("""
type Some = Some[Show a](a)

fun same(pair : (Some, Some)) -> Bool = match pair {
    (Some(x), Some(y)) -> x == y
}

fun main() {}
""")
    assert "a" in message and ("a2" in message or "Eq" in message)


def test_an_existential_constructor_is_a_function_value(capfd):
    source = """
type Some = Some[Show a](a)

fun say(Some(x)) -> String = show(x)

fun main() {
    for s in Array.map([1, 2], Some) {
        print(say(s))
    }
}
"""
    assert outputs(source, capfd) == "1\n2\n"


@pytest.mark.parametrize("source, message", [
    ("type Some = Some[Show a](a)\nfun leak(Some(x)) = x\nfun main() {}\n",
     "cannot escape the pattern 'Some' that opened it"),
    ("type Some = Some[Show a](a)\nfun main() {\n    let Some(x) = Some(1)\n}\n",
     "only a 'match' arm or a function parameter may do"),
    ("type Some = Some[Show a](a) | Other[Show a](a)\n"
     "fun f(v : Some) -> String = match v {\n"
     "    Some(x) | Other(x) -> show(x)\n}\nfun main() {}\n",
     "an arm with alternatives cannot open"),
    ("type Some = Some[Show a](a)\nfun f(v : Some) -> Unit {\n"
     "    var hold = []\n    match v {\n        Some(x) -> Array.push(hold, x)\n"
     "    }\n}\nfun main() {}\n",
     "cannot escape the pattern 'Some' that opened it"),
    ("type Some = Some[a](a)\nfun f(v : Some) -> String = match v {\n"
     "    Some(x) -> show(x)\n}\nfun main() {}\n",
     "no instance for 'Show a'"),
    ("type Some = Some[Show a](a)\nfun main() {\n    for Some(x) in [Some(1)] {\n    }\n}\n",
     "only a 'match' arm or a function parameter may do"),
])
def test_what_an_opening_may_not_do(source, message):
    assert message in fails(source)
