"""Waiting for time, descriptors and children (`Turkey.Wait`, `Turkey.Poll`).

A throwaway library module drives the waiting API from tasks and from the
program's own stack, with `pipe`, `fork` and `kill` declared through
`TURKEY_TEST_FOREIGN` to make something to wait on. Each test runs on one
worker and on four, and the main ones again with a collection at every
allocation and the heap verified around each.
"""

import hashlib

import pytest

from tests import bootc, toolchain
from tests.lang import run

LIB = bootc.REPO_ROOT / "lib"

PROBE = """module PROBE (
    sleepOrder, sleepFreesWorker, sleepOwn, readWake, readTimeout, childStatus,
    childAlready, childTimeout, joinSleeper, sleepBesideCompute,
)

import System.Task as Task
import Turkey.IO as IO
import Turkey.Libc as Libc
import Turkey.Sys as Sys
import Turkey.Wait as Wait

foreign "pipe" fun pipe(fds : Prim.Ptr) -> Int
foreign "fork" fun fork() -> Int
foreign "kill" fun kill(pid : Int, signal : Int) -> Int

let millisecond = 1000000

fun word(p : Prim.Ptr, at : Int) -> Int = Prim.charToInt(Prim.loadI32(p, at))

-- A pipe whose read end does not block, and its two ends.
fun makePipe() -> (Int, Int) {
    let fds = Libc.malloc(8)
    let _ = pipe(fds)
    let rd = word(fds, 0)
    let flags = IO.fcntl(rd, Sys.getStatusFlags)
    let _ = IO.fcntlWith(rd, Sys.setStatusFlags, flags + Sys.nonBlocking())
    (rd, word(fds, 4))
}

fun putByte(fd : Int) -> Unit {
    let b = Libc.malloc(1)
    Prim.storeI8(b, 0, Prim.byteFromInt(120))
    let _ = Libc.write(fd, b, 1)
}

fun takeByte(fd : Int) -> Int {
    let b = Libc.malloc(1)
    let n = Libc.read(fd, b, 1)
    if n == 1 { Prim.byteToInt(Prim.loadI8(b, 0)) } else { n }
}

fun after(ms : Int) -> Int = Wait.now() + ms * millisecond

-- Tasks that sleep to different deadlines, spawned out of order, end in
-- the order of their deadlines.
fun sleepOrder() -> Unit {
    let start = Wait.now()
    let _ = Task.runScope(do {
        for ms in [150, 50, 250, 100, 200] {
            let _ = Task.spawn(fun() {
                Wait.sleepUntil(start + ms * millisecond)
                print(ms)
            })?
        }
        pure(0)
    })
}

-- A sleeping task leaves its worker to the next: the second task finishes
-- while the first is still asleep, though it was spawned after it.
fun sleepFreesWorker() -> Unit {
    let _ = Task.runScope(do {
        let _ = Task.spawn(fun() {
            Wait.sleepUntil(after(200))
            print("slept")
        })?
        let _ = Task.spawn(fun() {
            var sum = 0
            for var i = 0; i < 100000; i = i + 1 { sum = sum + i }
            print("busy")
            print(sum)
        })?
        pure(0)
    })
}

-- No tasks: the program's own stack waits.
fun sleepOwn() -> Unit {
    let start = Wait.now()
    Wait.sleepUntil(start + 100 * millisecond)
    let ms = (Wait.now() - start) / millisecond
    print(ms >= 100 && ms < 2000)
    -- A deadline that has passed does not wait.
    Wait.sleepUntil(start)
    print(Wait.readable(0, start) == Wait.timedOut)
}

-- A reader that waits for a byte another task writes after a sleep.
fun readWake() -> Unit {
    let (rd, wr) = makePipe()
    let _ = Task.runScope(do {
        let _ = Task.spawn(fun() {
            let began = Wait.now()
            print(Wait.readable(rd, -1))
            print((Wait.now() - began) / millisecond >= 100)
            print(takeByte(rd))
        })?
        let _ = Task.spawn(fun() {
            Wait.sleepUntil(after(100))
            putByte(wr)
        })?
        pure(0)
    })
}

-- A wait that times out, and then a write that nobody waits for; the next
-- wait on the same descriptor sees the byte at once.
fun readTimeout() -> Unit {
    let (rd, wr) = makePipe()
    let _ = Task.runScope(do {
        let _ = Task.spawn(fun() {
            let began = Wait.now()
            let r = Wait.readable(rd, after(50))
            print(r == Wait.timedOut)
            print((Wait.now() - began) / millisecond >= 50)
            Wait.sleepUntil(after(150))
            print(Wait.readable(rd, after(1000)))
            print(takeByte(rd))
        })?
        let _ = Task.spawn(fun() {
            Wait.sleepUntil(after(100))
            putByte(wr)
        })?
        pure(0)
    })
}

-- A child that exits after a while: its status.
fun childStatus() -> Unit {
    let pid = Sys.cInt(fork())
    if pid == 0 {
        let _ = Libc.usleep(100000)
        Libc.exitNow(3)
    }
    let start = Wait.now()
    let _ = Task.runScope(do {
        let _ = Task.spawn(fun() {
            print(Wait.childExit(pid, -1))
            print((Wait.now() - start) / millisecond >= 100)
        })?
        pure(0)
    })
}

-- A child that exited before it was waited for.
fun childAlready() -> Unit {
    let pid = Sys.cInt(fork())
    if pid == 0 { Libc.exitNow(5) }
    let _ = Libc.usleep(300000)
    print(Wait.childExit(pid, -1))
}

-- A wait for a child with a deadline it misses; the child is killed, and
-- the next wait answers the signal.
fun childTimeout() -> Unit {
    let pid = Sys.cInt(fork())
    if pid == 0 {
        let _ = Libc.usleep(5000000)
        Libc.exitNow(0)
    }
    let _ = Task.runScope(do {
        let _ = Task.spawn(fun() {
            print(Wait.childExit(pid, after(50)) == Wait.timedOut)
            let _ = kill(pid, 9)
            print(Wait.childExit(pid, -1))
        })?
        pure(0)
    })
}

-- A task computes for about 600 ms without yielding or waiting, on one
-- worker, while another sleeps 100 ms on the other: the sleeper is woken by
-- a worker that polls, not left until the computation ends.
fun sleepBesideCompute() -> Unit {
    let start = Wait.now()
    let _ = Task.runScope(do {
        let _ = Task.spawn(fun() {
            Wait.sleepUntil(start + 100 * millisecond)
            print("slept")
            print((Wait.now() - start) / millisecond < 400)
        })?
        let _ = Task.spawn(fun() {
            while Wait.now() - start < 600 * millisecond {}
            print("computed")
        })?
        pure(0)
    })
}

-- The program's own stack waits for a task that sleeps, which is not a
-- deadlock.
fun joinSleeper() -> Unit {
    let _ = Task.runScope(do {
        let t = Task.spawn(fun() {
            Wait.sleepUntil(after(100))
            7
        })?
        print(Task.join(t))
        pure(0)
    })
}
"""

