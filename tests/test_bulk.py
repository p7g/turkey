"""Runs of elements and bytes (`Turkey.Bulk`): the loops `Prim.arrayCopy`,
`Prim.arrayCopyIn`, `Prim.arrayFill`, `Prim.copyBytes` and `Prim.fillBytes`
lower to.

`tests/programs/array_copy.gob` records what `Array.copy` and `Array.fill`
answer. What is here is what a golden cannot show: that the 32-byte loop is
the one emitted, that it polls, so a collection asked for during a large copy
happens inside it, and the checks a library module can reach by misusing the
primitives.
"""

import hashlib
import re
from pathlib import Path

import pytest

from tests import bootc, lang

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"

STRESS = {"TURKEY_GC_STRESS": "1"}
VERIFY = ("--gc-verify",)


@pytest.fixture
def library(request, monkeypatch):
    """A throwaway library module, since only the library may use `Prim` or
    declare a foreign symbol. Answers the entry that imports it."""
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    module = f"Probe_bulk_{digest}"
    path = LIB / f"{module}.gob"
    monkeypatch.setenv("TURKEY_TEST_FOREIGN", module)

    def write(body: str) -> str:
        path.write_text(f"module {module} (run)\n\n" + body, encoding="utf-8")
        return f"import {module} as P\nfun main() {{ P.run() }}\n"

    try:
        yield write
    finally:
        path.unlink(missing_ok=True)


COPY = """
fun main() {
    let source = Array.filled(1000, Option.unwrapOr(Byte.fromInt(7), Byte.minValue()))
    let target = Array.filled(1000, Byte.minValue())
    Array.copy(target, 0, source, 0, 1000)
    print(target[999])
}
"""


def test_a_copy_moves_32_bytes_through_register_pairs_and_polls(tmp_path):
    source = tmp_path / "main.gob"
    source.write_text(COPY)
    text = bootc.boot(*bootc.argv("asm"), str(source))
    loop = re.search(
        r"ldp (x\d+), (x\d+), \[(x\d+), #0\]\n\s+ldp (x\d+), (x\d+), \[\3, #16\]\n"
        r"(?:.*\n){0,2}?\s+stp \1, \2, \[(x\d+), #0\]\n\s+stp \4, \5, \[\6, #16\]\n"
        r"\s+ldr x16, \[x28, #272\]\n\s+cbnz x16, ", text)
    assert loop, "no 32-byte loop with a poll on its back edge"


# Under stress one poll in 257 collects, so a copy long enough to
# reach many of its back edges collects many times inside itself; a copy with
# no poll would collect at most once, after it. The program is the same but
# for the copy's length, and the difference in the collections counted is the
# ones the copy made. The traced copy keeps objects live across collections,
# and `--gc-verify` checks the heap at each.
DURING = """
fun main() {
    let n = LENGTH
    let bytes = Array.filled(n, Byte.minValue())
    let copied = Array.filled(n, Byte.minValue())
    let names = Array.init(64, Int.toString)
    Array.copy(copied, 0, bytes, 0, n)
    Array.copy(names, 1, names, 0, 63)
    print(names[63])
}
"""


def collections(length: int) -> int:
    result = lang.run(DURING.replace("LENGTH", str(length)),
                      env={**STRESS, "TURKEY_GC_STATS": "1"},
                      flags=(*VERIFY, "--gc-stats"))
    assert result.code == 0, result.stderr
    assert result.stdout == "62\n"
    found = re.search(r"\[gc\] collections (\d+)", result.stderr)
    assert found, result.stderr
    return int(found.group(1))


def test_a_collection_asked_for_during_a_large_copy_happens_inside_it():
    assert collections(1 << 20) - collections(0) > 100


@pytest.mark.parametrize("call", [
    "Prim.arrayCopy(xs, 1, xs, 0, 4)",
    "Prim.arrayCopy(xs, 0, xs, -1, 2)",
    "Prim.arrayCopy(xs, 0, xs, 0, -1)",
    "Prim.arrayFill(xs, 3, 2, 9)",
    "Prim.arrayCopyIn(xs, 2, Prim.ptrNull(), 3)",
])
def test_a_run_outside_an_arrays_storage_panics(library, call):
    """The library checks a run first, for its own message; this is the check
    under it, against the arrays themselves."""
    entry = library(f"""
fun run() -> Unit {{
    let xs : Prim.Array Int = Prim.arrayNew(4, 0)
    {call}
    print(Prim.arrayGet(xs, 0))
}}
""")
    result = lang.run(entry)
    assert result.code != 0
    assert "a run of elements outside the array's storage" in result.stderr


def test_raw_memory_holds_no_traced_element(library):
    entry = library("""
fun run() -> Unit {
    let xs : Prim.Array String = Prim.arrayNewUninit(4)
    Prim.arrayCopyIn(xs, 0, Prim.ptrNull(), 0)
}
""")
    # `run` does not lower. Which message says so depends on whether its
    # caller's call to it is checked first, so only the refusal is asserted.
    with pytest.raises(lang.CompileError):
        lang.run(entry)


# `Turkey.Memory.copy` is `memmove` and `fill` sets bytes, over raw memory:
# every length to 64 and every shift that crosses a word or the stride, both
# ways, compared with a copy made one byte at a time.
RAW = """
import Turkey.Memory as Memory
import Turkey.Ptr as Ptr

fun byte(n : Int) -> Byte = Option.unwrapOr(Byte.fromInt(n % 251), Byte.minValue())

fun reset(p : Ptr.Ptr) -> Unit {
    for var i = 0; i < 160; i = i + 1 { Ptr.store(p, i, byte(i)) }
}

fun main() {
    let p = Ptr.alloc(160)
    var agreed = 0
    var runs = 0
    for var n = 0; n <= 64; n = n + 1 {
        for shift in [0, 1, 7, 8, 31, 32, 33] {
            for up in [True, False] {
                reset(p)
                let (to, from) = if up { (shift, 0) } else { (0, shift) }
                Memory.copy(Ptr.raw(Ptr.add(p, to)), Ptr.raw(Ptr.add(p, from)), n)
                var ok = True
                for var i = 0; i < 160; i = i + 1 {
                    let want = if i >= to && i < to + n { byte(i - to + from) }
                               else { byte(i) }
                    let got : Byte = Ptr.load(p, i)
                    if got != want { ok = False }
                }
                if ok { agreed = agreed + 1 }
                runs = runs + 1
            }
        }
        reset(p)
        Memory.fill(Ptr.raw(Ptr.add(p, 3)), n, 0x1A5)
        var ok = True
        for var i = 0; i < 160; i = i + 1 {
            let want = if i >= 3 && i < 3 + n { byte(0xA5) } else { byte(i) }
            let got : Byte = Ptr.load(p, i)
            if got != want { ok = False }
        }
        if ok { agreed = agreed + 1 }
        runs = runs + 1
    }
    Ptr.free(p)
    print(Int.toString(agreed) + " of " + Int.toString(runs))
}
"""


def test_raw_copies_overlap_as_memmove_and_fills_set_the_low_byte():
    assert lang.output(RAW) == "975 of 975\n"
