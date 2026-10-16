"""`Turkey.FD`: a descriptor that tasks share, with a reference count that
keeps `close` from releasing the number under an operation still using it,
and locks that keep sequential operations whole.
"""

from __future__ import annotations

import os

import pytest

from tests import bootc
from tests.lang import run

HEADER = """\
import System.Sync as Sync
import System.Task as Task
import System.Time as Time
import Turkey.FD as FD
import Turkey.IO as IO
import Turkey.Sys as Sys

fun bytes(text : String) -> Array Byte = String.toBytes(text)

fun created(path : String) -> FD.Fd =
    FD.fd(IO.open(path, Int.or(Sys.readWrite, Int.or(Sys.create(), Sys.truncate())), 0o644))
"""


@pytest.fixture(autouse=True)
def internal(monkeypatch):
    """`Turkey.*` is the library's own; a test program may import these only
    by name."""
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       os.environ[bootc.INTERNAL_IMPORTS]
                       + ",Turkey.FD,Turkey.IO,Turkey.Sys")


def lines(src: str, cwd, workers: str = "1") -> list[str]:
    result = run(HEADER + src, cwd=cwd, env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


def test_sequential_and_positional_operations(tmp_path):
    out = lines("""
fun main() {
    let f = created("made")
    let text = bytes("hello, world")
    print(FD.write(f, text, 0, len(text)))
    print(FD.writeText(f, "!!", 0, 2))
    let back = Array.filled(32, Byte.truncate(0))
    -- Fills the window or stops at the end of the file.
    print(FD.readAt(f, 0, back, 0, 32))
    print(FD.readAt(f, 7, back, 0, 3))
    print(String.fromBytes(Array.slice(back, 0, 3)))
    print(FD.writeAt(f, 0, text, 7, 12))
    -- Neither positional call moved the position, which is at the end.
    print(FD.read(f, back, 0, 32))
}
""", tmp_path)
    assert out == ["12", "2", "14", "3", "Some(wor)", "5", "0"]
    assert (tmp_path / "made").read_bytes() == b"world, world!!"


def test_close_is_idempotent_and_every_later_operation_answers_closed(tmp_path):
    out = lines("""
fun main() {
    let f = created("made")
    let room = Array.filled(4, Byte.truncate(0))
    print(FD.close(f))
    print(FD.close(f))
    print(FD.read(f, room, 0, 4) == FD.closed)
    print(FD.write(f, room, 0, 4) == FD.closed)
    print(FD.readAt(f, 0, room, 0, 4) == FD.closed)
    print(FD.writeAt(f, 0, room, 0, 4) == FD.closed)
    -- With nothing using it, closing closed the descriptor.
    print(IO.fcntl(FD.number(f), Sys.getDescriptorFlags) == -Sys.ebadf)
}
""", tmp_path)
    assert out == ["0", "0", "True", "True", "True", "True", "True"]


@pytest.mark.parametrize("workers", ["1", "4"])
def test_writes_from_many_tasks_each_go_out_whole(tmp_path, workers):
    """Each task writes blocks of one letter; whatever the order, no block is
    split by another task's."""
    lines("""
fun main() {
    let f = created("made")
    Task.runScope(do {
        let tasks = []
        for var k = 0; k < 8; k = k + 1 {
            let block = Array.filled(4096, Byte.truncate(97 + k))
            let t = Task.spawn(fun() {
                for var i = 0; i < 50; i = i + 1 {
                    let _ = FD.write(f, block, 0, len(block))
                    Task.yield()
                }
            })?
            Vec.push(tasks, t)
        }
        for t in tasks { Task.join(t) }
        pure({})
    })
    print(FD.close(f))
}
""", tmp_path, workers)
    data = (tmp_path / "made").read_bytes()
    assert len(data) == 8 * 50 * 4096
    blocks = [data[i:i + 4096] for i in range(0, len(data), 4096)]
    assert all(block == block[:1] * 4096 for block in blocks)
    assert sorted(set(block[:1] for block in blocks)) == [bytes([97 + k]) for k in range(8)]


def test_reads_from_many_tasks_take_the_file_in_whole_pieces(tmp_path):
    """Sequential reads share the position: between them, the tasks read the
    file exactly once."""
    (tmp_path / "data").write_bytes(bytes(range(256)) * 4096)
    out = lines("""
fun main() {
    let f = FD.fd(IO.open("data", Sys.readOnly, 0))
    let total = Sync.atomicInt(0)
    let sum = Sync.atomicInt(0)
    Task.runScope(do {
        let tasks = []
        for var k = 0; k < 8; k = k + 1 {
            let t = Task.spawn(fun() {
                let into = Array.filled(1000, Byte.truncate(0))
                while True {
                    let n = FD.read(f, into, 0, len(into))
                    if n <= 0 { break }
                    var s = 0
                    for var i = 0; i < n; i = i + 1 { s = s + Byte.toInt(into[i]) }
                    let _ = Sync.addAndGet(total, n)
                    let _ = Sync.addAndGet(sum, s)
                }
            })?
            Vec.push(tasks, t)
        }
        for t in tasks { Task.join(t) }
        pure({})
    })
    print(Sync.load(total))
    print(Sync.load(sum))
}
""", tmp_path, "4")
    assert out == [str(256 * 4096), str(sum(range(256)) * 4096)]


def test_close_racing_a_blocked_read_does_not_release_the_number(tmp_path):
    """A read is blocked in the kernel on an empty FIFO when another task
    closes its `Fd`. The number stays the FIFO's until the read finishes: a
    new descriptor gets another number, and a write through the old one
    reaches the read. The read's next call answers `closed`, and its release
    closes the number."""
    os.mkfifo(tmp_path / "fifo")
    out = lines("""
fun main() {
    -- Read and write, so the open does not wait for a writer.
    let number = IO.open("fifo", Sys.readWrite, 0)
    let f = FD.fd(number)
    let started = Sync.atomicInt(0)
    let results = Task.runScope(do {
        let reader = Task.spawn(fun() {
            Sync.store(started, 1)
            let into = Array.filled(16, Byte.truncate(0))
            let first = FD.read(f, into, 0, 16)
            let second = FD.read(f, into, 0, 16)
            (first, second == FD.closed)
        })?
        let closer = Task.spawn(fun() {
            while Sync.load(started) == 0 { Task.yield() }
            -- Long enough for the reader to be inside read(2).
            let until = Time.monotonic() + 200_000_000
            while Time.monotonic() < until {}
            let closing = FD.close(f)
            let other = IO.open("fifo", Sys.readWrite, 0)
            let wrote = IO.write(number, bytes("ping"), 0, 4)
            (closing, other != number, wrote)
        })?
        pure((Task.join(reader), Task.join(closer)))
    })
    print(results)
    print(IO.fcntl(number, Sys.getDescriptorFlags) == -Sys.ebadf)
}
""", tmp_path, "4")
    assert out == ["((4, True), (0, True, 4))", "True"]


def test_a_task_waiting_for_a_lock_is_woken_by_close(tmp_path):
    """One task holds the read lock, blocked on an empty FIFO; a second waits
    for the lock, suspended. Closing wakes the second, which answers
    `closed` without reading."""
    os.mkfifo(tmp_path / "fifo")
    out = lines("""
fun main() {
    let number = IO.open("fifo", Sys.readWrite, 0)
    let f = FD.fd(number)
    let started = Sync.atomicInt(0)
    let results = Task.runScope(do {
        let holder = Task.spawn(fun() {
            Sync.store(started, 1)
            let into = Array.filled(16, Byte.truncate(0))
            FD.read(f, into, 0, 16)
        })?
        let waiter = Task.spawn(fun() {
            while Sync.load(started) == 0 { Task.yield() }
            let until = Time.monotonic() + 100_000_000
            while Time.monotonic() < until {}
            let into = Array.filled(16, Byte.truncate(0))
            FD.read(f, into, 0, 16) == FD.closed
        })?
        let closer = Task.spawn(fun() {
            while Sync.load(started) == 0 { Task.yield() }
            let until = Time.monotonic() + 300_000_000
            while Time.monotonic() < until {}
            let closing = FD.close(f)
            let _ = IO.write(number, bytes("ping"), 0, 4)
            closing
        })?
        pure((Task.join(holder), Task.join(waiter), Task.join(closer)))
    })
    print(results)
}
""", tmp_path, "4")
    assert out == ["(4, True, 0)"]
