"""`src/Turkey/Bounds.gob`: the array accesses whose index is proven within
the array's own length, and which carry no check.

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


def _accesses(ssa: str, name: str) -> list[str]:
    body = re.search(rf"^fun @{re.escape(name)}\(.*?^}}$", ssa, re.M | re.S).group(0)
    return [line.strip() for line in body.splitlines()
            if re.search(r"= array\.(get|set) ", line)]


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
    stores = _accesses(result.stdout, "Main#main")
    assert len(stores) == 3 and all(map(_proven, stores)), stores


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
