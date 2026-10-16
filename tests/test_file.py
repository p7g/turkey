"""`System.File`: open files with their mode in the type, positional reads and
writes beside sequential ones, and whole-file reads and writes by path."""

from __future__ import annotations

import os
import re

import pytest

from tests.lang import fails, run

HEADER = """\
import System.File as File
import System.IO as IO
import System.Task as Task

fun report(r : Either IO.IOError a) -> String = match r {
    Right(_) -> "ok"
    Left(e) -> show(e.kind) + ": " + message(e)
}

fun must(r : Either IO.IOError a) -> a = match r {
    Right(x) -> x
    Left(e) -> error(message(e))
}
"""


def lines(src: str, cwd, env: dict[str, str] | None = None,
          flags: tuple[str, ...] = ()) -> list[str]:
    result = run(HEADER + src, cwd=cwd, env=env, flags=flags)
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


def test_open_failures_say_what_and_where(tmp_path):
    (tmp_path / "plain").write_bytes(b"x")
    os.mkdir(tmp_path / "dir")
    out = lines("""
fun main() {
    print(report(File.openRead("missing")))
    print(report(File.openRead("plain/below")))
    print(report(File.create("plain", File.Exclusive)))
    print(report(File.create("dir", File.Truncate)))
    print(report(File.openRead("plain")))
}
""", tmp_path)
    assert out == [
        "NotFound: open missing: no such file or directory",
        "NotDirectory: open plain/below: not a directory",
        "AlreadyExists: open plain: file exists",
        "IsDirectory: open dir: is a directory",
        "ok",
    ]


def test_write_read_append_and_close(tmp_path):
    out = lines("""
fun main() {
    let w = must(File.create("made", File.Truncate))
    print(File.path(w))
    print(report(IO.writeText(w, "first\\n")))
    print(report(File.close(w)))
    -- Closing again does nothing; anything else answers Closed.
    print(report(File.close(w)))
    print(report(IO.writeText(w, "late")))
    let a = must(File.openAppend("made"))
    print(report(IO.writeFrom(a, IO.bytesOf("second\\n"))))
    print(report(File.close(a)))
    let r = must(File.openRead("made"))
    let into = IO.buffer(64)
    print(IO.readInto(r, into))
    print(String.fromBytes(into))
    print(IO.readInto(r, into))
    print(report(File.close(r)))
}
""", tmp_path)
    assert out == ["made", "ok", "ok", "ok",
                   "Closed: write made: file already closed", "ok", "ok",
                   "Right(13)", "Some(first", "second", ")", "Right(0)", "ok"]


def test_positional_reads_and_writes_leave_the_position_alone(tmp_path):
    (tmp_path / "data").write_bytes(b"0123456789")
    out = lines("""
fun main() {
    let f = must(File.openReadWrite("data"))
    let into = IO.buffer(4)
    print(File.readAt(f, 3, into))
    print(String.fromBytes(into))
    IO.clear(into)
    -- Fewer than the room only at the end of the file.
    print(File.readAt(f, 8, into))
    print(String.fromBytes(into))
    print(report(File.writeAt(f, 2, IO.bytesOf("ab"))))
    IO.clear(into)
    print(IO.readInto(f, into))
    print(String.fromBytes(into))
}
""", tmp_path)
    assert out == ["Right(4)", "Some(3456)", "Right(2)", "Some(89)", "ok",
                   "Right(4)", "Some(01ab)"]
    assert (tmp_path / "data").read_bytes() == b"01ab456789"


def test_readAt_on_a_fifo_is_not_seekable(tmp_path):
    os.mkfifo(tmp_path / "fifo")
    out = lines("""
fun main() {
    -- Read and write, so that the open does not wait for a writer.
    let f = must(File.openReadWrite("fifo"))
    print(report(File.readAt(f, 0, IO.buffer(4))))
    print(report(File.writeAt(f, 0, IO.bytesOf("x"))))
}
""", tmp_path)
    assert out == ["NotSeekable: readAt fifo: illegal seek",
                   "NotSeekable: writeAt fifo: illegal seek"]


@pytest.mark.parametrize("call", [
    "IO.writeFrom(must(File.openRead(\"x\")), IO.bytesOf(\"y\"))",
    "IO.writeText(must(File.openRead(\"x\")), \"y\")",
    "File.writeAt(must(File.openRead(\"x\")), 0, IO.bytesOf(\"y\"))",
])
def test_writing_through_a_read_only_file_is_a_type_error(call):
    message = fails(HEADER + f"fun main() {{ let _ = {call} }}\n")
    assert re.search(r"no instance for 'CanWrite(At)? ReadOnly'", message), message


def test_writeAt_on_an_appending_file_is_a_type_error():
    message = fails(HEADER + """
fun main() { let _ = File.writeAt(must(File.openAppend("x")), 0, IO.bytesOf("y")) }
""")
    assert message == "no instance for 'CanWriteAt Appending'"


def test_reading_a_write_only_file_is_a_type_error():
    message = fails(HEADER + """
fun main() { let _ = IO.readInto(must(File.create("x", File.Truncate)), IO.buffer(4)) }
""")
    assert message == "no instance for 'CanRead WriteOnly'"


