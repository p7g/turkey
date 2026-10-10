"""`src/Turkey/Bounds.gob`: the array accesses whose index is proven within
the array's own length, and which carry no check; and the comparisons the same
facts decide, which are folded to constants.

Each test reads the low IR, where a proven access prints `; in bounds`. The
rule the proofs keep is the check's own: only facts about the array being
accessed -- its length, read from its own header -- and comparisons the
program made, never an invariant one object keeps about another.
"""

import hashlib
import re
from pathlib import Path

import pytest

from tests import bootc, lang

LIB = Path(__file__).resolve().parent.parent / "lib"


def _body(ssa: str, name: str) -> str:
    return re.search(rf"^fun @{re.escape(name)}\(.*?^}}$", ssa, re.M | re.S).group(0)


def _accesses(ssa: str, name: str) -> list[str]:
    return [line.strip() for line in _body(ssa, name).splitlines()
            if re.search(r"= array\.(get|set) ", line)]


def _comparisons(ssa: str, name: str) -> list[str]:
    return [line.strip() for line in _body(ssa, name).splitlines()
            if re.search(r"= (eq|ne|lt|le|gt|ge) ", line)]


def _proven(line: str) -> bool:
    return line.endswith("; in bounds")


@pytest.fixture
def library_ssa(request, tmp_path):
    """The low IR of a library module, which may use `Prim`."""
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    module = f"Probe_bounds_{digest}"
    path = LIB / f"{module}.gob"

    def build(body: str) -> tuple[str, str]:
        path.write_text(f"module {module} (run)\n\n" + body, encoding="utf-8")
        entry = tmp_path / "main.gob"
        entry.write_text(f"import {module} as P\nfun main() {{ P.run() }}\n",
                         encoding="utf-8")
        return bootc.boot(*bootc.argv("ssa"), str(entry)), module

    try:
        yield build
    finally:
        path.unlink(missing_ok=True)


# Each function below starts by calling itself on a case that never happens:
# a recursive function is not inlined, so it keeps a body of its own to read.


def test_a_loop_over_the_arrays_own_length_carries_no_check(library_ssa):
    ssa, module = library_ssa("""
fun sum(xs : Prim.Array Int) -> Int {
    if Prim.arrayLength(xs) == 77 { return sum(xs) }
    var total = 0
    for var i = 0; i < Prim.arrayLength(xs); i = i + 1 {
        total = total + Prim.arrayGet(xs, i)
    }
    total
}

fun run() -> Unit { print(sum(Prim.arrayNew(3, 5))) }
""")
    accesses = _accesses(ssa, f"{module}#sum")
    assert accesses and all(map(_proven, accesses)), accesses


def test_a_length_read_once_before_the_loop_is_the_same_length(library_ssa):
    ssa, module = library_ssa("""
fun copy(xs : Prim.Array Int) -> Prim.Array Int {
    if Prim.arrayLength(xs) == 77 { return copy(xs) }
    let n = Prim.arrayLength(xs)
    let out : Prim.Array Int = Prim.arrayNew(n, 0)
    var i = 0
    while i < n {
        Prim.arraySet(out, i, Prim.arrayGet(xs, i) + 1)
        i = i + 1
    }
    out
}

fun run() -> Unit { print(Prim.arrayGet(copy(Prim.arrayNew(3, 5)), 2)) }
""")
    accesses = _accesses(ssa, f"{module}#copy")
    assert len(accesses) == 2 and all(map(_proven, accesses)), accesses


def test_a_check_whose_proof_would_read_another_object_stays(library_ssa):
    """A growable array's logical length is never larger than its storage --
    when one task writes both. Another can see one changed and not the other,
    so a bound from the record's field proves nothing about the storage."""
    ssa, module = library_ssa("""
type Growable = Growable { storage : Prim.Array Int, length : Int }

fun sum(g : Growable) -> Int {
    if g.length == 77 { return sum(g) }
    var total = 0
    for var i = 0; i < g.length; i = i + 1 {
        total = total + Prim.arrayGet(g.storage, i)
    }
    total
}

fun run() -> Unit {
    print(sum(Growable { storage = Prim.arrayNew(4, 5), length = 3 }))
}
""")
    accesses = _accesses(ssa, f"{module}#sum")
    assert accesses and not any(map(_proven, accesses)), accesses


