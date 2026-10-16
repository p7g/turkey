"""The standard streams: one buffered standard output that `print`, `write`
and `System.IO.stdout()` share, written out at every ending of the program
and before the standard error is written to; the standard input as one
buffered reader; and the standard error, unbuffered.
"""

from __future__ import annotations

import os
import pty
import select
import subprocess
import time

import pytest

from tests import toolchain
from tests.lang import binary, run


def test_print_before_a_panic_appears_before_its_message():
    result = run("""
fun main() {
    print("before the panic")
    write("and a partial line")
    let xs = [1]
    print(xs[5])
}
""")
    assert result.code == 1
    assert result.stdout == "before the panic\nand a partial line"
    assert result.stderr.startswith("panic: array index out of bounds")


def test_print_before_exit_appears():
    result = run("""
import System.Env as Env

fun main() {
    print("before the exit")
    write("and a partial line")
    Env.exit(3)
    print("not reached")
}
""")
    assert result.code == 3
    assert result.stdout == "before the exit\nand a partial line"


def test_a_panic_in_a_task_still_writes_what_was_printed():
    result = run("""
import System.Task as Task

fun main() {
    print("printed first")
    let _ = Task.runScope(do {
        let t = Task.spawn(fun() {
            print("from the task")
            let xs = [1]
            xs[3]
        })?
        pure(t)
    })
}
""", env={"TURKEY_WORKERS": "4"})
    assert result.code == 1, result.stderr
    assert result.stdout == "printed first\nfrom the task\n"
    assert "panic: " in result.stderr


def merged(src: str, stdin: bytes = b"", env: dict[str, str] | None = None) -> bytes:
    """Run `src` with its standard output and error on one pipe."""
    result = subprocess.run(toolchain.command(binary(src)), input=stdin,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=dict(os.environ, **(env or {})), timeout=600)
    assert result.returncode == 0, result.stdout
    return result.stdout


def test_stderr_comes_after_what_was_printed_before_it():
    # Block-buffered, since the output is a pipe: only the write-out before
    # each write to stderr keeps the order.
    out = merged("""
import System.IO as IO

fun main() {
    print("one")
    write("two ")
    let _ = IO.writeText(IO.stderr(), "three\\n")
    print("four")
}
""")
    assert out == b"one\ntwo three\nfour\n"


def test_stdin_lines_and_binary_stdout_without_paths():
    out = merged("""
import System.IO as IO

fun main() {
    var n = 0
    for line in IO.lines(IO.stdin()) {
        match line {
            Right(text) -> { n = n + String.byteLength(text) }
            Left(e) -> print(e)
        }
    }
    print(n)
    let b = IO.buffer(256)
    for var i = 0; i < 256; i = i + 1 { IO.push(b, Byte.truncate(i)) }
    let _ = IO.writeFrom(IO.stdout(), b)
}
""", stdin=b"abc\ndefg\n" * 1000)
    assert out == b"7000\n" + bytes(range(256))


def test_lines_from_tasks_on_eight_workers_never_tear():
    # Twenty-six bytes a line, so that a 64 KiB buffer never fills on a newline:
    # each fill writes out up to its last whole line.
    out = merged("""
import System.Task as Task

fun lines(tag : String) -> Unit {
    for var i = 0; i < 20000; i = i + 1 {
        print("task " + tag + " line " + Int.toString(100000 + i) + " ......")
        if i % 1000 == 0 { Task.yield() }
    }
}

fun spawnFrom(k : Int) -> Task.Spawn Unit =
    if k == 8 {
        pure(())
    } else {
        do {
            let _ = Task.spawn(fun() = lines(Int.toString(k)))?
            spawnFrom(k + 1)
        }
    }

fun main() { Task.runScope(spawnFrom(0)) }
""", env={"TURKEY_WORKERS": "8"})
    lines = out.decode().split("\n")
    assert lines[-1] == ""
    lines = lines[:-1]
    assert len(lines) == 8 * 20000
    seen = {}
    for line in lines:
        words = line.split(" ")
        assert len(words) == 5 and words[0] == "task" and words[4] == "......", line
        k, i = int(words[1]), int(words[3]) - 100000
        # Each task's lines in its own order.
        assert seen.get(k, -1) == i - 1, line
        seen[k] = i


def test_a_line_longer_than_the_buffer_goes_out_whole_and_in_order():
    out = merged("""
fun main() {
    print("short")
    write("start ")
    print(String.repeat("x", 200000))
    print("after")
}
""")
    assert out == b"short\nstart " + b"x" * 200000 + b"\nafter\n"


def test_flush_reports_a_failed_write(tmp_path):
    # A descriptor opened only for reading, so that every write fails.
    readonly = tmp_path / "readonly"
    readonly.write_text("")
    src = """
import System.IO as IO

fun main() {
    print("lost")
    match IO.flush(IO.stdout()) {
        Left(e) -> {
            let _ = IO.writeText(IO.stderr(), message(e) + "\\n")
        }
        Right(_) -> {
            let _ = IO.writeText(IO.stderr(), "flushed\\n")
        }
    }
    -- Reported once.
    print(Either.isRight(IO.flush(IO.stdout())))
}
"""
    with open(readonly, "rb") as stdout:
        result = subprocess.run(toolchain.command(binary(src)), stdout=stdout,
                                stderr=subprocess.PIPE, timeout=600)
    assert result.returncode == 0
    assert result.stderr == b"write <stdout>: bad file descriptor\n"


def test_flush_makes_output_visible_before_a_wait():
    process = subprocess.Popen(toolchain.command(binary("""
import System.IO as IO

fun main() {
    print("ready")
    let _ = IO.flush(IO.stdout())
    match IO.readLine(IO.stdin()) {
        Right(Some(line)) -> print("got " + line)
        _ -> print("nothing")
    }
}
""")), stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert process.stdout.readline() == b"ready\n"
        out, _ = process.communicate(b"hello\n", timeout=60)
    finally:
        process.kill()
    assert out == b"got hello\n"


def read_until(fd: int, wanted: bytes, timeout: float = 30.0) -> bytes:
    got = b""
    deadline = time.monotonic() + timeout
    while wanted not in got:
        left = deadline - time.monotonic()
        assert left > 0, got
        ready, _, _ = select.select([fd], [], [], left)
        if ready:
            got += os.read(fd, 4096)
    return got


def test_on_a_terminal_lines_and_prompts_appear_at_once():
    """Every line is written out as it is printed, and a prompt with no
    newline before a read of a terminal."""
    path = binary("""
import System.IO as IO

fun main() {
    print("a line")
    write("name? ")
    match IO.readLine(IO.stdin()) {
        Right(Some(name)) -> print("hello " + name)
        _ -> print("nothing")
    }
}
""")
    main, side = pty.openpty()
    process = subprocess.Popen(toolchain.command(path), stdin=side, stdout=side,
                               stderr=subprocess.PIPE)
    os.close(side)
    try:
        # Neither waits for the program to end: it is waiting for the name.
        assert b"a line" in read_until(main, b"name? ")
        os.write(main, b"Ada\n")
        assert b"hello Ada" in read_until(main, b"hello Ada")
        assert process.wait(timeout=30) == 0
    finally:
        process.kill()
        os.close(main)
