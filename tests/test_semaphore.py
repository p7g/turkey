"""`System.Sync`'s semaphore: units handed out in order, deadlines, and the
skip of a waiter whose deadline passed.

Each program runs its body on a task, or on the program's own stack, which
waits for a unit apart from the tasks (`Turkey.Wait.parkApart`) since a task
gives the unit back. A body waits for the others to queue by asking
`Turkey.Semaphore.waiting`, a bounded number of times.
"""

import os
import subprocess

import pytest

from tests import bootc, toolchain


PROGRAM = """
import System.Env as Env
import System.Sync as Sync
import System.Task as Task
import Time.Deadline as Deadline
import Time.Duration as Duration
import Turkey.Semaphore as Units
import Turkey.Wait as Wait

-- Until `ready()`, looking every millisecond for at most ten seconds.
fun waitFor(ready : fun() -> Bool) -> Unit {
    var tries = 0
    while !ready() && tries < 10000 {
        Wait.sleepUntil(Wait.now() + 1000000)
        tries = tries + 1
    }
    if !ready() { error("gave up waiting") }
}

fun queued(s : Sync.Semaphore, n : Int) -> Unit = waitFor(fun() = Units.waiting(s) == n)

-- Three tasks queue, one at a time, behind the unit this stack holds, and
-- take it in the order they came.
fun inOrder() -> Unit {
    let s = Sync.semaphore(1)
    let order = Sync.mutex("")
    let _ = Sync.acquire(s, None)
    Task.runScope(do {
        let names = ["a", "b", "c"]
        for var i = 0; i < len(names); i = i + 1 {
            let name = names[i]
            let _ = Task.spawn(fun() = Sync.withUnit(s, fun() = Sync.update(order, fun(o) = o + name)))?
            queued(s, i + 1)
        }
        Sync.release(s)
        pure({})
    })
    print(Sync.withLock(order, fun(o) = o))
}

-- This stack waits with a deadline behind a unit a task holds: it gives up
-- at the deadline, and takes the unit once the task gives it back.
fun expires() -> Unit {
    let s = Sync.semaphore(1)
    let held = Sync.atomicInt(0)
    let letGo = Sync.atomicInt(0)
    Task.runScope(do {
        let _ = Task.spawn(fun() {
            let _ = Sync.acquire(s, None)
            Sync.store(held, 1)
            waitFor(fun() = Sync.load(letGo) == 1)
            Sync.release(s)
        })?
        waitFor(fun() = Sync.load(held) == 1)
        let start = Wait.now()
        let took = Sync.acquire(s, Some(Deadline.after(Duration.millis(50))))
        let waited = (Wait.now() - start) / 1000000
        print(took)
        print(waited >= 50 && waited < 5000)
        Sync.store(letGo, 1)
        print(Sync.acquire(s, Some(Deadline.after(Duration.seconds(10)))))
        Sync.release(s)
        pure({})
    })
}

-- A task with a deadline queues first, and this stack, without one, second.
-- The task's deadline passes; the unit given back then skips it, and comes
-- here.
fun skips() -> Unit {
    let s = Sync.semaphore(1)
    let held = Sync.atomicInt(0)
    let letGo = Sync.atomicInt(0)
    let gaveUp = Sync.atomicInt(0)
    Task.runScope(do {
        let _ = Task.spawn(fun() {
            let _ = Sync.acquire(s, None)
            Sync.store(held, 1)
            waitFor(fun() = Sync.load(letGo) == 1)
            Sync.release(s)
        })?
        waitFor(fun() = Sync.load(held) == 1)
        let _ = Task.spawn(fun() {
            if !Sync.acquire(s, Some(Deadline.after(Duration.millis(100)))) {
                Sync.store(gaveUp, 1)
                Sync.store(letGo, 1)
            } else {
                print("the first waiter took a unit")
            }
        })?
        queued(s, 1)
        print(Sync.acquire(s, None))
        print(Sync.load(gaveUp))
        Sync.release(s)
        -- Free again, and nobody waits.
        print(Sync.acquire(s, Some(Deadline.after(Duration.seconds(10)))))
        print(Units.waiting(s))
        pure({})
    })
}

fun main() {
    let body = match Env.get("BODY") {
        Some("inOrder") -> inOrder
        Some("expires") -> expires
        _ -> skips
    }
    match Env.get("FROM") {
        Some("task") -> Task.runScope(do {
            let _ = Task.spawn(body)?
            pure({})
        })
        _ -> body()
    }
}
"""


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    directory = tmp_path_factory.mktemp("semaphore")
    entry = directory / "main.gob"
    entry.write_text(PROGRAM)
    output = directory / "semaphore"
    # `Turkey.Semaphore.waiting` and `Turkey.Wait`, which a test program may
    # import only by name.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(bootc.INTERNAL_IMPORTS,
                     os.environ[bootc.INTERNAL_IMPORTS] + ",Turkey.Semaphore,Turkey.Wait")
        bootc.link(entry, output)
    return output


def _run(binary, body, origin, workers):
    done = subprocess.run(toolchain.command(binary), capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, BODY=body, FROM=origin, TURKEY_WORKERS=workers))
    assert done.returncode == 0, done.stderr
    return done.stdout.split("\n")[:-1]


ORIGINS = ["task", "program"]
WORKERS = ["1", "4"]


@pytest.mark.parametrize("workers", WORKERS)
@pytest.mark.parametrize("origin", ORIGINS)
def test_units_go_to_waiters_in_the_order_they_came(binary, origin, workers):
    assert _run(binary, "inOrder", origin, workers) == ["abc"]


@pytest.mark.parametrize("workers", WORKERS)
@pytest.mark.parametrize("origin", ORIGINS)
def test_a_wait_gives_up_at_its_deadline(binary, origin, workers):
    assert _run(binary, "expires", origin, workers) == ["False", "True", "True"]


@pytest.mark.parametrize("workers", WORKERS)
@pytest.mark.parametrize("origin", ORIGINS)
def test_a_waiter_whose_deadline_passed_is_skipped(binary, origin, workers):
    assert _run(binary, "skips", origin, workers) == ["True", "1", "True", "0"]
