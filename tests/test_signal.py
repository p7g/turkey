"""`Signal`: a program waits for a signal as a value, and the disposition the
signal had comes back when nobody listens.

Each test compiles a Turkey program, starts it, waits for it to print
`ready` -- it has listened by then -- and sends it signals from here, as a
service manager or a terminal would. Each runs on one worker and on four.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import time

import pytest

from tests import bootc, toolchain
from tests.lang import binary, run

HEADER = """\
import Signal
import System.IO as IO
import Time.Duration as Duration
import Time.Sleep as Sleep

-- Tell the test the program has listened, past the pipe's buffer.
fun ready() -> Unit {
    print("ready")
    let _ = IO.flush(IO.stdout())
}

fun listening(signals : Array Signal.Signal) -> Signal.Listener = match Signal.listen(signals) {
    Right(l) -> l
    Left(e) -> error("listen: " + show(e))
}
"""

WORKERS = pytest.mark.parametrize("workers", ["1", "4"])

# How long a step may take before the test gives up on the program.
PATIENCE = 30


def start(body: str, workers: str, preexec=None) -> subprocess.Popen:
    """The program, started, once it has printed `ready`."""
    exe = binary(HEADER + body)
    p = subprocess.Popen(toolchain.command(exe), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True,
                         env=dict(os.environ, TURKEY_WORKERS=workers),
                         preexec_fn=preexec)
    readable, _, _ = select.select([p.stdout], [], [], PATIENCE)
    if not readable:
        p.kill()
        pytest.fail("the program did not print 'ready'")
    line = p.stdout.readline()
    assert line == "ready\n", line + p.communicate()[1]
    return p


def finish(p: subprocess.Popen) -> tuple[list[str], int]:
    """The rest of what the program printed, and how it ended."""
    out, err = p.communicate(timeout=PATIENCE)
    return out.split("\n")[:-1], p.returncode


@WORKERS
def test_sigterm_runs_the_shutdown_and_the_program_exits(workers):
    p = start("""
fun main() {
    let signals = listening([Signal.Terminate, Signal.Interrupt])
    ready()
    let got = Signal.waitUnbounded(signals)
    print("shutting down on " + show(got))
    print("done")
}
""", workers)
    p.send_signal(signal.SIGTERM)
    assert finish(p) == (["shutting down on Terminate", "done"], 0)


@WORKERS
def test_a_task_waits_for_the_signal_while_others_run(workers):
    # One task works until told to stop; another waits for SIGINT and tells it.
    p = start("""
import System.Sync as Sync
import System.Task as Task

fun main() {
    let signals = listening([Signal.Interrupt])
    let stopping = Sync.event()
    let rounds = Sync.atomicInt(0)
    ready()
    Task.runScope(do {
        let _ = Task.spawn(fun() {
            while !Sync.isEventSet(stopping) {
                let _ = Sync.addAndGet(rounds, 1)
                Sleep.sleep(Duration.millis(5))
            }
        })?
        let _ = Task.spawn(fun() {
            print("stopping on " + show(Signal.waitUnbounded(signals)))
            Sync.setEvent(stopping)
        })?
        pure({})
    })
    print(Sync.load(rounds) > 0)
}
""", workers)
    time.sleep(0.2)
    p.send_signal(signal.SIGINT)
    assert finish(p) == (["stopping on Interrupt", "True"], 0)


@WORKERS
def test_repeats_coalesce_and_kinds_stay_apart(workers):
    p = start("""
fun main() {
    let signals = listening([Signal.Hangup, Signal.User1])
    ready()
    -- The signals arrive while nobody waits.
    Sleep.sleep(Duration.millis(500))
    loop {
        match Signal.wait(signals, Duration.millis(200)) {
            Some(s) -> print(s)
            None -> break
        }
    }
    print("none left")
}
""", workers)
    for _ in range(3):
        p.send_signal(signal.SIGHUP)
    p.send_signal(signal.SIGUSR1)
    assert finish(p) == (["Hangup", "User1", "none left"], 0)


@WORKERS
def test_every_listener_gets_the_signal(workers):
    p = start("""
