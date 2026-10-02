"""The atomic primitives: `Prim.atomicCas`, `Prim.atomicAdd`, `Prim.atomicLoad`
and `Prim.atomicStore`, on an `Int` at a byte offset into raw memory.

One thread cannot tell an atomic access from a plain one, so these check what
each answers and leaves behind, that the instructions are the ordered ones,
and that the encoder's words for them agree with the assembler's.
"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain
from tests.test_encode import _compare

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"

PROGRAM = """
fun run() -> Unit {
    let p = Libc.malloc(16)
    Prim.storeI64(p, 8, 5)
    -- The expected value: stored, and the old one answered.
    print(Prim.atomicCas(p, 8, 5, 9))
    -- Not the expected value: nothing stored, and the value found answered.
    print(Prim.atomicCas(p, 8, 5, 11))
    print(Prim.loadI64(p, 8))
    print(Prim.atomicAdd(p, 8, 3))
    print(Prim.atomicAdd(p, 8, -2))
    Prim.atomicStore(p, 8, 40)
    print(Prim.atomicLoad(p, 8) + 2)
}
"""


@pytest.fixture
def module(request, tmp_path):
    """A library module, since only the library may use `Prim`, and the
    program that runs it."""
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    name = f"Probe_atomics_{digest}"
    path = LIB / f"{name}.gob"
    path.write_text(f"module {name} (run)\n\nimport Turkey.Libc as Libc\n" + PROGRAM,
                    encoding="utf-8")
    entry = tmp_path / "main.gob"
    entry.write_text(f"import {name} as P\nfun main() {{ P.run() }}\n", encoding="utf-8")
    try:
        yield entry
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
def test_each_answers_and_stores_what_it_should(module, tmp_path):
    asm = tmp_path / "main.s"
    built = subprocess.run(
        toolchain.command(bootc.binary(), "build", "-o", str(asm), str(module)),
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    binary = tmp_path / "main"
    subprocess.run([*toolchain.cc(), "-o", str(binary), str(asm),
                    *toolchain.libraries()], check=True, capture_output=True)
    ran = subprocess.run(toolchain.command(binary), capture_output=True, text=True,
                         timeout=60)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.split() == ["5", "9", "9", "9", "12", "42"]


def test_they_are_the_ordered_instructions(module):
    text = bootc.boot(*bootc.argv("asm"), str(module))
    ops = {line.split()[0] for line in text.splitlines() if line.startswith("    ")}
    assert {"casal", "ldaddal", "ldar", "stlr"} <= ops


@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
def test_the_encoder_agrees_with_the_assembler(module):
    listing = bootc.boot(*bootc.argv("listing"), str(module))
    native = bootc.boot(*bootc.argv("asm"), str(module))
    _compare(listing, native)
