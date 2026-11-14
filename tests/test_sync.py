"""`System.Sync`: a mutex, atomic integers, a value computed once and an
event, used by tasks that run at once. Each program's result must be the same
at any number of workers; at one, tasks interleave only where they wait.
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


# A task that holds a mutex finds it held; once it is released, it is free.
TRY = """
import System.Sync as Sync

fun main() {
    let m = Sync.mutex(5)
    print(Sync.withLock(m, fun(_) = Sync.tryWithLock(m, fun(v) = v + 1)))
    print(Sync.tryWithLock(m, fun(v) = v + 1))
    print(Sync.withLock(m, fun(v) = v))
}
"""


def test_trying_a_held_mutex_does_not_wait():
    result = lang.run(TRY)
    assert result.code == 0, result.stderr
    assert result.stdout == "None\nSome(6)\n5\n"


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
    assert lang.panic_message(result) == "deadlock: every task is waiting"


@pytest.mark.parametrize("workers", ["1", "4"])
def test_the_counters_survive_collections(workers):
    result = lang.run(COUNTERS, env={"TURKEY_WORKERS": workers, "TURKEY_GC_STRESS": "97"},
                      flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout == "79800\n400\n400\nTrue\nFalse\n7\n"


# Tasks wait for an event that a later task sets; it stays set, and lets a
# wait with a deadline already passed through, until it is cleared. Waits
# with deadlines on an event nobody sets give up at them, and the program's
# own stack then waits, unbounded, for a task that sets it.
EVENT = """
import System.Sync as Sync
import System.Task as Task
import Time.Duration as Duration

fun main() {
    let e = Sync.event()
    print(Sync.isEventSet(e))
    print(Sync.waitEvent(e, Duration.millis(20)))
    let woken = Sync.atomicInt(0)
    Task.runScope(do {
        for _ in [1, 2, 3] {
            let _ = Task.spawn(fun() {
                Sync.waitEventUnbounded(e)
                let _ = Sync.addAndGet(woken, 1)
            })?
        }
        let _ = Task.spawn(fun() = Sync.setEvent(e))?
        pure(())
    })
    print(Sync.load(woken))
    print(Sync.waitEvent(e, Duration.nanos(0)))
    Sync.clearEvent(e)
    print(Sync.isEventSet(e))
    let gaveUp = Sync.atomicInt(0)
    Task.runScope(do {
        for _ in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20] {
            let _ = Task.spawn(fun() {
                if !Sync.waitEvent(e, Duration.millis(1)) { let _ = Sync.addAndGet(gaveUp, 1) }
            })?
        }
        pure(())
    })
    print(Sync.load(gaveUp))
    Task.runScope(do {
        let _ = Task.spawn(fun() = Sync.setEvent(e))?
        Sync.waitEventUnbounded(e)
        pure(())
    })
    print(Sync.isEventSet(e))
}
"""


EVENT_OUTPUT = "False\nFalse\n3\nTrue\nFalse\n20\nTrue\n"


@pytest.mark.parametrize("workers", COUNTS)
def test_an_event_wakes_every_waiter_and_stays_set(workers):
    result = lang.run(EVENT, env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    assert result.stdout == EVENT_OUTPUT


# Tasks wait on a condition for jobs that another task pushes one at a time,
# each push followed by a notification, until each takes a 0. A wait with a
# deadline gives up on a condition that never holds, and a deadline already
# passed still runs the function once. Timed waits on a condition nobody
# notifies give up at their deadlines, and the program's own stack then
# waits, unbounded, for a task that pushes a job.
CONDITION = """
import System.Sync as Sync
import System.Task as Task
import Time.Duration as Duration

fun push(jobs : Sync.Mutex (Vec Int), added : Sync.Condition (Vec Int), job : Int) -> Unit {
    Sync.withLock(jobs, Vec.push(_, job))
    Sync.notifyAll(added)
}

fun main() {
    let jobs = Sync.mutex([])
    let added = Sync.condition(jobs)
    print(Sync.waitFor(added, Duration.millis(20), Vec.pop))
    let total = Sync.atomicInt(0)
    Task.runScope(do {
        for _ in [1, 2, 3, 4] {
            let _ = Task.spawn(fun() {
                loop {
                    let job = Sync.waitForUnbounded(added, Vec.pop)
                    if job == 0 {
                        break
                    }
                    let _ = Sync.addAndGet(total, job)
                }
            })?
        }
        let _ = Task.spawn(fun() {
            var job = 1
            while job <= 100 {
                push(jobs, added, job)
                job = job + 1
                Task.yield()
            }
            for _ in [1, 2, 3, 4] {
                push(jobs, added, 0)
            }
        })?
        pure(())
    })
    print(Sync.load(total))
    print(Sync.waitFor(added, Duration.nanos(0), fun(v) = Some(len(v))))
    let gaveUp = Sync.atomicInt(0)
    Task.runScope(do {
        for _ in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20] {
            let _ = Task.spawn(fun() {
                if let None = Sync.waitFor(added, Duration.millis(1), fun(_) = None) {
                    let _ = Sync.addAndGet(gaveUp, 1)
                }
            })?
        }
        pure(())
    })
    print(Sync.load(gaveUp))
    Task.runScope(do {
        let _ = Task.spawn(fun() = push(jobs, added, 7))?
        print(Sync.waitForUnbounded(added, Vec.pop))
        pure(())
    })
}
"""

CONDITION_OUTPUT = "None\n5050\nSome(0)\n20\n7\n"


@pytest.mark.parametrize("workers", COUNTS)
def test_a_condition_wakes_its_waiters_to_look_again(workers):
    result = lang.run(CONDITION, env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    assert result.stdout == CONDITION_OUTPUT


@pytest.mark.parametrize("workers", ["1", "4"])
@pytest.mark.parametrize("program, output", [(EVENT, EVENT_OUTPUT), (CONDITION, CONDITION_OUTPUT)],
                         ids=["event", "condition"])
def test_waits_survive_collections(workers, program, output):
    result = lang.run(program, env={"TURKEY_WORKERS": workers, "TURKEY_GC_STRESS": "97"},
                      flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout == output
