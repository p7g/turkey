"""The runtime's lock (`Turkey.Lock`): mutual exclusion, and no lost wake-up.

A program cannot import the runtime's modules, so the first test adds a
module of its own to a copy of the library, which the compiler reads through
`TURKEY_LIB`, and has the program call that: several workers take one lock a
great many times, and every increment made under it must count.

A lost wake-up -- a thread left asleep on a free lock -- needs a narrow
interleaving that hammering one lock rarely produces. Collecting at every
allocation with several workers does: workers stopping and starting skew who
runs when, around the heap's lock. A lock whose release could lose a wake-up
hung that program about one run in three; it is run several times here, each
with a timeout.
"""

import os
import shutil
import subprocess

import pytest

from tests import bootc, toolchain

PROBE = """module System.LockProbe (run)

import System.Parallel as Parallel
import Turkey.Libc as Libc
import Turkey.Lock as Lock

var count = 0

-- Each worker takes the lock `rounds` times, adding one under it, and every
-- so often yields its core while holding it, so that others give up spinning
-- and sleep.
fun run(rounds : Int) -> Int {
    let lock = Lock.new()
    let workers = []
    for var w = 0; w < Parallel.workers(); w = w + 1 { Vec.push(workers, w) }
    Parallel.each(workers, fun(_) {
        for var i = 0; i < rounds; i = i + 1 {
            Lock.acquire(lock)
            count = count + 1
            if i % 64 == 0 { let _ = Libc.schedYield() }
            Lock.release(lock)
        }
    })
    count
}
"""

MAIN = """import System.LockProbe as Probe

fun main() { print(Probe.run(200000)) }
"""


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    root = tmp_path_factory.mktemp("lock")
    library = root / "lib"
    shutil.copytree(bootc.REPO_ROOT / "lib", library)
    (library / "System" / "LockProbe.gob").write_text(PROBE)
    source = root / "main.gob"
    source.write_text(MAIN)
    compiled = subprocess.run(toolchain.command(bootc.binary(), *bootc.argv("asm"), str(source)),
                              cwd=bootc.REPO_ROOT, capture_output=True, text=True, check=True,
                              env=dict(os.environ, TURKEY_LIB=str(library)))
    assembly = root / "main.s"
    assembly.write_text(compiled.stdout)
    binary = root / "probe"
    subprocess.run([*toolchain.cc(), str(assembly), *toolchain.libraries(), "-o", str(binary)],
                   capture_output=True, text=True, check=True)
    return binary


@pytest.mark.parametrize("workers", [1, 2, 4, 12])
def test_every_increment_under_the_lock_counts(probe, workers):
    result = subprocess.run(toolchain.command(probe), capture_output=True, text=True,
                            timeout=60, env=dict(os.environ, TURKEY_WORKERS=str(workers)))
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{200000 * workers}\n"


# Tasks that allocate strings and maps, collected at every allocation: the
# allocator takes the heap's lock for each region, from every worker.
STRESSED = """
import System.Task as Task

fun work(n : Int) -> Int {
    var total = 0
    for var round = 0; round < 10; round = round + 1 {
        let parts : Vec String = []
        for var i = 0; i < 40; i = i + 1 { Vec.push(parts, Int.toString(n * 1000 + i)) }
        total = total + String.byteLength(String.join(parts, ","))
        let m = Map.new()
        for var i = 0; i < 30; i = i + 1 { Map.put(m, i, Int.toString(i + n)) }
        total = total + String.byteLength(Map.getOr(m, 7, ""))
    }
    total
}

fun main() {
    let tasks = Task.runScope(do {
        let made : Vec (Task.Task Int) = []
        for var n = 0; n < 16; n = n + 1 {
            let k = n
            Vec.push(made, Task.spawn(fun() = work(k))?)
        }
        pure(made)
    })
    var sum = 0
    for t in tasks { sum = sum + Task.join(t) }
    print(sum)
}
"""


def test_workers_contending_for_the_heap_lock_under_stress_all_finish(tmp_path):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    source = tmp_path / "main.gob"
    source.write_text(STRESSED)
    compiled = subprocess.run(toolchain.command(bootc.binary(), *bootc.argv("asm"), str(source)),
                              cwd=bootc.REPO_ROOT, capture_output=True, text=True, check=True)
    assembly = tmp_path / "main.s"
    assembly.write_text(compiled.stdout)
    binary = tmp_path / "stressed"
    subprocess.run([*toolchain.cc(), str(assembly), *toolchain.libraries(), "-o", str(binary)],
                   capture_output=True, text=True, check=True)
    env = dict(os.environ, TURKEY_WORKERS="4", TURKEY_GC_STRESS="1", TURKEY_GC_GENERATIONAL="0")
    for _ in range(5):
        result = subprocess.run(toolchain.command(binary), capture_output=True, text=True,
                                timeout=60, env=env)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "33630\n"
