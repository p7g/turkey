"""The I/O floor: `Turkey.IO`'s system calls, each answering a count or a
descriptor or the negated `errno`, and its checked copy between byte arrays.

Each test runs a program against real files and checks what it printed
against what Python sees of the same files, so the per-target constants in
`Turkey.Sys` -- the `open` flags, the error numbers, where `struct stat` keeps
the mode and the size -- are checked on whichever target the suite runs on.
"""

from __future__ import annotations

import errno
import fcntl
import os
import stat

import pytest

from tests import bootc
from tests.lang import panic_message, panics, run

HEADER = """\
import Turkey.IO as IO
import Turkey.Sys as Sys

fun bytes(text : String) -> Array Byte = String.toBytes(text)
"""


@pytest.fixture(autouse=True)
def internal(monkeypatch):
    """`Turkey.*` is the library's own; a test program may import these two
    only by name."""
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       os.environ[bootc.INTERNAL_IMPORTS] + ",Turkey.IO,Turkey.Sys")


def lines(src: str, cwd) -> list[str]:
    result = run(HEADER + src, cwd=cwd)
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


def test_open_create_write_read_and_close(tmp_path):
    out = lines("""
fun main() {
    let flags = Int.or(Sys.readWrite, Int.or(Sys.create(), Sys.exclusive()))
    let fd = IO.open("made", flags, 0o640)
    print(fd >= 0)
    let text = bytes("hello, world")
    -- From the middle of the array: "lo, wor".
    print(IO.write(fd, text, 3, 10))
    -- Exclusive: it exists now.
    print(IO.open("made", flags, 0o640) == 0 - Sys.eexist)
    let back = Array.filled(16, Byte.truncate(0))
    print(IO.readAt(fd, 1, back, 4, 16))
    print(String.fromBytes(Vec.fromArray(Array.slice(back, 4, 10))))
    -- The position is past what was written, and `readAt` did not move it.
    print(IO.read(fd, back, 0, 16))
    print(IO.writeAt(fd, 0, text, 0, 2))
    print(IO.close(fd))
    print(IO.close(fd) == 0 - Sys.ebadf)
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
    print(IO.open("missing", Sys.readOnly, 0))
    print(IO.open("plain/below", Sys.readOnly, 0))
    let fd = IO.open("plain", Sys.readOnly, 0)
    print(IO.isatty(fd))
    print(IO.write(fd, bytes("y"), 0, 1))
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
    let fd = IO.open("sized", Sys.readOnly, 0)
    print(IO.fstat(fd, stat))
    print(IO.statSize(stat))
    print(IO.statMode(stat))
    print(Sys.fileType(IO.statMode(stat)) == Sys.regularFile)
    let dir = IO.open("folder", Sys.readOnly, 0)
    print(IO.fstat(dir, stat))
    print(Sys.fileType(IO.statMode(stat)) == Sys.directory)
}
""", tmp_path)
    assert out == ["0", "10000", str(path.stat().st_mode), "True", "0", "True"]


def test_fcntl_sets_and_reads_descriptor_flags(tmp_path):
    (tmp_path / "plain").write_bytes(b"x")
    out = lines("""
fun main() {
    let fd = IO.open("plain", Sys.readOnly, 0)
    print(IO.fcntl(fd, Sys.getDescriptorFlags))
    print(IO.fcntlWith(fd, Sys.setDescriptorFlags, Sys.descriptorCloseOnExec))
    print(IO.fcntl(fd, Sys.getDescriptorFlags))
    let status = IO.fcntl(fd, Sys.getStatusFlags)
    print(IO.fcntlWith(fd, Sys.setStatusFlags, Int.or(status, Sys.nonBlocking())))
    print(Int.and(IO.fcntl(fd, Sys.getStatusFlags), Sys.nonBlocking()) != 0)
}
""", tmp_path)
    assert out == ["0", "0", "1", "0", "True"]


def test_open_with_close_on_exec(tmp_path):
    (tmp_path / "plain").write_bytes(b"x")
    out = lines("""
fun main() {
    let fd = IO.open("plain", Int.or(Sys.readOnly, Sys.closeOnExec()), 0)
    print(IO.fcntl(fd, Sys.getDescriptorFlags))
}
""", tmp_path)
    assert out == ["1"]


def test_a_path_with_a_nul_is_refused(tmp_path):
    result = panics(HEADER + """
fun main() { let _ = IO.open("a\\u{0}b", Sys.readOnly, 0) }
""")
    assert panic_message(result) == "a path cannot contain a NUL byte"


