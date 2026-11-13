"""`Process`: running a program, capturing its output and status, timing it out.

Each test is a Turkey program that runs `/bin/sh` or a tool beside it and
prints what `Process.run` answered. Each runs on one worker and on four.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from tests import bootc
from tests.lang import run

HEADER = """\
import Process
import Time.Duration as Duration
import Time.Stopwatch as Stopwatch (Stopwatch)

fun text(b : Array Byte) -> String = match String.fromBytes(Vec.fromArray(b)) {
    Some(s) -> s
    None -> "?"
}

fun sh(script : String) -> Process.Command = Process.command(["/bin/sh", "-c", script])

fun millisSince(sw : Stopwatch) -> Int = Duration.toNanos(Stopwatch.elapsed(sw)) / 1000000
"""

WORKERS = pytest.mark.parametrize("workers", ["1", "4"])


def lines(body: str, workers: str, **kw) -> list[str]:
    result = run(HEADER + body, env={"TURKEY_WORKERS": workers}, **kw)
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


@WORKERS
def test_stdout_stderr_and_the_exit_code_come_back_apart(workers):
    out = lines("""
fun main() {
    match Process.run(sh("echo out; echo err >&2; exit 3"), None) {
        Right(o) -> {
            print(text(o.stdout))
            print(text(o.stderr))
            print(o.status)
        }
        Left(e) -> print(e)
    }
}
""", workers)
    assert out == ["out", "", "err", "", "Exited(3)"]


@WORKERS
def test_a_program_found_on_the_path_and_empty_standard_input(workers):
    out = lines("""
fun main() {
    match Process.run(Process.command(["echo", "hi", "there"]), None) {
        Right(o) -> print(text(o.stdout) + " " + show(o.status))
        Left(e) -> print(e)
    }
    -- Standard input is /dev/null: `cat` ends at once.
    match Process.run(Process.command(["cat"]), None) {
        Right(o) -> print(show(len(o.stdout)) + " " + show(o.status))
        Left(e) -> print(e)
    }
}
""", workers)
    assert out == ["hi there", " Exited(0)", "0 Exited(0)"]


@WORKERS
def test_a_signal_death_is_not_an_exit_code(workers):
    out = lines("""
fun main() {
    match Process.run(sh("kill -SEGV $$"), None) {
        Right(o) -> print(o.status)
        Left(e) -> print(e)
    }
    match Process.run(sh("exit 139"), None) {
        Right(o) -> print(o.status)
        Left(e) -> print(e)
    }
}
""", workers)
    assert out == ["Signaled(11)", "Exited(139)"]


@WORKERS
def test_a_program_that_cannot_start_is_an_error_and_not_a_crash(workers, tmp_path):
    out = lines("""
fun main() {
    match Process.run(Process.command(["/no/such/program"]), None) {
        Right(o) -> print(o.status)
        Left(e) -> print(e)
    }
    match Process.run(Process.inDirectory(Process.command(["/bin/echo"]), "/no/such/directory"), None) {
        Right(o) -> print(o.status)
        Left(e) -> print(e)
    }
    match Process.run(Process.command([]), None) {
        Right(o) -> print(o.status)
        Left(e) -> print(e)
    }
    match Process.run(Process.command(["nonexistent-program-name"]), None) {
        Right(o) -> print(o.status)
        Left(e) -> print(e)
    }
}
""", workers)
    assert out[0] == "spawn /no/such/program: no such file or directory"
    assert out[1] == "spawn /bin/echo: no such file or directory"
    assert out[2].startswith("spawn : ")
    assert out[3] == "spawn nonexistent-program-name: no such file or directory"


@WORKERS
def test_more_than_a_pipe_buffer_on_both_streams_at_once(workers):
    # Each stream is written in full before the other is touched, so reading
    # one to its end before the other would deadlock.
    out = lines("""
fun main() {
    match Process.run(sh("head -c 300000 /dev/zero | tr '\\\\000' b >&2; head -c 300000 /dev/zero | tr '\\\\000' a"), None) {
        Right(o) -> {
            print(len(o.stdout))
            print(len(o.stderr))
            print(o.stdout[0] == Byte.truncate(97) && o.stdout[299999] == Byte.truncate(97))
            print(o.stderr[0] == Byte.truncate(98) && o.stderr[299999] == Byte.truncate(98))
            print(o.status)
        }
        Left(e) -> print(e)
    }
}
""", workers)
    assert out == ["300000", "300000", "True", "True", "Exited(0)"]


@WORKERS
def test_the_working_directory_and_environment_overrides(workers, tmp_path):
    (tmp_path / "sub").mkdir()
    result = run(HEADER + """
import System.Task as Task

fun main() {
    let cmd = Process.withEnvironment(
        Process.withEnvironment(
            Process.withEnvironment(
                Process.inDirectory(sh("pwd; echo $TURKEY_PROCESS_A $TURKEY_PROCESS_B; echo $HOME; echo $KEEP"),
                                    "sub"),
                "TURKEY_PROCESS_A", "one"),
            "TURKEY_PROCESS_B", "two"),
        "TURKEY_PROCESS_A", "three")
    -- From a task, whose worker is not the one that started the program.
    let r = Task.runScope(do {
        let t = Task.spawn(fun() = Process.run(Process.withEnvironment(cmd, "HOME", "/elsewhere"), None))?
        pure(Task.join(t))
    })
    match r {
        Right(o) -> print(text(o.stdout))
        Left(e) -> print(e)
    }
}
""", env={"TURKEY_WORKERS": workers, "KEEP": "kept"}, cwd=tmp_path)
    assert result.code == 0, result.stderr
    got = result.stdout.split("\n")
    assert got[0] == os.path.realpath(tmp_path / "sub")
    assert got[1:4] == ["three two", "/elsewhere", "kept"]


@WORKERS
def test_a_timeout_kills_the_program_and_keeps_what_it_wrote(workers):
    out = lines("""