@pytest.fixture
def probe(request, monkeypatch):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    module = f"Probe_wait_{digest}"
    path = LIB / f"{module}.gob"
    monkeypatch.setenv("TURKEY_TEST_FOREIGN", module)
    path.write_text(PROBE.replace("PROBE", module), encoding="utf-8")

    def call(body: str, workers: str = "1", stress: bool = False) -> list[str]:
        env = {"TURKEY_WORKERS": workers}
        flags = ()
        if stress:
            env["TURKEY_GC_STRESS"] = "97"
            flags = ("--gc-verify",)
        result = run(f"import {module} as P\nfun main() {{ {body} }}\n", env=env, flags=flags)
        assert result.code == 0, result.stderr
        return result.stdout.split("\n")[:-1]

    try:
        yield call
    finally:
        path.unlink(missing_ok=True)


WORKERS = ["1", "4"]


@pytest.mark.parametrize("workers", WORKERS)
def test_sleeping_tasks_end_in_the_order_of_their_deadlines(probe, workers):
    assert probe("P.sleepOrder()", workers) == ["50", "100", "150", "200", "250"]


@pytest.mark.parametrize("workers", WORKERS)
def test_a_sleeping_task_does_not_hold_its_worker(probe, workers):
    out = probe("P.sleepFreesWorker()", workers)
    if workers == "1":
        assert out == ["busy", "4999950000", "slept"]
    else:
        assert sorted(out) == ["4999950000", "busy", "slept"]


@pytest.mark.parametrize("workers", WORKERS)
def test_the_programs_own_stack_can_sleep(probe, workers):
    assert probe("P.sleepOwn()", workers) == ["True", "True"]


@pytest.mark.parametrize("workers", WORKERS)
def test_a_task_waiting_to_read_wakes_when_another_writes(probe, workers):
    assert probe("P.readWake()", workers) == ["0", "True", "120"]


@pytest.mark.parametrize("workers", WORKERS)
def test_a_read_wait_times_out_and_a_later_write_disturbs_nothing(probe, workers):
    assert probe("P.readTimeout()", workers) == ["True", "True", "0", "120"]


@pytest.mark.parametrize("workers", WORKERS)
def test_a_child_that_exits_is_waited_for_with_its_status(probe, workers):
    assert probe("P.childStatus()", workers) == [str(3 << 8), "True"]


@pytest.mark.parametrize("workers", WORKERS)
def test_a_child_that_exited_before_the_wait_is_still_seen(probe, workers):
    assert probe("P.childAlready()", workers) == [str(5 << 8)]


@pytest.mark.parametrize("workers", WORKERS)
def test_a_child_wait_times_out_and_the_next_gets_the_signal(probe, workers):
    assert probe("P.childTimeout()", workers) == ["True", "9"]


@pytest.mark.parametrize("workers", WORKERS)
def test_a_task_that_sleeps_is_not_a_deadlock(probe, workers):
    assert probe("P.joinSleeper()", workers) == ["7"]


@pytest.mark.parametrize("workers", WORKERS)
def test_waits_survive_a_collection_at_every_allocation(probe, workers):
    assert probe("P.sleepOrder()", workers, stress=True) == ["50", "100", "150", "200", "250"]
    assert probe("P.readWake()", workers, stress=True) == ["0", "True", "120"]
    assert probe("P.readTimeout()", workers, stress=True) == ["True", "True", "0", "120"]
    assert probe("P.childStatus()", workers, stress=True) == [str(3 << 8), "True"]


def test_a_sleeper_is_woken_while_the_other_worker_computes(probe):
    assert probe("P.sleepBesideCompute()", "2") == ["slept", "True", "computed"]