def test_copy_moves_a_window(tmp_path):
    out = lines("""
fun main() {
    let into = bytes("..........")
    IO.copy(into, 2, bytes("abcdefghijklmnopqrstuvwxyz0123456789"), 20, 26)
    print(String.fromBytes(Vec.fromArray(into)))
    -- Toward the start of one array, overlapping: a partial line moved to the
    -- front of a buffer.
    let line = bytes("0123456789")
    IO.copy(line, 0, line, 3, 10)
    print(String.fromBytes(Vec.fromArray(line)))
    -- Nothing at all, at the very end.
    IO.copy(line, 10, line, 10, 10)
}
""", tmp_path)
    assert out == ["Some(..uvwxyz..)", "Some(3456789789)"]


def test_copy_toward_the_end_of_one_array_is_a_memmove(tmp_path):
    """The bytes arrive as they were before the copy began."""
    out = lines("""
fun main() {
    let line = bytes("0123456789")
    IO.copy(line, 3, line, 0, 7)
    print(String.fromBytes(Vec.fromArray(line)))
}
""", tmp_path)
    assert out == ["Some(0120123456)"]


@pytest.mark.parametrize("call", [
    "IO.copy(bytes(\"abc\"), 0, bytes(\"abc\"), 2, 1)",
    "IO.copy(bytes(\"abc\"), 0, bytes(\"abc\"), -1, 1)",
    "{ let _ = IO.read(0, bytes(\"abc\"), 1, 4) }",
])
def test_a_window_outside_its_array_panics(call):
    result = panics(HEADER + f"fun main() {{ {call} }}\n")
    assert "is outside an array of length" in panic_message(result)


def test_a_copy_past_the_targets_end_panics():
    result = panics(HEADER + "fun main() { IO.copy(bytes(\"abc\"), 1, bytes(\"abc\"), 0, 3) }\n")
    assert "the window 1 to 4 is outside an array of length 3" in panic_message(result)


# -- the directory calls ---------------------------------------------------------
#
# `struct stat`'s modification time, `struct dirent`'s type and name, and the
# `AT_*`, `O_DIRECTORY`/`O_NOFOLLOW` and `LOCK_*` values, each checked against
# what Python sees of the same files on whichever target the suite runs on.


def test_constants_python_also_has(tmp_path):
    out = lines("""
fun main() {
    print(Sys.directoryOnly())
    print(Sys.noFollow())
    print(Sys.lockExclusive)
    print(Sys.lockNonBlocking)
    print(Sys.lockUnlock)
    print(Sys.enotempty())
    print(Sys.eloop())
    print(Sys.exdev)
    print(Sys.enametoolong())
    print(Sys.eilseq())
}
""", tmp_path)
    assert out == [str(v) for v in [
        os.O_DIRECTORY, os.O_NOFOLLOW, fcntl.LOCK_EX, fcntl.LOCK_NB, fcntl.LOCK_UN,
        errno.ENOTEMPTY, errno.ELOOP, errno.EXDEV, errno.ENAMETOOLONG, errno.EILSEQ]]


def test_statAt_reads_the_modification_time_and_a_link_itself(tmp_path):
    (tmp_path / "plain").write_bytes(b"abc")
    os.utime(tmp_path / "plain", ns=(1_000_000_123, 1_700_000_000_987_654_321))
    os.symlink("plain", tmp_path / "link")
    out = lines("""
fun main() {
    let stat = Array.filled(Sys.statBytes(), Byte.truncate(0))
    let here = Sys.atWorkingDirectory()
    print(IO.statAt(here, IO.cPath("link"), stat, 0))
    print(IO.statModified(stat))
    print(IO.statSize(stat))
    print(IO.statAt(here, IO.cPath("link"), stat, Sys.atSymlinkNoFollow()))
    print(IO.statMode(stat))
    print(IO.statAt(here, IO.cPath("missing"), stat, 0))
}
""", tmp_path)
    assert out == ["0", "1700000000987654321", "3", "0",
                   str(os.lstat(tmp_path / "link").st_mode), str(-errno.ENOENT)]


