"""Minor collections: an old object given a young one keeps it alive only if
the write barrier remembered the old one.

Each program runs with `TURKEY_GC_GENERATIONAL=1 TURKEY_GC_STRESS=1
TURKEY_GC_VERIFY=1`: a collection at every allocation, seven minor ones in
every eight, and the heap verified around each. A store the barrier missed
shows up as the verifier's "old object holds a young one and is not
remembered", or, if the verifier missed it too, as a young object freed while
reachable and output that differs from the plain run.
"""

import os
import shutil
import subprocess

import pytest

from tests import bootc, lang, toolchain
from tests.test_escape import ESCAPES, READERS

GENERATIONAL = {"TURKEY_GC_GENERATIONAL": "1", "TURKEY_GC_STRESS": "1",
                "TURKEY_GC_VERIFY": "1"}


def _agrees(src: str) -> None:
    plain = lang.output(src)
    result = lang.run(src, env=GENERATIONAL)
    assert result.code == 0, result.stderr
    assert result.stdout == plain


# Containers that are old by the time most of their elements are stored:
# pushes into a growing array, element stores into one, a record field, a
# cell, and a map's table. Every stored string is built in the iteration that
# stores it, so it is young whenever its container is old.
CONTAINERS = """
type Latest = Latest { text : String, count : Int }

var latest = Latest { text = "", count = 0 }

fun main() {
    let pushed : Array String = []
    let replaced = Array.new(0)
    for var i = 0; i < 50; i = i + 1 { Array.push(replaced, "") }
    var last = ""
    let names = Map.new()
    for var i = 0; i < 400; i = i + 1 {
        let s = Int.toString(i) + "!"
        Array.push(pushed, s)
        replaced[i % 50] = Int.toString(i * 3)
        latest.text = s
        latest.count = latest.count + 1
        last = s + "?"
        Map.put(names, i % 37, s)
    }
    var total = 0
    for s in pushed { total = total + String.byteLength(s) }
    for s in replaced { total = total + String.byteLength(s) }
    print(total)
    print(latest.text + " " + Int.toString(latest.count) + " " + last)
    print(Map.getOr(names, 5, "none"))
}
"""


def test_young_objects_stored_into_old_containers_survive():
    _agrees(CONTAINERS)


def test_frame_objects_and_closures_under_minor_collections():
    """A callee storing into its caller's frame object through a parameter
    reads that object's zeroed header as young, and a recursive closure's
    capture of itself goes through the barrier."""
    _agrees(READERS)
    _agrees(ESCAPES)


def test_a_barrier_that_remembers_nothing_is_caught(tmp_path):
    """With the slow path emptied, the barrier still runs and remembers
    nothing: the verifier has to stop the program before a minor collection
    frees a reachable young object."""
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    library = tmp_path / "lib"
    shutil.copytree(bootc.REPO_ROOT / "lib", library)
    heap = library / "Turkey" / "Heap.gob"
    text = heap.read_text()
    before = "    if Prim.loadI64(header, headerGeneration) != old { return {} }\n"
    assert text.count(before) == 1
    heap.write_text(text.replace(before, "    return {}\n"))
    source = tmp_path / "main.gob"
    source.write_text(CONTAINERS)
    result = subprocess.run(toolchain.command(bootc.binary(), "native", str(source)),
                            cwd=bootc.REPO_ROOT, capture_output=True, text=True,
                            check=True, env=dict(os.environ, TURKEY_LIB=str(library)))
    generated = tmp_path / "main.s"
    generated.write_text(result.stdout)
    binary = tmp_path / "broken"
    subprocess.run([*toolchain.cc(), str(generated), *toolchain.libraries(),
                    "-o", str(binary)], capture_output=True, text=True, check=True)
    result = subprocess.run(toolchain.command(binary), capture_output=True, text=True,
                            timeout=60, env=dict(os.environ, **GENERATIONAL))
    assert result.returncode == 1, result.stderr
    assert ("heap verifier: old object holds a young one and is not remembered"
            in result.stderr)