def test_another_arrays_length_proves_nothing(library_ssa):
    ssa, module = library_ssa("""
fun pairs(xs : Prim.Array Int, ys : Prim.Array Int) -> Int {
    if Prim.arrayLength(xs) == 77 { return pairs(xs, ys) }
    var total = 0
    for var i = 0; i < Prim.arrayLength(ys); i = i + 1 {
        total = total + Prim.arrayGet(xs, i)
    }
    total
}

fun run() -> Unit { print(pairs(Prim.arrayNew(3, 5), Prim.arrayNew(2, 1))) }
""")
    accesses = _accesses(ssa, f"{module}#pairs")
    assert accesses and not any(map(_proven, accesses)), accesses


@pytest.mark.parametrize("start, step", [
    # The index starts below zero.
    ("-1", "i + 1"),
    # And here it counts down, past zero, with nothing to stop it there.
    ("Prim.arrayLength(xs) - 1", "i - 1"),
])
def test_an_index_that_may_be_negative_stays_checked(library_ssa, start, step):
    ssa, module = library_ssa(f"""
fun sum(xs : Prim.Array Int) -> Int {{
    if Prim.arrayLength(xs) == 77 {{ return sum(xs) }}
    var total = 0
    for var i = {start}; i < Prim.arrayLength(xs); i = {step} {{
        total = total + Prim.arrayGet(xs, i)
        if total > 100 {{ return total }}
    }}
    total
}}

fun run() -> Unit {{ print(sum(Prim.arrayNew(3, 5))) }}
""")
    accesses = _accesses(ssa, f"{module}#sum")
    assert accesses and not any(map(_proven, accesses)), accesses


def test_an_access_one_past_the_end_stays_checked(library_ssa):
    ssa, module = library_ssa("""
fun last(xs : Prim.Array Int) -> Int {
    if Prim.arrayLength(xs) == 77 { return last(xs) }
    var total = 0
    for var i = 0; i < Prim.arrayLength(xs); i = i + 1 {
        total = total + Prim.arrayGet(xs, i + 1)
    }
    total
}

fun run() -> Unit { print(last(Prim.arrayNew(3, 5))) }
""")
    accesses = _accesses(ssa, f"{module}#last")
    assert accesses and not any(map(_proven, accesses)), accesses


def test_an_array_literal_fills_without_checks():
    result = lang.dump("ssa", """
fun main() {
    let xs = [10, 20, 30, 40]
    print(xs)
}
""")
    assert result.code == 0, result.stderr
    accesses = _accesses(result.stdout, "Main#main")
    stores = [line for line in accesses if "array.set" in line]
    assert len(stores) == 3 and all(map(_proven, accesses)), accesses


def test_a_string_pattern_reads_its_bytes_without_checks():
    """The pattern tests the length first, and each byte read is below it."""
    result = lang.dump("ssa", """
fun keyword(s : String) -> Int = match s {
    "let" -> 1
    "fun" -> 2
    _ -> 0
}

fun main() { print(keyword("fun")) }
""")
    assert result.code == 0, result.stderr
    reads = _accesses(result.stdout, "Main#keyword")
    assert len(reads) == 6 and all(map(_proven, reads)), reads


# Comparisons. Each function below also compares the length with 77 to call
# itself, a comparison nothing decides.


