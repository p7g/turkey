"""What keeps a data race between tasks from breaking memory safety or
reaching into race-free code: every array access is checked against the
array's own length, and every store to a mutable location another task could
see is ordered after the loads before it.

Neither is observable from one worker, so these check the code the compiler
emits and the one panic a library module can reach by misusing a primitive.
"""

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"


def assembly(tmp_path: Path, src: str) -> str:
    entry = tmp_path / "main.gob"
    entry.write_text(src, encoding="utf-8")
    return bootc.boot(*bootc.argv("asm"), str(entry))


def function(asm: str, name: str) -> list[str]:
    lines = asm.splitlines()
    start = lines.index(f'"{toolchain.c_symbol(name)}":')
    body = []
    for line in lines[start + 1:]:
        if line.startswith('"_') or line.startswith('"') and line.endswith('":'):
            break
        body.append(line.strip())
    return body


def test_a_store_to_a_mutable_field_is_ordered_after_earlier_loads(tmp_path):
    asm = assembly(tmp_path, """
type Counter = Counter { var count : Int }

fun bump(c : Counter) -> Unit { c.count = c.count + 1 }

fun main() {
    let c = Counter { count = 0 }
    bump(c)
    print(c.count)
}
""")
    body = function(asm, "Main#bump")
    # Stores through a register other than `sp`: the frame's own saves are
    # not to a location anyone else can see.
    stores = [i for i, line in enumerate(body)
              if line.startswith("str ") and ", [sp" not in line]
    assert stores, body
    assert all(body[i - 1] == "dmb ish" for i in stores), body


def test_initializing_an_object_needs_no_fence(tmp_path):
    asm = assembly(tmp_path, """
type Point = Point { x : Int, y : Int }

fun make(n : Int) -> Point = Point { x = n, y = n + 1 }

fun main() { print(make(3).y) }
""")
    assert "dmb ish" not in function(asm, "Main#make")


@pytest.fixture
def library_asm(request, tmp_path):
    """The assembly of a library module, which may use `Prim`."""
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    module = f"Probe_fences_{digest}"
    path = LIB / f"{module}.gob"

    def build(body: str) -> tuple[str, str]:
        path.write_text(f"module {module} (run)\n\n" + body, encoding="utf-8")
        return assembly(tmp_path, f"import {module} as P\nfun main() {{ P.run() }}\n"), module

    try:
        yield build
    finally:
        path.unlink(missing_ok=True)


def fences(asm: str, name: str) -> int:
    return function(asm, name).count("dmb ish")


# Each function below starts by calling itself on a case that never happens:
# a recursive function is not inlined, so it keeps a body of its own to read.


def test_filling_a_fresh_array_needs_no_fence(library_asm):
    """No other task can see an array until something is handed it, so the
    order of the stores that fill it is unobservable."""
    asm, module = library_asm("""
fun squares(n : Int) -> Prim.Array Int {
    if n < 0 { return squares(0) }
    let out : Prim.Array Int = Prim.arrayNew(n, 0)
    for var i = 0; i < n; i = i + 1 { Prim.arraySet(out, i, i * i) }
    out
}

fun run() -> Unit { print(Prim.arrayGet(squares(4), 3)) }
""")
    assert fences(asm, f"{module}#squares") == 0


def test_a_store_after_the_array_is_handed_on_is_ordered(library_asm):
    asm, module = library_asm("""
fun keep(xs : Prim.Array Int) -> Unit {
    if Prim.arrayLength(xs) == 0 { return keep(xs) }
    print(Prim.arrayGet(xs, 0))
}

fun fill(n : Int) -> Prim.Array Int {
    if n < 0 { return fill(0) }
    let out : Prim.Array Int = Prim.arrayNew(n, 0)
    Prim.arraySet(out, 0, 1)
    keep(out)
    Prim.arraySet(out, 0, 2)
    out
}

fun run() -> Unit { print(Prim.arrayGet(fill(2), 0)) }
""")
    assert fences(asm, f"{module}#fill") == 1


def test_a_store_in_a_loop_that_hands_the_array_on_is_ordered(library_asm):
    """The loop carries the publication back to its own head, so the store
    at the top of the next iteration may be seen."""
    asm, module = library_asm("""
fun keep(xs : Prim.Array Int) -> Unit {
    if Prim.arrayLength(xs) == 0 { return keep(xs) }
    print(Prim.arrayGet(xs, 0))
}

fun fill(n : Int) -> Prim.Array Int {
    if n < 0 { return fill(0) }
    let out : Prim.Array Int = Prim.arrayNew(1, 0)
    for var i = 0; i < n; i = i + 1 {
        Prim.arraySet(out, 0, i)
        keep(out)
    }
    out
}

fun run() -> Unit { print(Prim.arrayGet(fill(2), 0)) }
""")
    assert fences(asm, f"{module}#fill") == 1


@pytest.fixture
def probe(request, tmp_path):
    """A library module, since only the library may use `Prim`."""
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    module = f"Probe_memory_{digest}"
    path = LIB / f"{module}.gob"

    def run(body: str) -> subprocess.CompletedProcess:
        path.write_text(f"module {module} (run)\n\n" + body, encoding="utf-8")
        entry = tmp_path / "main.gob"
        entry.write_text(f"import {module} as P\nfun main() {{ P.run() }}\n",
                         encoding="utf-8")
        asm = tmp_path / "main.s"
        built = subprocess.run(
            toolchain.command(bootc.binary(), "build", "-o", str(asm), str(entry)),
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        binary = tmp_path / "main"
        subprocess.run([*toolchain.cc(), "-o", str(binary), str(asm),
                        *toolchain.libraries()], check=True, capture_output=True)
        return subprocess.run(toolchain.command(binary), capture_output=True,
                              text=True, timeout=60)

    try:
        yield run
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("access", [
    "print(Prim.arrayGet(xs, 2))",
    "Prim.arraySet(xs, -1, 7)",
])
def test_an_access_outside_an_arrays_storage_panics(probe, access):
    """The library checks an index against a growable array's logical length
    first; this is the check under it, against the storage itself, which is
    the one no race between tasks can get past."""
    result = probe(f"""
fun run() -> Unit {{
    let xs : Prim.Array Int = Prim.arrayNew(2, 0)
    {access}
}}
""")
    assert result.returncode == 1
    assert result.stderr == "panic: array access outside the array's storage\n"