def test_use_closes_the_file_when_its_body_returns(tmp_path):
    (tmp_path / "data").write_bytes(b"a\nb\nc\n")
    out = lines("""
fun main() {
    var kept = None
    let counted = File.use(File.openRead("data"), fun(f) {
        kept = Some(f)
        var n = 0
        for line in IO.lines(IO.buffered(f, 64)) { n = n + 1 }
        Right(n)
    })
    print(counted)
    match kept {
        Some(f) -> print(report(IO.readInto(f, IO.buffer(4))))
        None -> {}
    }
    print(report(File.use(File.openRead("missing"), fun(f) = Right(1))))
}
""", tmp_path)
    assert out == ["Right(3)", "Closed: read data: file already closed",
                   "NotFound: open missing: no such file or directory"]


def test_whole_files_by_path(tmp_path):
    (tmp_path / "bad").write_bytes(b"ok\xff")
    out = lines("""
fun main() {
    print(report(File.writeText("made", "héllo\\n")))
    print(File.readText("made"))
    match File.readAll("made") {
        Right(b) -> print(IO.filled(b))
        Left(e) -> print(message(e))
    }
    print(File.readText("bad"))
    print(File.readText("missing"))
    print(report(File.writeAll("copy", IO.bytesOf("bytes"))))
    print(File.readText("copy"))
    -- A device that fstat gives no size for, read until it ends.
    match File.readAll("/dev/null") {
        Right(b) -> print(IO.filled(b))
        Left(e) -> print(message(e))
    }
}
""", tmp_path)
    assert out == ["ok", "Right(héllo", ")", "7", "Left(Left(InvalidUTF8))",
                   "Left(Right(open missing: no such file or directory))",
                   "ok", "Right(bytes)", "0"]


def test_a_layers_error_reads_as_whichever_side_failed(tmp_path):
    (tmp_path / "bad").write_bytes(b"ok\xff")
    (tmp_path / "short").write_bytes(b"ab")
    out = lines("""
fun main() {
    for path in ["bad", "missing"] {
        match File.readText(path) {
            Right(_) -> print("text")
            Left(e) -> print(message(e))
        }
    }
    match IO.readExact(must(File.openRead("short")), IO.buffer(4), 4) {
        Right(_) -> print("four bytes")
        Left(e) -> print(message(e))
    }
}
""", tmp_path)
    assert out == ["invalid UTF-8", "open missing: no such file or directory",
                   "the input ended early"]


def test_readAll_reads_a_large_file_whole(tmp_path):
    data = bytes(range(256)) * 5000
    (tmp_path / "big").write_bytes(data)
    out = lines("""
fun main() {
    let b = must(File.readAll("big"))
    var sum = 0
    for x in b { sum = sum + Byte.toInt(x) }
    print((IO.filled(b), sum))
    let g = must(IO.readAll(must(File.openRead("big"))))
    print(IO.filled(g))
}
""", tmp_path)
    assert out == [f"({len(data)}, {sum(data)})", str(len(data))]


def test_eight_tasks_read_disjoint_ranges_at_once(tmp_path):
    """Positional reads take no lock: eight tasks each read their eighth of
    one `File` with `readAt`, in pieces, at `TURKEY_WORKERS=8`."""
    data = bytes((i * 7) % 251 for i in range(8 * 98_304))
    (tmp_path / "data").write_bytes(data)
    out = lines("""
fun main() {
    let f = must(File.openRead("data"))
    -- 24 reads of 4096 bytes each.
    let part = 98304
    let sums = Task.runScope(do {
        let tasks = []
        for var k = 0; k < 8; k = k + 1 {
            let start = k * part
            let t = Task.spawn(fun() {
                let into = IO.buffer(4096)
                var sum = 0
                var at = start
                while at < start + part {
                    IO.clear(into)
                    let n = must(File.readAt(f, at, into))
                    for x in into { sum = sum + Byte.toInt(x) }
                    at = at + n
                }
                sum
            })?
            Vec.push(tasks, t)
        }
        var all = []
        for t in tasks { Vec.push(all, Task.join(t)) }
        pure(all)
    })
    for s in sums { print(s) }
}
""", tmp_path, env={"TURKEY_WORKERS": "8"})
    part = 98_304
    assert out == [str(sum(data[k * part:(k + 1) * part])) for k in range(8)]


def allocations(stderr: str) -> int:
    found = re.search(r"\[gc\] collections \d+, allocations (\d+)", stderr)
    assert found, stderr
    return int(found.group(1))


def test_readLineInto_allocates_nothing_per_line(tmp_path):
    """The same loop over 1,000 and over 100,000 lines: the extra lines cost
    a few allocations per buffer refill, and none per line."""
    (tmp_path / "few").write_bytes(b"a line of text\n" * 1_000)
    (tmp_path / "many").write_bytes(b"a line of text\n" * 100_000)
    src = """
import System.Env as Env

fun main() {
    let f = must(File.openRead(Env.args()[0]))
    let b = IO.buffered(f, IO.defaultCapacity)
    let line = IO.buffer(256)
    var n = 0
    while True {
        match IO.readLineInto(b, line) {
            IO.Got -> { n = n + 1 }
            IO.Partial -> {}
            IO.End -> break
            IO.Failed(e) -> error(message(e))
        }
        IO.clear(line)
    }
    print(n)
}
"""
    counts = []
    for name, expected in (("few", 1_000), ("many", 100_000)):
        result = run(HEADER + src, cwd=tmp_path, args=(str(tmp_path / name),),
                     flags=("--gc-stats",))
        assert result.code == 0, result.stderr
        assert result.stdout == f"{expected}\n"
        counts.append(allocations(result.stderr))
    # 1.5 MB is 23 refills of 64 KiB.
    assert counts[1] - counts[0] < 100, counts
