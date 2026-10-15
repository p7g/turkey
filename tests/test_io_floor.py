"""The I/O floor: `Turkey.Io`'s system calls, each answering a count or a
descriptor or the negated `errno`, and its checked copy between byte arrays.

Each test runs a program against real files and checks what it printed
against what Python sees of the same files, so the per-target constants in
`Turkey.Sys` -- the `open` flags, the error numbers, where `struct stat` keeps
the mode and the size -- are checked on whichever target the suite runs on.
"""

from __future__ import annotations

import errno
import os
import stat

import pytest

from tests import bootc
from tests.lang import panic_message, panics, run

HEADER = """\
import Turkey.Io as Io
import Turkey.Sys as Sys

fun bytes(text : String) -> Array Byte = String.toBytes(text)
"""


@pytest.fixture(autouse=True)
def internal(monkeypatch):
    """`Turkey.*` is the library's own; a test program may import these two
    only by name."""
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       os.environ[bootc.INTERNAL_IMPORTS] + ",Turkey.Io,Turkey.Sys")


def lines(src: str, cwd) -> list[str]:
    result = run(HEADER + src, cwd=cwd)
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


def test_open_create_write_read_and_close(tmp_path):
    out = lines("""
fun main() {
    let flags = Int.or(Sys.readWrite, Int.or(Sys.create(), Sys.exclusive()))
    let fd = Io.open("made", flags, 0o640)
    print(fd >= 0)
    let text = bytes("hello, world")
    -- From the middle of the array: "lo, wor".
    print(Io.write(fd, text, 3, 10))
    -- Exclusive: it exists now.
    print(Io.open("made", flags, 0o640) == 0 - Sys.eexist)
    let back = Array.filled(16, Byte.truncate(0))
    print(Io.readAt(fd, 1, back, 4, 16))
    print(String.fromBytes(Vec.fromArray(Array.slice(back, 4, 10))))
    -- The position is past what was written, and `readAt` did not move it.
    print(Io.read(fd, back, 0, 16))
    print(Io.writeAt(fd, 0, text, 0, 2))
    print(Io.close(fd))
    print(Io.close(fd) == 0 - Sys.ebadf)
}
""", tmp_path)
    assert out == ["True", "7", "True", "6", "Some(o, wor)", "0", "2", "0", "True"]
    assert (tmp_path / "made").read_bytes() == b"he, wor"
    mask = os.umask(0)
    os.umask(mask)
    assert stat.S_IMODE((tmp_path / "made").stat().st_mode) == 0o640 & ~mask


def test_a_failure_is_the_negated_errno(tmp_path):
    (tmp_path / "plain").write_bytes(b"x")
    out = lines("""
fun main() {
    print(Io.open("missing", Sys.readOnly, 0))
    print(Io.open("plain/below", Sys.readOnly, 0))
    let fd = Io.open("plain", Sys.readOnly, 0)
    print(Io.isatty(fd))
    print(Io.write(fd, bytes("y"), 0, 1))
}
""", tmp_path)
    assert out == [str(-errno.ENOENT), str(-errno.ENOTDIR), str(-errno.ENOTTY),
                   str(-errno.EBADF)]


def test_fstat_reads_the_mode_and_the_size(tmp_path):
    path = tmp_path / "sized"
    path.write_bytes(b"0123456789" * 1000)
    os.mkdir(tmp_path / "folder")
    out = lines("""
fun main() {
    let stat = Array.filled(Sys.statBytes(), Byte.truncate(0))
    let fd = Io.open("sized", Sys.readOnly, 0)
    print(Io.fstat(fd, stat))
    print(Io.statSize(stat))
    print(Io.statMode(stat))
    print(Sys.fileType(Io.statMode(stat)) == Sys.regularFile)
    let dir = Io.open("folder", Sys.readOnly, 0)
    print(Io.fstat(dir, stat))
    print(Sys.fileType(Io.statMode(stat)) == Sys.directory)
}
""", tmp_path)
    assert out == ["0", "10000", str(path.stat().st_mode), "True", "0", "True"]


def test_fcntl_sets_and_reads_descriptor_flags(tmp_path):
    (tmp_path / "plain").write_bytes(b"x")
    out = lines("""
fun main() {
    let fd = Io.open("plain", Sys.readOnly, 0)
    print(Io.fcntl(fd, Sys.getDescriptorFlags))
    print(Io.fcntlWith(fd, Sys.setDescriptorFlags, Sys.descriptorCloseOnExec))
    print(Io.fcntl(fd, Sys.getDescriptorFlags))
    let status = Io.fcntl(fd, Sys.getStatusFlags)
    print(Io.fcntlWith(fd, Sys.setStatusFlags, Int.or(status, Sys.nonBlocking())))
    print(Int.and(Io.fcntl(fd, Sys.getStatusFlags), Sys.nonBlocking()) != 0)
}
""", tmp_path)
    assert out == ["0", "0", "1", "0", "True"]


def test_open_with_close_on_exec(tmp_path):
    (tmp_path / "plain").write_bytes(b"x")
    out = lines("""
fun main() {
    let fd = Io.open("plain", Int.or(Sys.readOnly, Sys.closeOnExec()), 0)
    print(Io.fcntl(fd, Sys.getDescriptorFlags))
}
""", tmp_path)
    assert out == ["1"]


def test_a_path_with_a_nul_is_refused(tmp_path):
    result = panics(HEADER + """
fun main() { let _ = Io.open("a\\u{0}b", Sys.readOnly, 0) }
""")
    assert panic_message(result) == "a path cannot contain a NUL byte"


def test_copy_moves_a_window(tmp_path):
    out = lines("""
fun main() {
    let into = bytes("..........")
    Io.copy(into, 2, bytes("abcdefghijklmnopqrstuvwxyz0123456789"), 20, 26)
    print(String.fromBytes(Vec.fromArray(into)))
    -- Toward the start of one array, overlapping: a partial line moved to the
    -- front of a buffer.
    let line = bytes("0123456789")
    Io.copy(line, 0, line, 3, 10)
    print(String.fromBytes(Vec.fromArray(line)))
    -- Nothing at all, at the very end.
    Io.copy(line, 10, line, 10, 10)
}
""", tmp_path)
    assert out == ["Some(..uvwxyz..)", "Some(3456789789)"]


def test_copy_toward_the_end_of_one_array_is_a_memmove(tmp_path):
    """The bytes arrive as they were before the copy began."""
    out = lines("""
fun main() {
    let line = bytes("0123456789")
    Io.copy(line, 3, line, 0, 7)
    print(String.fromBytes(Vec.fromArray(line)))
}
""", tmp_path)
    assert out == ["Some(0120123456)"]


@pytest.mark.parametrize("call", [
    "Io.copy(bytes(\"abc\"), 0, bytes(\"abc\"), 2, 1)",
    "Io.copy(bytes(\"abc\"), 0, bytes(\"abc\"), -1, 1)",
    "{ let _ = Io.read(0, bytes(\"abc\"), 1, 4) }",
])
def test_a_window_outside_its_array_panics(call):
    result = panics(HEADER + f"fun main() {{ {call} }}\n")
    assert "is outside an array of length" in panic_message(result)


def test_a_copy_past_the_targets_end_panics():
    result = panics(HEADER + "fun main() { Io.copy(bytes(\"abc\"), 1, bytes(\"abc\"), 0, 3) }\n")
    assert "out of bounds" in panic_message(result)
