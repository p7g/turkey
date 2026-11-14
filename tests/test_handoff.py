"""A thread blocked in a call the poller cannot carry costs no running worker.

A thread in one of `Turkey.Workers`' blocking calls keeps its run permit until
the monitor (`Turkey.Monitor`) sees it in the same call on two ticks while
tasks wait, and then the permit goes to a spare thread. Each program here
starts as many spinners as there are permits, each of which waits without
yielding until every one has arrived: they can finish only if they all run at
once, so only if the thread blocked beside them gave its permit up.
"""

import fcntl
import os
import re
import subprocess
import threading
import time

import pytest

from tests import bootc, toolchain


@pytest.fixture(autouse=True)
def internal(monkeypatch):
    """The blocking call is `Turkey.Workers`' own, which a test program may
    import only by name."""
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       os.environ[bootc.INTERNAL_IMPORTS]
                       + ",Turkey.Scheduler,Turkey.Workers")


SPINNERS = """
-- Start `n` spinners. Each waits, without yielding, until all `n` have
-- arrived; the last to arrive says so on stderr.
fun spinners(n : Int) -> Task.Spawn (Vec (Task.Task Int)) {
    let arrived = Sync.atomicInt(0)
    let started : Vec (Task.Task Int) = []
    for var i = 0; i < n; i = i + 1 {
        Vec.push(started, Task.spawn(fun() {
            if Sync.addAndGet(arrived, 1) == n {
                let _ = IO.writeText(IO.stderr(), "spun\\n")
            }
            var x = 0
            while Sync.load(arrived) < n { x = (x + 1) % 1000 }
            x
        })?)
    }
    pure(started)
}

fun permits() -> Int = match Env.get("TURKEY_WORKERS") {
    Some(text) -> Option.unwrapOr(Int.parse(text), 1)
    None -> 1
}

-- A read of up to `most` bytes of the standard input, which blocks its
-- thread, as a regular file's does, rather than waiting in the poller.
fun blockedRead(most : Int) -> Int {
    let buffer = Libc.malloc(most)
    let n = Workers.blockingRead(0, buffer, most)
    Scheduler.afterCall()
    Libc.free(buffer)
    n
}
"""

HEADER = """
import System.Env as Env
import System.IO as IO
import System.Sync as Sync
import System.Task as Task
import Turkey.Libc as Libc
import Turkey.Scheduler as Scheduler
import Turkey.Workers as Workers
"""

READ = HEADER + SPINNERS + """
fun main() {
    let (reader, spun) = Task.runScope(do {
        -- First, so that it is the first task a thread takes.
        let reader = Task.spawn(fun() = blockedRead(8))?
        let spun = spinners(permits())?
        pure((reader, spun))
    })
    for t in spun { let _ = Task.join(t) }
    print(Task.join(reader))
}
"""


def _start(tmp_path, source, workers, *flags, **env):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    entry = tmp_path / "main.gob"
    entry.write_text(source)
    binary = tmp_path / "handoff"
    bootc.executable(entry, binary, *flags)
    return subprocess.Popen(toolchain.command(binary), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=dict(os.environ, TURKEY_WORKERS=workers, **env))


@pytest.mark.parametrize("workers", ["1", "2", "4"])
def test_a_blocked_read_costs_no_running_worker(tmp_path, workers):
    # With one permit the read blocks the program's own thread, which runs
    # tasks: the spinner runs on a spare, and the read's task goes on once
    # the spare gives the permit back.
    process = _start(tmp_path, READ, workers)
    try:
        assert process.stderr.readline() == "spun\n"
        out, err = process.communicate("hello", timeout=60)
    finally:
        process.kill()
    assert process.returncode == 0, err
    assert out == "5\n"


LOCK = HEADER + SPINNERS + """
import System.File as File

fun main() {
    let path = match Env.get("LOCKED") { Some(p) -> p
        None -> "" }
    let (locker, spun) = Task.runScope(do {
        let locker = Task.spawn(fun() = match File.openReadWrite(path) {
            Right(f) -> match File.lock(f) {
                Right(_) -> print("locked")
                Left(_) -> print("lock failed")
            }
            Left(_) -> print("open failed")
        })?
        let spun = spinners(permits())?
        pure((locker, spun))
    })
    for t in spun { let _ = Task.join(t) }
    Task.join(locker)
}
"""


@pytest.mark.parametrize("workers", ["1", "2"])
def test_a_blocked_flock_costs_no_running_worker(tmp_path, workers):
    locked = tmp_path / "locked"
    locked.write_text("")
    with open(locked, "r+") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        process = _start(tmp_path, LOCK, workers, LOCKED=str(locked))
        try:
            assert process.stderr.readline() == "spun\n"
            fcntl.flock(holder, fcntl.LOCK_UN)
            out, err = process.communicate(timeout=60)
        finally:
            process.kill()
    assert process.returncode == 0, err
    assert out == "locked\n"


