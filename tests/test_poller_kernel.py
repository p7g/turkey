"""The poller's kernel layer (`Turkey.Workers`): kqueue on Darwin, epoll on Linux.

A program cannot reach the runtime, so a throwaway library module drives the
calls directly, with `pipe` and `fork` declared through `TURKEY_TEST_FOREIGN`
to make something to wait on. What is asserted is what the layer promises its
caller: one result with the right token and flags for a ready descriptor,
nothing for a quiet one, a wait that ends at its timeout and not before, a
break that ends a blocked wait, and a child that has exited already being told
apart from one that has not.
"""

import hashlib
import sys
from pathlib import Path

import pytest

from tests import bootc, toolchain
from tests.lang import run

LIB = bootc.REPO_ROOT / "lib"
DARWIN = sys.platform == "darwin"

PROBE = """export (ready, quiet, timeout, wakeBefore, wakeDuring, exitedChild, runningChild)

import System.Task as Task
import Time.Duration as Duration
import Time.Stopwatch as Stopwatch
import Turkey.Heap as Heap
import Turkey.Libc as Libc
import Turkey.Sys as Sys
import Turkey.Workers as Workers

foreign "pipe" fun pipe(fds : Prim.Ptr) -> Int
foreign "fork" fun fork() -> Int

fun word(p : Prim.Ptr, at : Int) -> Int = Prim.charToInt(Prim.loadI32(p, at))

-- A pipe: its read end, and its write end.
fun makePipe() -> (Int, Int) {
    let fds = Libc.malloc(8)
    let _ = pipe(fds)
    (word(fds, 0), word(fds, 4))
}

fun putByte(fd : Int) -> Unit {
    let b = Libc.malloc(1)
    Prim.storeI8(b, 0, Prim.byteFromInt(120))
    let _ = Libc.write(fd, b, 1)
}

fun show(n : Int) -> Unit {
    var i = 0
    while i < n {
        print(Workers.resultToken(i))
        print(Workers.resultValue(i))
        i = i + 1
    }
}

-- A byte written after the registration: one result, the token, readable;
-- and once taken, nothing more.
fun ready() -> Unit {
    let (rd, wr) = makePipe()
    print(Workers.pollRead(rd, 77))
    putByte(wr)
    let _ = Workers.claim()
    let n = Workers.pollNow()
    print(n)
    show(n)
    print(Workers.pollNow())
    Workers.release()
}

-- Nothing written: nothing reported.
fun quiet() -> Unit {
    let (rd, _) = makePipe()
    print(Workers.pollRead(rd, 5))
    let _ = Workers.claim()
    print(Workers.pollNow())
    Workers.release()
}

-- A wait with nothing to report ends after its timeout, in milliseconds.
fun timeout(ns : Int) -> Unit {
    let _ = Workers.claim()
    let seen = Workers.workSeen()
    let start = Stopwatch.start()
    let n = Heap.pause(seen, ns)
    print(n)
    print(Duration.toNanos(Stopwatch.elapsed(start)) / 1000000)
    Workers.release()
}

-- A break before the wait: the wait does not sleep through it.
fun wakeBefore() -> Unit {
    let _ = Workers.claim()
    -- Made, so that there is something to trigger.
    print(Workers.pollNow())
    Workers.wakePoller()
    let seen = Workers.workSeen()
    let start = Stopwatch.start()
    print(Heap.pause(seen, 5000000000))
    print(Duration.toNanos(Stopwatch.elapsed(start)) / 1000000 < 1000)
    Workers.release()
}

-- A break from another worker's task while this one is blocked in the wait.
fun wakeDuring() -> Unit {
    let _ = Workers.claim()
    print(Workers.pollNow())
    let _ = Task.runScope(do {
        let t = Task.spawn(fun() {
            let _ = Libc.usleep(150000)
            Workers.wakePoller()
            let _ = Libc.usleep(1500000)
            0
        })?
        let seen = Workers.workSeen()
        let start = Stopwatch.start()
        let n = Heap.pause(seen, 5000000000)
        let ms = Duration.toNanos(Stopwatch.elapsed(start)) / 1000000
        print(n)
        print(ms >= 100 && ms < 1000)
        pure(t)
    })
    Workers.release()
}

-- Reaps `pid`: its wait status.
fun reap(pid : Int) -> Int {
    let status = Libc.malloc(8)
    Prim.storeI64(status, 0, 0)
    let r = Sys.cInt(Libc.waitpid(pid, status, 0))
    if r < 0 { r } else { word(status, 0) }
}

-- A child that is a zombie by the time it is registered.
fun exitedChild() -> Unit {
    let pid = Sys.cInt(fork())
    if pid == 0 { Libc.exitNow(7) }
    let _ = Libc.usleep(300000)
    print(Workers.pollChild(pid, 5))
    let _ = Workers.claim()
    let n = Workers.pollNow()
    print(n)
    show(n)
    Workers.release()
    Workers.pollForgetChild(pid, 5)
    print(reap(pid))
}

-- A child that exits while it is waited for.
fun runningChild() -> Unit {
    let pid = Sys.cInt(fork())
    if pid == 0 {
        let _ = Libc.usleep(300000)
        Libc.exitNow(9)
    }
    print(Workers.pollChild(pid, 6))
    let _ = Workers.claim()
    let seen = Workers.workSeen()
    let n = Heap.pause(seen, 5000000000)
    print(n)
    show(n)
    Workers.release()
    Workers.pollForgetChild(pid, 6)
    print(reap(pid))
}
"""


