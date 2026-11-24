"""Single-thread mode: with `TURKEY_SINGLE_THREAD` set, a program runs on the
thread the process started on and starts no other.

The program below does what would otherwise start threads -- it makes tasks,
which starts the workers and the monitor, and it waits from its own stack in
`parkApart`, which hands the permit to a spare -- and stops twice, blocked in a
read of its standard input, for the test to count its threads. Workers are
started when `System.Task` is initialized and never end, so a thread made at
any point before the last stop is still there to be counted at it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import bootc, toolchain

pytestmark = pytest.mark.skipif(
    toolchain.missing() or bool(os.environ.get("TURKEY_RUN")),
    reason="needs a native run, whose threads are the program's own")

PROGRAM = """
import System.IO as IO
import System.Parallel as Parallel
import System.Sync as Sync
import System.Task as Task
import Time.Duration as Duration
import Time.Sleep as Sleep

fun depth(n : Int) -> Int = if n == 0 { 0 } else { 1 + depth(n - 1) }

fun stop(name : String) {
    let _ = IO.writeText(IO.stderr(), name + "\\n")
    match IO.readLine(IO.stdin()) {
        Right(Some(text)) -> print(text)
        _ -> print("no line")
    }
}

fun main() {
    stop("started")
    let raised = Sync.event()
    let (a, b) = Task.runScope(do {
        let a = Task.spawn(fun() {
            Sleep.sleep(Duration.millis(20))
            Sync.setEvent(raised)
            1
        })?
        let b = Task.spawn(fun() {
            Task.yield()
            2
        })?
        pure((a, b))
    })
    print(Task.join(a) + Task.join(b))
    print(Sync.waitEvent(raised, Duration.seconds(5)))
    print(Sync.waitEvent(Sync.event(), Duration.millis(10)))
    print(Parallel.map([1, 2, 3, 4], fun(x) = x * x))
    print(depth(10000))
    stop("ready")
}
"""

OUTPUT = "first\n3\nTrue\nFalse\n[1, 4, 9, 16]\n10000\nsecond\n"


@pytest.fixture(scope="module")
def program(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("single_thread")
    entry = directory / "main.gob"
    entry.write_text(PROGRAM)
    binary = directory / "program"
    bootc.executable(entry, binary)
    return binary


def threads(pid: int) -> int:
    """How many threads process `pid` has."""
    if sys.platform == "darwin":
        listed = subprocess.run(["ps", "-M", "-p", str(pid)], check=True,
                                capture_output=True, text=True).stdout
        # A header line, then a line per thread.
        return len(listed.splitlines()) - 1
    return len(os.listdir(f"/proc/{pid}/task"))


def counted(program: Path, **env: str) -> tuple[list[int], str]:
    """The threads `program` has at each of its stops, and its output."""
    environment = {k: v for k, v in os.environ.items()
                   if k != "TURKEY_SINGLE_THREAD"}
    process = subprocess.Popen(
        toolchain.command(program), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=dict(environment, **env))
    seen = []
    try:
        for name, line in [("started", "first"), ("ready", "second")]:
            assert process.stderr.readline() == name + "\n"
            seen.append(threads(process.pid))
            process.stdin.write(line + "\n")
            process.stdin.flush()
        out, err = process.communicate(timeout=60)
    finally:
        process.kill()
    assert process.returncode == 0, err
    return seen, out


def test_the_program_has_one_thread_for_its_lifetime(program):
    # Four workers asked for, which the mode overrides.
    seen, out = counted(program, TURKEY_SINGLE_THREAD="1", TURKEY_WORKERS="4")
    assert seen == [1, 1]
    assert out == OUTPUT


def test_without_the_mode_the_same_program_starts_threads(program):
    # What the count above would show if the mode started any: the workers
    # and the monitor, from as `System.Task` is initialized.
    seen, out = counted(program, TURKEY_WORKERS="4")
    assert all(n > 1 for n in seen)
    assert out == OUTPUT


# Waits from `main`'s own stack that a task ends: a semaphore's permit, given
# back by a task while `main` waits, and a detached call, which the one thread
# runs to its end however early its deadline when the call does not wait. The tasks allocate, so that a
# collection under `TURKEY_GC_STRESS` finds them parked and running.
WAITS = """
import System.Sync as Sync
import System.Task as Task
import Time.Deadline as Deadline
import Time.Duration as Duration
import Time.Sleep as Sleep
import Turkey.Detach as Detach

fun squares(n : Int) -> Vec String {
    let out : Vec String = []
    for var i = 0; i < n; i = i + 1 {
        Vec.push(out, Int.toString(i * i))
        Task.yield()
    }
    out
}

fun squaresAtOnce(n : Int) -> Vec String {
    let out : Vec String = []
    for var i = 0; i < n; i = i + 1 { Vec.push(out, Int.toString(i * i)) }
    out
}

fun main() {
    let s = Sync.semaphore(0)
    let (got, a, b) = Task.runScope(do {
        let a = Task.spawn(fun() = squares(20))?
        let b = Task.spawn(fun() = squares(30))?
        let _ = Task.spawn(fun() {
            Sleep.sleep(Duration.millis(10))
            Sync.release(s)
        })?
        pure((Sync.acquire(s, Some(Deadline.after(Duration.seconds(5)))), a, b))
    })
    print(got)
    print(Sync.acquire(s, Some(Deadline.after(Duration.millis(10)))))
    print(len(Task.join(a)) + len(Task.join(b)))
    print(Task.join(b)[29])
    -- A deadline on `Turkey.Wait.now`'s clock that passed long ago, for a
    -- call that never waits, as a C call does not.
    print(Detach.run(fun() = Int.toString(len(squaresAtOnce(3))), fun(_) = {}, 1))
}
"""

WAITS_OUTPUT = 'True\nFalse\n50\n841\nRight("3")\n'


def built(directory: Path, source: str, *flags: str) -> Path:
    entry = directory / "main.gob"
    entry.write_text(source)
    binary = directory / "program"
    imports = os.environ[bootc.INTERNAL_IMPORTS] + ",Turkey.Detach"
    bootc.executable(entry, binary, *flags,
                     env=dict(os.environ, **{bootc.INTERNAL_IMPORTS: imports}))
    return binary


def single(binary: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(toolchain.command(binary), capture_output=True,
                          text=True, timeout=120,
                          env=dict(os.environ, TURKEY_SINGLE_THREAD="1", **env))


def test_main_waits_for_a_task_and_a_detached_call_on_the_one_thread(tmp_path):
    ran = single(built(tmp_path, WAITS))
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == WAITS_OUTPUT


def test_collections_with_tasks_on_the_one_thread(tmp_path):
    # Tasks with no registered thread are a state only this mode has:
    # collect, and verify the heap, at every allocation.
    ran = single(built(tmp_path, WAITS, "--gc-verify"), TURKEY_GC_STRESS="1")
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == WAITS_OUTPUT


def test_a_wait_nothing_can_end_is_a_deadlock(tmp_path):
    # Without the mode, `main` would sleep apart from the tasks for ever.
    ran = single(built(tmp_path, """
import System.Sync as Sync

fun main() {
    Sync.waitEventUnbounded(Sync.event())
}
"""))
    assert ran.returncode == 1
    assert ran.stderr.startswith("panic: deadlock: every task is waiting\n")
