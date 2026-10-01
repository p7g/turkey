"""Coroutines (`Turkey.Coroutine`): a function on a stack of its own, suspended
at any depth of ordinary calls and resumed later.

Only the library may import `Turkey.*`, so, as in `test_giblets`, each test
writes its probe into the shipped `lib/` under a name carrying the test's own
and removes it afterwards, and a program outside imports it.

Every program that should succeed is also run with a collection at every
allocation and the heap verified around each: a root held only in a parked
stack's frames that the collector missed shows up as the verifier's complaint
or as output that differs from the plain run.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib"
STRESS = {"TURKEY_GC_STRESS": "1"}


@pytest.fixture
def coroutines(request, tmp_path):
    """Compile `body` as a library module with `Turkey.Coroutine` imported,
    and a program that calls its `run`; answer a function that runs the
    program, compiled plainly or with `--gc-verify`."""
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    module = f"Probe_coroutine_{digest}"
    probe = LIB / f"{module}.gob"

    def build(body: str, *flags: str) -> Path:
        probe.write_text(f"module {module} (run)\n\n"
                         "import Turkey.Coroutine as Co\n\n" + body,
                         encoding="utf-8")
        entry = tmp_path / "main.gob"
        entry.write_text(f"import {module} as P\nfun main() {{ P.run() }}\n",
                         encoding="utf-8")
        name = "verified" if flags else "plain"
        assembly = tmp_path / f"{name}.s"
        compiled = subprocess.run(
            toolchain.command(bootc.binary(), *bootc.argv("asm"), *flags, str(entry)),
            cwd=REPO_ROOT, capture_output=True, text=True)
        assert compiled.returncode == 0, compiled.stderr
        assembly.write_text(compiled.stdout, encoding="utf-8")
        binary = tmp_path / name
        subprocess.run([*toolchain.cc(), "-o", str(binary), str(assembly),
                        *toolchain.libraries()],
                       check=True, capture_output=True, text=True)
        return binary

    def run(body: str, verified: bool = False,
            env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        binary = build(body, *(("--gc-verify",) if verified else ()))
        return subprocess.run(toolchain.command(binary),
                              env=dict(os.environ, **(env or {})),
                              capture_output=True, text=True, timeout=120)

    try:
        yield run
    finally:
        probe.unlink(missing_ok=True)


def agrees(run, body: str) -> str:
    """The plain run's output, after checking the stressed and verified run
    prints the same."""
    plain = run(body)
    assert plain.returncode == 0, plain.stderr
    stressed = run(body, verified=True, env=STRESS)
    assert stressed.returncode == 0, stressed.stderr
    assert stressed.stdout == plain.stdout
    return plain.stdout


def test_a_coroutine_suspends_deep_inside_ordinary_calls(coroutines):
    """Every frame between the body and the `suspend` keeps its values, and
    what only those frames hold survives the collections the resumer's
    allocations cause while they are parked."""
    out = agrees(coroutines, """
type Box = Box { value : Int, items : Array String }

fun deep(box : Box, n : Int) -> Unit {
    if n == 0 {
        box.items = box.items + ["at the bottom with " + Int.toString(box.value)]
        Co.suspend()
        box.value = box.value + 1
        Co.suspend()
    } else {
        let mine = "frame " + Int.toString(n)
        deep(box, n - 1)
        if n % 4 == 0 { box.items = box.items + [mine] }
    }
}

fun run() {
    let box = Box { value = 41, items = [] }
    let c = Co.new(fun() {
        deep(box, 12)
        box.items = box.items + ["done"]
    })
    var rounds = 0
    while !Co.finished(c) {
        Co.resume(c)
        rounds = rounds + 1
        let made = Array.map([1, 2, 3], fun(x) = Int.toString(x * rounds))
        print("round " + Int.toString(rounds) + ": " + String.join(made, ","))
    }
    print(box.value)
    for s in box.items { print(s) }
}
""")
    assert out == ("round 1: 1,2,3\nround 2: 2,4,6\nround 3: 3,6,9\n42\n"
                   "at the bottom with 41\nframe 4\nframe 8\nframe 12\ndone\n")


def test_each_coroutine_comes_back_to_whoever_resumed_it(coroutines):
    out = agrees(coroutines, """
fun run() {
    let inner = Co.new(fun() {
        print("inner 1")
        Co.suspend()
        print("inner 2")
    })
    let outer = Co.new(fun() {
        print("outer 1")
        Co.resume(inner)
        print("outer 2")
        Co.suspend()
        Co.resume(inner)
        print("outer 3")
    })
    Co.resume(outer)
    print("main 1")
    Co.resume(outer)
    print("main 2")
    print(Co.finished(inner) && Co.finished(outer))
}
""")
    assert out == "outer 1\ninner 1\nouter 2\nmain 1\ninner 2\nouter 3\nmain 2\nTrue\n"


def test_a_finished_coroutine_gives_back_its_stack(coroutines):
    """Each stack reserves over a megabyte of address space, so ten thousand
    that were never unmapped would be ten gigabytes of it."""
    out = agrees(coroutines, """
fun run() {
    var total = 0
    for var i = 0; i < 10000; i = i + 1 {
        let c = Co.new(fun() {
            total = total + i
            Co.suspend()
            total = total + 1
        })
        Co.resume(c)
        Co.resume(c)
    }
    print(total)
}
""")
    assert out == f"{sum(range(10000)) + 10000}\n"


def test_a_panic_in_a_coroutine_ends_the_program(coroutines):
    result = coroutines("""
fun run() {
    let c = Co.new(fun() {
        print("before")
        let xs = [1, 2]
        print(xs[5])
        print("not reached")
    })
    Co.resume(c)
    print("not reached either")
}
""")
    assert result.returncode == 1
    assert result.stdout == "before\n"
    assert result.stderr == \
        "panic: array index out of bounds: read at index 5, length 2\n"


@pytest.mark.parametrize("body,message", [
    ("""
fun run() {
    let c = Co.new(fun() { print("ran") })
    Co.resume(c)
    Co.resume(c)
}
""", "resumed a finished coroutine"),
    # Resuming itself would run its own stack twice.
    ("""
var me : Option Co.Coroutine = None

fun run() {
    let c = Co.new(fun() {
        match me {
            Some(c) -> Co.resume(c)
            None -> {}
        }
    })
    me = Some(c)
    Co.resume(c)
}
""", "resumed a running coroutine"),
    ("""
fun run() { Co.suspend() }
""", "suspended outside a coroutine"),
])
def test_misuse_panics(coroutines, body, message):
    result = coroutines(body)
    assert result.returncode == 1
    assert result.stderr == f"panic: {message}\n"