fun main() {
    let a = listening([Signal.Terminate])
    let b = listening([Signal.Terminate, Signal.Hangup])
    ready()
    print("a " + show(Signal.waitUnbounded(a)))
    print("b " + show(Signal.waitUnbounded(b)))
    print(Signal.wait(b, Duration.millis(50)))
}
""", workers)
    p.send_signal(signal.SIGTERM)
    assert finish(p) == (["a Terminate", "b Terminate", "None"], 0)


@WORKERS
def test_stopping_the_last_listener_brings_the_default_back(workers):
    p = start("""
fun main() {
    let a = listening([Signal.Terminate])
    let b = listening([Signal.Terminate])
    Signal.stop(a)
    ready()
    print(Signal.waitUnbounded(b))
    let _ = IO.flush(IO.stdout())
    Signal.stop(b)
    Signal.stop(b)
    Sleep.sleep(Duration.seconds(30))
    print("still running")
}
""", workers)
    p.send_signal(signal.SIGTERM)
    # The first SIGTERM is taken; once the program reaches its sleep, nobody
    # listens, and the second one ends it.
    out = p.stdout.readline()
    assert out == "Terminate\n"
    time.sleep(0.3)
    p.send_signal(signal.SIGTERM)
    assert finish(p) == ([], -signal.SIGTERM)


@WORKERS
def test_a_signal_ignored_at_start_is_ignored_again_after_stop(workers):
    # As `nohup` starts a program: SIGHUP ignored. While the program listens it
    # gets the signal; once it stops, SIGHUP is ignored again, not fatal.
    p = start("""
fun main() {
    let signals = listening([Signal.Hangup])
    ready()
    print(Signal.waitUnbounded(signals))
    Signal.stop(signals)
    print("stopped")
    let _ = IO.flush(IO.stdout())
    Sleep.sleep(Duration.millis(500))
    print("survived")
}
""", workers, preexec=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
    p.send_signal(signal.SIGHUP)
    assert p.stdout.readline() == "Hangup\n"
    assert p.stdout.readline() == "stopped\n"
    p.send_signal(signal.SIGHUP)
    assert finish(p) == (["survived"], 0)


@WORKERS
def test_stop_ends_a_wait_in_another_task(workers):
    p = start("""
import System.Task as Task
import Time.Stopwatch as Stopwatch

fun main() {
    let signals = listening([Signal.User2])
    ready()
    let sw = Stopwatch.start()
    Task.runScope(do {
        let _ = Task.spawn(fun() = print(Signal.wait(signals, Duration.seconds(30))))?
        let _ = Task.spawn(fun() {
            Sleep.sleep(Duration.millis(100))
            Signal.stop(signals)
        })?
        pure({})
    })
    print(Duration.toNanos(Stopwatch.elapsed(sw)) < 10_000_000_000)
}
""", workers)
    assert finish(p) == (["None", "True"], 0)


@WORKERS
def test_a_child_started_while_listening_has_the_default_disposition(workers):
    # The child's own SIGTERM ends it, though the parent catches SIGTERM.
    result = run(HEADER + """
import Process

fun main() {
    let _ = listening([Signal.Terminate])
    match Process.run(Process.command(["/bin/sh", "-c", "kill -TERM $$; echo survived"]), None) {
        Right(o) -> print(o.status)
        Left(e) -> print(e)
    }
}
""", env={"TURKEY_WORKERS": workers})
    assert (result.stdout, result.code) == ("Signaled(15)\n", 0), result.stderr


def test_waitUnbounded_on_a_stopped_listener_panics():
    result = run(HEADER + """
fun main() {
    let signals = listening([Signal.Terminate])
    Signal.stop(signals)
    let _ = Signal.waitUnbounded(signals)
}
""")
    assert result.code == 1
    assert "panic: Signal.waitUnbounded: the listener is stopped" in result.stderr


def test_numbers_are_this_targets():
    result = run(HEADER + """
fun main() {
    for s in [Signal.Hangup, Signal.Interrupt, Signal.Terminate, Signal.User1,
              Signal.User2, Signal.WindowChange] {
        print(Signal.number(s))
    }
}
""")
    assert result.code == 0, result.stderr
    assert result.stdout.split("\n")[:-1] == [
        str(int(s)) for s in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGUSR1,
                              signal.SIGUSR2, signal.SIGWINCH)]


def test_the_http_server_example_builds():
    # It stops its server on SIGTERM; it listens on a fixed port, so the suite
    # only checks it.
    bootc.boot("check", "examples/http-server/main.gob")