fun main() {
    let sw = Stopwatch.start()
    match Process.run(sh("echo before; echo oops >&2; sleep 30; echo after"), Some(Duration.millis(400))) {
        Right(o) -> {
            print(o.status)
            print(text(o.stdout))
            print(text(o.stderr))
        }
        Left(e) -> print(e)
    }
    let took = millisSince(sw)
    print(took >= 400 && took < 3000)
}
""", workers)
    assert out == ["TimedOut", "before", "", "oops", "", "True"]


@WORKERS
def test_a_timeout_with_a_grandchild_holding_the_pipe(workers):
    """The program leaves at once, and what it started keeps the streams open
    and runs far past the deadline: the caller returns at the deadline, and the
    grandchild is gone."""
    result = run(HEADER + """
fun main() {
    let sw = Stopwatch.start()
    match Process.run(sh("sleep 60 & echo $!"), Some(Duration.millis(500))) {
        Right(o) -> {
            print(o.status)
            print(text(o.stdout))
        }
        Left(e) -> print(e)
    }
    let took = millisSince(sw)
    print(took >= 500 && took < 5000)
}
""", env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    status, pid, _, ok, *_ = result.stdout.split("\n")
    assert status == "TimedOut" and ok == "True"
    assert gone(int(pid))


@WORKERS
def test_a_timeout_kills_everything_in_the_programs_group(workers):
    result = run(HEADER + """
fun main() {
    match Process.run(sh("sleep 60 & sleep 60 & echo $!; wait"), Some(Duration.millis(500))) {
        Right(o) -> {
            print(o.status)
            print(text(o.stdout))
        }
        Left(e) -> print(e)
    }
}
""", env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    status, pid, *_ = result.stdout.split("\n")
    assert status == "TimedOut"
    assert gone(int(pid))


def gone(pid: int) -> bool:
    """Whether `pid` is no longer running, allowing a moment for it to be
    reaped by whoever inherited it."""
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@WORKERS
def test_several_run_at_once_and_are_waited_for_as_each_ends(workers):
    out = lines("""
import System.Task as Task

fun main() {
    let sw = Stopwatch.start()
    let codes = Task.runScope(do {
        let ts = []
        for ms in [900, 100, 500, 300] {
            let script = "sleep " + (if ms >= 1000 { "1" } else { "0." + show(ms / 100) }) + "; exit " + show(ms / 100)
            Vec.push(ts, Task.spawn(fun() {
                match Process.run(sh(script), None) {
                    Right(o) -> {
                        print(o.status)
                        show(o.status)
                    }
                    Left(e) -> show(e)
                }
            })?)
        }
        pure(Vec.map(ts, Task.join))
    })
    print(codes)
    let took = millisSince(sw)
    print(took < 1700)
}
""", workers)
    # In order of ending, not of starting.
    assert out[:4] == ["Exited(1)", "Exited(3)", "Exited(5)", "Exited(9)"]
    assert out[4:] == ['["Exited(9)", "Exited(1)", "Exited(5)", "Exited(3)"]', "True"]


@WORKERS
def test_no_descriptor_of_the_parents_reaches_a_child(workers, tmp_path):
    (tmp_path / "held").write_bytes(b"x")
    out = lines("""
import System.File as File
import System.Task as Task

fun main() {
    -- A file held open, and another program running with pipes of its own,
    -- while the one that lists its descriptors starts.
    match File.openRead("held") {
        Left(e) -> print(e)
        Right(f) -> {
            let r = Task.runScope(do {
                let other = Task.spawn(fun() = Process.run(sh("sleep 1"), None))?
                let lister = Task.spawn(fun() {
                    match Process.run(Process.command(["ls", "/dev/fd"]), None) {
                        Right(o) -> text(o.stdout)
                        Left(e) -> show(e)
                    }
                })?
                let _ = Task.join(other)
                pure(Task.join(lister))
            })
            print(r)
            let _ = File.close(f)
        }
    }
}
""", workers, cwd=tmp_path)
    # What `ls` itself holds open, run from a parent that holds nothing.
    alone = subprocess.run(["ls", "/dev/fd"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, close_fds=True)
    assert {int(line) for line in out if line.strip()} == {int(n) for n in alone.stdout.split()}


@pytest.mark.skipif(sys.platform != "darwin",
                    reason="Linux relies on every descriptor being made close-on-exec")
def test_darwin_closes_even_a_descriptor_that_is_not_close_on_exec(monkeypatch, tmp_path):
    """The window `pipe` then `fcntl` leaves, and any other descriptor opened
    without the flag, are shut by `POSIX_SPAWN_CLOEXEC_DEFAULT`."""
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       os.environ[bootc.INTERNAL_IMPORTS] + ",Turkey.IO,Turkey.Sys")
    (tmp_path / "held").write_bytes(b"x")
    result = run(HEADER + """
import Turkey.IO as IO
import Turkey.Sys as Sys

fun main() {
    -- Past the numbers `ls` uses for itself.
    var held = 0
    for var i = 0; i < 8; i = i + 1 { held = IO.open("held", Sys.readOnly, 0) }
    match Process.run(Process.command(["ls", "/dev/fd"]), None) {
        Right(o) -> print(text(o.stdout))
        Left(e) -> print(e)
    }
    print(held)
}
""", cwd=tmp_path)
    assert result.code == 0, result.stderr
    *listed, held, _ = result.stdout.split("\n")
    assert int(held) > 8 and held not in listed
