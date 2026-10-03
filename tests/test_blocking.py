"""A task blocked in a read does not hold up the others' collections.

One task reads standard input, which the test keeps open and empty; another
allocates enough to collect many times. A collection stops every worker
first, and a worker inside a C call that waits for input is stopped only if
it said, going in, that it was blocked (`Turkey.Workers.blockingRead`). The
allocating task must finish while the read still waits.
"""

import os
import subprocess

import pytest

from tests import bootc, toolchain
from tests.test_memory_model import function

PROGRAM = """
import System.IO as IO
import System.Task as Task

fun churn(n : Int) -> Int {
    var total = 0
    for var r = 0; r < 100; r = r + 1 {
        let parts : Array String = []
        for var i = 0; i < 50; i = i + 1 { Array.push(parts, Int.toString(n + i)) }
        total = total + String.byteLength(String.join(parts, ","))
    }
    total
}

fun main() {
    let (reader, worker) = Task.runScope(do {
        let reader = Task.spawn(fun() = match IO.readFile("/dev/stdin") {
            Some(text) -> String.byteLength(text)
            None -> -1
        })?
        let worker = Task.spawn(fun() {
            var total = 0
            for var k = 0; k < 40; k = k + 1 { total = total + churn(k) }
            IO.stderr("churned\\n")
            total
        })?
        pure((reader, worker))
    })
    print(Task.join(worker))
    print(Task.join(reader))
}
"""


@pytest.mark.parametrize("workers", ["2", "4"])
def test_a_blocked_read_does_not_hold_up_a_collection(tmp_path, workers):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    source = tmp_path / "main.gob"
    source.write_text(PROGRAM)
    compiled = subprocess.run(toolchain.command(bootc.binary(), *bootc.argv("asm"), str(source)),
                              cwd=bootc.REPO_ROOT, capture_output=True, text=True, check=True)
    assembly = tmp_path / "main.s"
    assembly.write_text(compiled.stdout)
    binary = tmp_path / "blocking"
    subprocess.run([*toolchain.cc(), str(assembly), *toolchain.libraries(), "-o", str(binary)],
                   capture_output=True, text=True, check=True)
    process = subprocess.Popen(toolchain.command(binary), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               env=dict(os.environ, TURKEY_WORKERS=workers))
    try:
        # Only once the allocating task has finished is there input to read.
        assert process.stderr.readline() == "churned\n"
        out, err = process.communicate("hello", timeout=30)
    finally:
        process.kill()
    assert process.returncode == 0, err
    lines = out.splitlines()
    assert lines[1] == "5"


def test_the_blocking_wrappers_are_called_not_inlined(tmp_path):
    """A wrapper records its own frame as where the worker parks: inlined,
    that frame would be its caller's, whose roots a walk from it skips."""
    source = tmp_path / "main.gob"
    source.write_text(PROGRAM)
    text = bootc.boot(*bootc.argv("asm"), str(source))
    body = function(text, "System.IO#readAll")
    assert any("Turkey.Workers#blockingRead" in line and line.startswith("bl ")
               for line in body), body