def test_a_comparison_the_facts_decide_is_folded(library_ssa):
    """An access's own test, `i < 0 || i >= length`, is implied by the loop's
    test and the index's floor, so the branch to the error goes with it and
    only the loop's test is left."""
    ssa, module = library_ssa("""
fun sum(xs : Prim.Array Int) -> Int {
    if Prim.arrayLength(xs) == 77 { return sum(xs) }
    var total = 0
    for var i = 0; i < Prim.arrayLength(xs); i = i + 1 {
        if i < 0 || i >= Prim.arrayLength(xs) { error("index out of range") }
        total = total + Prim.arrayGet(xs, i)
    }
    total
}

fun run() -> Unit { print(sum(Prim.arrayNew(3, 5))) }
""")
    name = f"{module}#sum"
    assert len(_comparisons(ssa, name)) == 2, _comparisons(ssa, name)
    assert "index out of range" not in _body(ssa, name)


@pytest.mark.parametrize("test", [
    "i < 1", "i > 0", "i == 0", "i != 0", "i + 1 < Prim.arrayLength(xs)",
])
def test_a_comparison_the_facts_do_not_decide_stays(library_ssa, test):
    """`i` is at least 0 and below the length, and each of these can go
    either way."""
    ssa, module = library_ssa(f"""
fun sum(xs : Prim.Array Int) -> Int {{
    if Prim.arrayLength(xs) == 77 {{ return sum(xs) }}
    var total = 0
    for var i = 0; i < Prim.arrayLength(xs); i = i + 1 {{
        if {test} {{ total = total + 1 }}
    }}
    total
}}

fun run() -> Unit {{ print(sum(Prim.arrayNew(3, 5))) }}
""")
    compares = _comparisons(ssa, f"{module}#sum")
    assert len(compares) == 3, compares


def test_a_branch_on_a_negated_comparison_is_a_fact(library_ssa):
    """`i >= n` is `not (i < n)`, so returning when it holds leaves `i < n`
    behind."""
    ssa, module = library_ssa("""
fun at(xs : Prim.Array Int, i : Int) -> Int {
    if Prim.arrayLength(xs) == 77 { return at(xs, i) }
    if i < 0 { return 0 }
    if i >= Prim.arrayLength(xs) { return 0 }
    Prim.arrayGet(xs, i)
}

fun run() -> Unit { print(at(Prim.arrayNew(3, 5), 2)) }
""")
    accesses = _accesses(ssa, f"{module}#at")
    assert accesses and all(map(_proven, accesses)), accesses


# An `Array` has one length, in its own header, and indexing one tests the
# index against that length for its error message before the primitive access.
# A loop bounded by `len(a)` implies that test, so it is folded away, and the
# same facts prove the access: no message, no check against the storage.


@pytest.mark.parametrize("loop", [
    "for var i = 0; i < len(a); i = i + 1 { total = total + a[i] }",
    "for x in a { total = total + x }",
    "for var i = 0; i < len(a); i = i + 1 { a[i] = i }",
], ids=["read", "iterate", "write"])
def test_a_loop_over_an_array_carries_no_check(loop):
    result = lang.dump("ssa", f"""
fun walk(a : Array Int) -> Int {{
    var total = 0
    {loop}
    total
}}

fun main() {{ print(walk([1, 2, 3])) }}
""")
    assert result.code == 0, result.stderr
    body = _body(result.stdout, "Main#walk")
    accesses = _accesses(result.stdout, "Main#walk")
    assert accesses and all(map(_proven, accesses)), accesses
    assert "outOfBounds" not in body, body
    # Only the loop's own test against the length is left.
    compares = _comparisons(result.stdout, "Main#walk")
    assert len(compares) == 1, compares


@pytest.mark.parametrize("access, message", [
    ("print(a[5])", "array index out of bounds: read at index 5, length 3"),
    ("a[5] = 0", "array index out of bounds: write at index 5, length 3"),
    ("print(a[-1])", "array index out of bounds: read at index -1, length 3"),
], ids=["read", "write", "negative"])
def test_an_index_outside_an_array_panics_with_its_message(access, message):
    result = lang.panics(f"""
fun main() {{
    let a = [1, 2, 3]
    {access}
}}
""")
    assert lang.panic_message(result) == message
