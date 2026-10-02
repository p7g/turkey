"""How wide a value can be: one header word describes a tuple's or a
constructed value's fields, three bits each, and a closure's captures, one bit
each. What fits must work, collector included; what does not is a compile
error rather than a value whose last fields the collector cannot see.
"""

import pytest

from tests import lang


def record(count: int) -> str:
    """A record of `count` fields, the last a string the record alone holds,
    built in a loop that allocates enough to collect under stress."""
    fields = ", ".join(f"f{i} : Int" for i in range(count - 1))
    values = ", ".join(f"f{i} = {i}" for i in range(count - 1))
    return f"""
type Wide = Wide {{ {fields}, last : String }}

fun make(i : Int) -> Wide = Wide {{ {values}, last = "s" + Int.toString(i) }}

fun main() {{
    let kept = []
    for var i = 0; i < 300; i = i + 1 {{ Array.push(kept, make(i)) }}
    var total = 0
    for w in kept {{ total = total + String.byteLength(w.last) + w.f0 }}
    print(total)
    print(kept[299].last)
}}
"""


def test_a_value_of_twenty_one_fields_keeps_its_last_one_through_collections():
    result = lang.run(record(21), env={"TURKEY_GC_STRESS": "7"}, flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout == lang.output(record(21))
    assert result.stdout.endswith("s299\n")


def test_a_constructor_of_twenty_two_fields_is_an_error():
    message = lang.fails(record(22))
    assert "constructor 'Wide' has 22 fields, and a value can have at most 21" in message


@pytest.mark.parametrize("where", ["expression", "type"])
def test_a_tuple_of_twenty_two_elements_is_an_error(where):
    elements = ", ".join(str(i) for i in range(22))
    types = ", ".join("Int" for _ in range(22))
    src = (f"fun main() {{ let t = ({elements})\n print(t.0) }}" if where == "expression"
           else f"fun first(t : ({types})) -> Int = t.0\nfun main() {{ print(1) }}")
    assert "has 22 fields, and a value can have at most 21" in lang.fails(src)


def test_a_closure_of_sixty_five_captures_does_not_compile():
    # One bit a capture in the header, and one in the mask of captures written
    # after the closure is made: 63 is the most both can say.
    names = [f"v{i}" for i in range(65)]
    lets = "\n".join(f"    let {n} = Int.toString({i})" for i, n in enumerate(names))
    src = f"""
fun main() {{
{lets}
    let f = fun() = {" + ".join(names)}
    print(f())
}}
"""
    with pytest.raises(lang.CompileError) as error:
        lang.run(src)
    assert "a lambda with more than 63 captures" in str(error.value)