def test_open_make_remove_and_rename_relative_to_a_directory(tmp_path):
    os.mkdir(tmp_path / "base")
    os.symlink("base", tmp_path / "link")
    out = lines("""
fun main() {
    let here = Sys.atWorkingDirectory()
    let only = Int.or(Sys.readOnly, Sys.directoryOnly())
    let dir = IO.openAt(here, IO.cPath("base"), only, 0)
    print(dir >= 0)
    -- Refused: Linux says ELOOP, Darwin ENOTDIR.
    let link = IO.openAt(here, IO.cPath("link"), Int.or(only, Sys.noFollow()), 0)
    print(link == -Sys.eloop() || link == -Sys.enotdir)
    print(IO.makeDirectoryAt(dir, IO.cPath("sub"), 0o750))
    print(IO.makeDirectoryAt(dir, IO.cPath("sub"), 0o750) == -Sys.eexist)
    let made = Int.or(Sys.writeOnly, Sys.create())
    print(IO.close(IO.openAt(dir, IO.cPath("sub/file"), made, 0o600)))
    print(IO.removeAt(dir, IO.cPath("sub"), Sys.atRemoveDirectory()) == -Sys.enotempty())
    print(IO.renameAt(dir, IO.cPath("sub/file"), here, IO.cPath("moved")))
    print(IO.removeAt(dir, IO.cPath("sub"), Sys.atRemoveDirectory()))
    print(IO.removeAt(here, IO.cPath("moved"), 0))
    print(IO.close(dir))
}
""", tmp_path)
    assert out == ["True", "True", "0", "True", "0", "True", "0", "0", "0", "0"]
    assert sorted(os.listdir(tmp_path / "base")) == []
    assert sorted(os.listdir(tmp_path)) == ["base", "link"]


def test_readdir_reads_each_name_and_type(tmp_path):
    os.mkdir(tmp_path / "d")
    (tmp_path / "d" / "file").write_bytes(b"")
    os.mkdir(tmp_path / "d" / "sub")
    os.symlink("file", tmp_path / "d" / "link")
    os.mkfifo(tmp_path / "d" / "fifo")
    (tmp_path / "d" / ("long" * 40)).write_bytes(b"")
    out = lines("""
fun main() {
    let fd = IO.openAt(Sys.atWorkingDirectory(), IO.cPath("d"),
                       Int.or(Sys.readOnly, Sys.directoryOnly()), 0)
    let stream = IO.openStream(fd)
    while True {
        let entry = IO.readEntry(stream)
        if entry <= 0 {
            print(entry)
            break
        }
        let name = IO.entryName(entry)
        let text = String.fromBytes(Vec.fromArray(Array.slice(name, 0, len(name) - 1)))
        print(show(text) + " " + show(IO.entryType(entry)))
    }
    print(IO.closeStream(stream))
}
""", tmp_path)
    assert out[-2:] == ["0", "0"]
    seen = {}
    for line in out[:-2]:
        name, kind = line.rsplit(" ", 1)
        seen[name.removeprefix("Some(").removesuffix(")")] = int(kind)
    # `d_type` against Python's reading of the same directory.
    expected = {".": 4, "..": 4}
    for entry in os.scandir(tmp_path / "d"):
        mode = entry.stat(follow_symlinks=False).st_mode
        expected[entry.name] = stat.S_IFMT(mode) >> 12
    assert seen == expected


def test_flock_keeps_a_second_open_file_out(tmp_path):
    out = lines("""
fun main() {
    let flags = Int.or(Sys.readWrite, Sys.create())
    let a = IO.open("lock", flags, 0o600)
    let b = IO.open("lock", flags, 0o600)
    print(IO.flock(a, Sys.lockExclusive))
    print(IO.flock(b, Int.or(Sys.lockExclusive, Sys.lockNonBlocking)) == -Sys.eagain())
    print(IO.flock(a, Sys.lockUnlock))
    print(IO.flock(b, Int.or(Sys.lockExclusive, Sys.lockNonBlocking)))
}
""", tmp_path)
    assert out == ["0", "True", "0", "0"]


def test_mkdtemp_fills_in_the_template(tmp_path):
    out = lines("""
fun main() {
    let template = IO.cPath("dirXXXXXX")
    print(IO.makeTemporaryDirectory(template))
    print(String.fromBytes(Vec.fromArray(Array.slice(template, 0, 3))))
}
""", tmp_path)
    assert out == ["0", "Some(dir)"]
    made = [p for p in os.listdir(tmp_path) if p.startswith("dir")]
    assert len(made) == 1 and len(made[0]) == 9 and (tmp_path / made[0]).is_dir()