@pytest.fixture
def probe(request, monkeypatch):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    module = f"Probe_poller_{digest}"
    path = LIB / f"{module}.gob"
    monkeypatch.setenv("TURKEY_TEST_FOREIGN", module)
    path.write_text(PROBE.replace("PROBE", module), encoding="utf-8")

    def call(body: str, workers: str = "1") -> list[str]:
        result = run(f"import {module} as P\nfun main() {{ {body} }}\n", env={"TURKEY_WORKERS": workers})
        assert result.code == 0, result.stderr
        return result.stdout.split("\n")[:-1]

    try:
        yield call
    finally:
        path.unlink(missing_ok=True)


def test_a_ready_descriptor_is_one_result_with_its_token_and_the_readable_flag(probe):
    # registered; one result; token 77, readable; then nothing: one-shot.
    assert probe("P.ready()") == ["0", "1", "77", "1", "0"]


def test_a_quiet_descriptor_reports_nothing(probe):
    assert probe("P.quiet()") == ["0", "0"]


def test_a_wait_with_nothing_ready_ends_at_its_timeout(probe):
    n, ms = probe("P.timeout(50000000)")
    assert n == "0"
    assert 50 <= int(ms) < 500


def test_a_break_sent_before_the_wait_ends_it_at_once(probe):
    assert probe("P.wakeBefore()") == ["0", "0", "True"]


def test_a_break_from_another_worker_ends_a_blocked_wait(probe):
    assert probe("P.wakeDuring()", workers="2") == ["0", "0", "True"]


def test_a_child_that_exited_is_told_apart_from_one_that_has_not(probe):
    out = probe("P.exitedChild()")
    if DARWIN:
        # Already exited: 1, nothing delivered, and the status is waitpid's.
        assert out == ["1", "0", str(7 << 8)]
    else:
        # A zombie's pidfd is registered and readable at once.
        assert out == ["0", "1", "5", "1", str(7 << 8)]


def test_a_child_that_exits_during_the_wait_is_delivered_with_its_status(probe):
    out = probe("P.runningChild()")
    if DARWIN:
        # Registered; one result, token 6, the raw wait status; reaped by waitpid.
        assert out == ["0", "1", "6", str(9 << 8), str(9 << 8)]
    else:
        assert out == ["0", "1", "6", "1", str(9 << 8)]
