"""Objects of many fields: a record, a tuple and a closure's environment of any
width keep every field through collections, including fields written after
the object is built and fields past the reach of an immediate offset. An
object with more pointers than its header counts is a compile error.
"""

import pytest

from tests import lang

STRESS = {"TURKEY_GC_STRESS": "7"}


def wide_record(count: int) -> str:
    """A record of `count` fields, every third a string only the record holds,
    the rest `Int`s, with a `var` field near the end that is written after the
    record is built. Built in a loop that allocates enough to collect under
    stress."""
    def kind(i: int) -> str:
        return "String" if i % 3 == 0 else "Int"

    def value(i: int) -> str:
        return f'"s" + Int.toString(i + {i})' if i % 3 == 0 else f"i * {i}"

    marked = count - 20
    fields = ", ".join(("var " if i == marked else "") + f"f{i} : {kind(i)}"
                       for i in range(count))
    values = ", ".join(f"f{i} = {value(i)}" for i in range(count))
    total = " + ".join(f"String.byteLength(w.f{i})" if i % 3 == 0 else f"w.f{i}"
                       for i in range(count))
    return f"""
type Wide = Wide {{ {fields} }}

fun make(i : Int) -> Wide {{
    let w = Wide {{ {values} }}
    w.f{marked} = w.f{marked} + 1
    w
}}

fun main() {{
    let kept = []
    for var i = 0; i < 300; i = i + 1 {{ Vec.push(kept, make(i)) }}
    var total = 0
    for w in kept {{ total = total + {total} }}
    print(total)
    print(kept[299].f0 + " " + kept[299].f{count - 1 - (count - 1) % 3})
    print(kept[299].f{marked})
}}
"""


def wide_record_output(count: int) -> str:
    """What `wide_record(count)` prints."""
    def field(i: int, k: int) -> int:
        return len(f"s{i + k}") if k % 3 == 0 else i * k

    marked = count - 20
    total = sum(field(i, k) for i in range(300) for k in range(count)) + 300
    last = count - 1 - (count - 1) % 3
    return f"{total}\ns299 s{299 + last}\n{299 * marked + 1}\n"


def test_a_record_of_a_hundred_fields_keeps_them_through_collections():
    result = lang.run(wide_record(100), env=STRESS, flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout == wide_record_output(100)


def test_a_tuple_of_seventy_elements_keeps_them_through_collections():
    elements = ", ".join(f'Int.toString(i + {k})' if k % 2 == 0 else f"i + {k}"
                         for k in range(70))
    src = f"""
fun make(i : Int) = ({elements})

fun main() {{
    let kept = []
    for var i = 0; i < 300; i = i + 1 {{ Vec.push(kept, make(i)) }}
    print(kept[299].68 + " " + Int.toString(kept[299].69))
}}
"""
    result = lang.run(src, env=STRESS, flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout == "367 368\n"


def closure(count: int) -> str:
    """A recursive local function capturing `count` locals, every other a
    string, and itself after them, called from a loop that allocates enough to
    collect under stress."""
    lets = "\n".join(f"    let v{i} = Int.toString({i})" if i % 2 == 0 else f"    let v{i} = {i}"
                     for i in range(count))
    total = " + ".join(f"String.byteLength(v{i})" if i % 2 == 0 else f"v{i}"
                       for i in range(count))
    return f"""
fun main() {{
{lets}
    fun sum(k : Int) -> Int = if k == 0 {{ {total} }} else {{ sum(k - 1) + 1 }}
    let kept = []
    for var i = 0; i < 300; i = i + 1 {{ Vec.push(kept, Int.toString(sum(i % 3))) }}
    print(kept[299] + " " + v{count - 2})
}}
"""


def test_a_closure_of_seventy_captures_keeps_them_through_collections():
    src = closure(70)
    result = lang.run(src, env=STRESS, flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout == "1292 68\n"


def test_fields_past_an_immediate_offset_load_and_store():
    # A word field past 32760 bytes, a four-byte one past 16380 and a byte one
    # past 4095 each need the offset in a register.
    words = 4100
    fields = ", ".join(f"f{i} : Int" for i in range(words))
    values = ", ".join(f"f{i} = {i}" for i in range(words))
    src = f"""
type Huge = Huge {{ {fields}, var c : Char, var b : Bool, s : String }}

fun make(i : Int) -> Huge {{
    let h = Huge {{ {values}, c = 'a', b = False, s = Int.toString(i) }}
    h.c = 'z'
    h.b = i % 2 == 1
    h
}}

fun main() {{
    let kept = []
    for var i = 0; i < 20; i = i + 1 {{ Vec.push(kept, make(i)) }}
    let h = kept[19]
    print(h.f0 + h.f{words - 1})
    print(Char.toString(h.c) + " " + Bool.toString(h.b) + " " + h.s)
}}
"""
    result = lang.run(src, env=STRESS, flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout == "4099\nz True 19\n"


def test_a_closure_capturing_more_pointers_than_a_header_counts_is_an_error():
    # Parameters rather than locals, so the source does not nest 65536 deep.
    count = 65536
    params = ", ".join(f"v{i} : String" for i in range(count))
    src = f"""
fun make({params}) -> fun() -> Array String =
    fun() = [{", ".join(f"v{i}" for i in range(count))}]

fun main() {{
    let s = "x"
    print(len(make({", ".join(["s"] * count)})()))
}}
"""
    with pytest.raises(lang.CompileError) as error:
        lang.run(src)
    assert ("in 'make': a nested function captures 65536 values held by reference, and "
            "a function can capture at most 65535 of them") in str(error.value)


def test_a_tuple_of_more_pointers_than_a_header_counts_is_an_error():
    count = 65536
    src = f"""
fun make(s : String) = ({", ".join(["s"] * count)})

fun main() {{ print(make("x").0) }}
"""
    with pytest.raises(lang.CompileError) as error:
        lang.run(src)
    assert ("a value needs 65536 pointer fields, and a value can have at most 65535"
            in str(error.value))
