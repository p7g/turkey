"""`System.Sync`: a mutex, atomic integers, and a value computed once, used by
tasks that run at once. Each program's result must be the same at any number
of workers; at one, tasks interleave only where they wait.
"""

import pytest

from tests import lang

COUNTS = ["1", "4", "12"]

# Every worker adds to a total under a mutex, yielding while it holds it so
# that the others wait for it; and to an atomic counter with no mutex.
COUNTERS = """
import System.Parallel as Parallel
import System.Sync as Sync
import System.Task as Task

fun main() {
    let total = Sync.mutex(0)
    let log = Sync.mutex([])
    let hits = Sync.atomicInt(0)
    let items = []
    for var i = 0; i < 400; i = i + 1 { Vec.push(items, i) }
    Parallel.each(items, fun(i) {
        Sync.update(total, fun(t) {
            if i % 50 == 0 { Task.yield() }
            t + i
        })
        Sync.withLock(log, fun(entries) { Vec.push(entries, i) })
        let _ = Sync.addAndGet(hits, 1)
    })
    print(Sync.withLock(total, fun(t) = t))
    print(Sync.withLock(log, fun(entries) = len(Vec.sort(entries))))
    print(Sync.load(hits))
    print(Sync.compareAndSet(hits, 400, 7))
    print(Sync.compareAndSet(hits, 400, 8))
    print(Sync.load(hits))
}
"""


@pytest.mark.parametrize("workers", COUNTS)
def test_a_mutex_and_an_atomic_count_every_task(workers):
    result = lang.run(COUNTERS, env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    assert result.stdout == "79800\n400\n400\nTrue\nFalse\n7\n"


# Many tasks force one value; it is computed once, by whichever asks first.
ONCE = """
import System.Parallel as Parallel
import System.Sync as Sync
import System.Task as Task

fun main() {
    let computed = Sync.atomicInt(0)
    let value = Sync.once(fun() {
        let _ = Sync.addAndGet(computed, 1)
        Task.yield()
        "made"
    })
    let items = []
    for var i = 0; i < 200; i = i + 1 { Vec.push(items, i) }
    let seen = Parallel.map(items, fun(_) = Sync.force(value))
    print(len(Array.filter(seen, fun(s) = s == "made")))
    print(Sync.force(value))
    print(Sync.load(computed))
}
"""


@pytest.mark.parametrize("workers", COUNTS)
def test_a_value_is_computed_once_however_many_ask(workers):
    result = lang.run(ONCE, env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    assert result.stdout == "200\nmade\n1\n"


@pytest.mark.parametrize("workers", ["1", "4"])
def test_taking_a_mutex_twice_is_a_deadlock(workers):
    result = lang.run("""
import System.Sync as Sync

fun main() {
    let m = Sync.mutex(1)
    Sync.withLock(m, fun(_) = Sync.withLock(m, fun(x) = print(x)))
}
""", env={"TURKEY_WORKERS": workers})
    assert result.code == 1
    assert result.stderr == "panic: deadlock: every task is waiting\n"


@pytest.mark.parametrize("workers", ["1", "4"])
def test_the_counters_survive_collections(workers):
    result = lang.run(COUNTERS, env={"TURKEY_WORKERS": workers, "TURKEY_GC_STRESS": "97"},
                      flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout == "79800\n400\n400\nTrue\nFalse\n7\n"
