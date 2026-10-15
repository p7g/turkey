"""`Prim.keepAlive(x)`: a use of `x` that emits nothing.

Its whole effect is on which values the collector is told about. An object
whose address was handed to C is dead to the compiler after its last use in
Turkey code, so a collection during the C call -- a worker blocked in `read`
lets one run -- would free the storage under it. A `keepAlive` after the call
makes the object live across it, and so a root there. These check that, and
that nothing is emitted for the use itself.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from tests import bootc
from tests.lang import LIB, output, program

# The bytes are a library array whose storage `strlen` reads by address.
# `Prim.objectAddress` gives the header's address; the elements start 24 bytes
# in.
PROBE = """\
module {name} (lengthOf)

import Turkey.Libc as Libc

fun lengthOf(n : Int) -> Int {{
    let bytes : Prim.Array Byte = Prim.arrayNew(n + 1, Prim.byteFromInt(65))
    Prim.arraySet(bytes, n, Prim.byteFromInt(0))
    {early}
    let k = Libc.strlen(Prim.ptrAdd(Prim.objectAddress(bytes), 24))
    {keep}
    k
}}
"""


@pytest.fixture
def probe(request):
    """A library module, since only the library may spell `Prim`, written
    with or without the `keepAlive`, and the program that calls it."""
    made = []

    def write(keep: bool, early: bool = False):
        digest = hashlib.sha1(
            (request.node.name + str(keep) + str(early)).encode()).hexdigest()[:10]
        name = f"Probe_keep_alive_{digest}"
        path = LIB / f"{name}.gob"
        use = "Prim.keepAlive(bytes)" if keep else ""
        path.write_text(PROBE.format(
            name=name, early=use if early else "", keep="" if early else use),
            encoding="utf-8")
        made.append(path)
        return program(f"import {name} as P\n"
                       "fun main() { print(P.lengthOf(5)) }\n")

    try:
        yield write
    finally:
        for path in made:
            path.unlink(missing_ok=True)


def dump(entry, stage: str) -> str:
    return bootc.boot(*bootc.argv(stage), str(entry))


def lines_of(text: str, function: str) -> list[str]:
    """The lines of `function`'s body in a dump. `lengthOf` is small enough
    to be inlined, so the tests read `main`."""
    out, inside = [], False
    for line in text.splitlines():
        if not line.startswith((" ", "\t")):
            inside = function in line
            continue
        if inside:
            out.append(line.strip())
    return out


def test_it_runs(probe):
    assert output(probe(True)) == "5\n"


def test_it_is_one_instruction_in_the_low_ir(probe):
    body = lines_of(dump(probe(True), "ssa"), "@Main#main(")
    assert sum("keep.alive" in line for line in body) == 1


def before_the_call(body: list[str]) -> list[str]:
    """`main`'s instructions up to its call to `strlen`."""
    for k, line in enumerate(body):
        if "strlen" in line and line.startswith("bl "):
            return body[:k]
    raise AssertionError("no call to strlen\n" + "\n".join(body))


def test_the_array_is_a_root_across_the_call_only_when_kept(probe):
    """Without the `keepAlive` nothing after `strlen` reads the array, so
    nothing roots it there; with it, the array is stored to a root slot
    before the call."""
    kept = before_the_call(lines_of(dump(probe(True), "select"), "@Main#main("))
    dropped = before_the_call(lines_of(dump(probe(False), "select"), "@Main#main("))
    assert any("[root" in line for line in kept)
    assert not any("[root" in line for line in dropped)


def test_nothing_is_emitted_for_it(probe):
    """Not even its unit result: the low IR's `keep.alive` defines a value,
    and no machine instruction mentions it."""
    entry = probe(True)
    low = lines_of(dump(entry, "ssa"), "@Main#main(")
    [kept] = [line.split(" = ")[0] for line in low if "keep.alive" in line]
    machine = lines_of(dump(entry, "select"), "@Main#main(")
    assert not any(re.search(rf"{kept}\b", line) for line in machine)


def assembly_of_main(entry) -> list[str]:
    """`main`'s instructions in the program's assembly, which `lengthOf` is
    inlined into, with the probe module's name taken out."""
    out, inside = [], False
    for line in bootc.boot(*bootc.argv("asm"), str(entry)).splitlines():
        if not line.startswith((" ", "\t")):
            inside = "Main#main" in line and line.rstrip().endswith(":")
            continue
        if inside:
            out.append(re.sub(r"Probe_keep_alive_[0-9a-f]+", "P", line.strip()))
    return out


def test_a_keep_alive_that_extends_nothing_costs_nothing(probe):
    """Placed before the call, where the array is still used by the address
    taken for it, the `keepAlive` keeps nothing alive that was not, and the
    function's assembly is exactly what it is without one."""
    early = probe(True, early=True)
    without = probe(False)
    body = assembly_of_main(without)
    assert body
    assert assembly_of_main(early) == body