PRINT = HEADER + SPINNERS + """
import System.File as File

fun main() {
    let line = String.repeat("x", 1000)
    let n = permits()
    let arrived = Sync.atomicInt(0)
    let printer = Task.runScope(do {
        -- First, so that it is the first task a thread takes. The pipe fills
        -- long before it is done, and its write then waits for the test.
        let printer = Task.spawn(fun() {
            for var i = 0; i < 2000; i = i + 1 { print(line) }
            0
        })?
        -- Spinners as `spinners` starts them, which say so in a file: a
        -- write to the standard error first writes out the standard output.
        for var i = 0; i < n; i = i + 1 {
            let _ = Task.spawn(fun() {
                if Sync.addAndGet(arrived, 1) == n {
                    let _ = File.writeText(Option.unwrapOr(Env.get("SPUN"), ""), "spun")
                }
                var x = 0
                while Sync.load(arrived) < n { x = (x + 1) % 1000 }
                x
            })?
        }
        pure(printer)
    })
    let _ = Task.join(printer)
}
"""


@pytest.mark.parametrize("workers", ["1", "2"])
def test_a_print_blocked_on_a_full_pipe_costs_no_running_worker(tmp_path, workers):
    # The standard output is written out through a blocking call, so the
    # spinners finish while the test has read none of it.
    spun = tmp_path / "spun"
    process = _start(tmp_path, PRINT, workers, SPUN=str(spun))
    try:
        deadline = time.monotonic() + 60
        while not spun.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert spun.exists(), "the spinners did not run while the print waited"
        out, err = process.communicate(timeout=60)
    finally:
        process.kill()
    assert process.returncode == 0, err
    assert out == ("x" * 1000 + "\n") * 2000


MANY = HEADER + SPINNERS + """
fun churn(n : Int) -> Int {
    let parts : Vec String = []
    for var i = 0; i < 50; i = i + 1 { Vec.push(parts, Int.toString(n + i)) }
    String.byteLength(String.join(parts, ","))
}

fun main() {
    let wanted = match Env.get("READERS") { Some(t) -> Option.unwrapOr(Int.parse(t), 0)
        None -> 0 }
    let (readers, churner) = Task.runScope(do {
        let readers : Vec (Task.Task Int) = []
        for var i = 0; i < wanted; i = i + 1 { Vec.push(readers, Task.spawn(fun() = blockedRead(1))?) }
        -- Collects, by allocating, while the readers' threads pile up, and
        -- for a while after they all have.
        let churner = Task.spawn(fun() {
            var total = 0
            var k = 0
            var after = 0
            while after < 20 {
                total = total + churn(k)
                k = k + 1
                if Workers.count() > wanted { after = after + 1 }
                Task.yield()
            }
            let _ = IO.writeText(IO.stderr(), "churned\\n")
            total
        })?
        pure((readers, churner))
    })
    let _ = Task.join(churner)
    var bytes = 0
    for t in readers { bytes = bytes + Task.join(t) }
    print(bytes)
}
"""


def test_a_collection_with_hundreds_of_blocked_threads(tmp_path):
    # Every reader blocks a thread of its own, and each collection stops
    # them all. The readers are let go one byte at a time: woken all at
    # once, 300 threads each collecting at every allocation take minutes
    # on a 4-core machine, which tests the machine rather than the handoff.
    readers = 300
    process = _start(tmp_path, MANY, "2", "--gc-verify", "--gc-stats",
                     READERS=str(readers), TURKEY_GC_STRESS="1")
    try:
        # Each collection reports itself on stderr first.
        before = []
        while (line := process.stderr.readline()) not in ("churned\n", ""):
            before.append(line)
        assert line == "churned\n", "".join(before)[-2000:]
        # Drained meanwhile, or a full pipe would stop the program.
        after = []
        drain = threading.Thread(target=lambda: after.extend(process.stderr))
        drain.start()
        for _ in range(readers):
            process.stdin.write("x")
            process.stdin.flush()
            time.sleep(0.002)
        process.stdin.close()
        process.wait(timeout=300)
        out = process.stdout.read()
        drain.join()
        err = "".join(after)
    finally:
        process.kill()
    assert process.returncode == 0, err[-2000:]
    assert out == f"{readers}\n"
    threads = re.search(r"\[gc\] threads (\d+) of at most (\d+), permits 2", err)
    assert threads, err[-2000:]
    assert 300 < int(threads.group(1)) <= int(threads.group(2))


ENDING = """
import System.Task as Task

fun main() {
    let tasks = Task.runScope(do {
        let started : Vec (Task.Task Int) = []
        for var i = 0; i < 64; i = i + 1 {
            let n = i
            Vec.push(started, Task.spawn(fun() = n * 2)?)
        }
        pure(started)
    })
    var total = 0
    for t in tasks { total = total + Task.join(t) }
    print(total)
}
"""


def test_the_report_at_exit_stops_for_another_workers_collection(tmp_path):
    # The --gc-stats report stops the world to retire every cursor. Workers
    # woken by the last task to finish allocate as they look for more, and
    # under stress each of those allocations collects: the report must stop
    # for that collection rather than wait for it. A race, so many runs.
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    entry = tmp_path / "main.gob"
    entry.write_text(ENDING)
    binary = tmp_path / "ending"
    bootc.executable(entry, binary, "--gc-stats")
    env = dict(os.environ, TURKEY_WORKERS="8", TURKEY_GC_STRESS="1")
    for _ in range(100):
        result = subprocess.run(toolchain.command(binary), capture_output=True, text=True,
                                env=env, timeout=60)
        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout == "4032\n"
