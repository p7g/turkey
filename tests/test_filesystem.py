"""`System.File`'s file system operations: what a path names (`Stat`),
directories as handles and the operations relative to one, listing, making
and removing trees, atomic rename, locks and temporary directories."""

from __future__ import annotations

import errno
import fcntl
import os
import stat
import subprocess

import pytest

from tests import toolchain
from tests.lang import binary, run

HEADER = """\
import System.File as File
import System.IO as IO
import System.Time as Time

fun report(r : Either IO.IOError a) -> String = match r {
    Right(_) -> "ok"
    Left(e) -> show(e.kind) + ": " + message(e)
}

fun must(r : Either IO.IOError a) -> a = match r {
    Right(x) -> x
    Left(e) -> error(message(e))
}

fun names(r : Either IO.IOError (Array String)) -> String = match r {
    Right(xs) -> String.join(xs, " ")
    Left(e) -> show(e.kind) + ": " + message(e)
}
"""


def lines(src: str, cwd, env: dict[str, str] | None = None) -> list[str]:
    result = run(HEADER + src, cwd=cwd, env=env)
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


def test_a_stat_is_a_value(tmp_path):
    (tmp_path / "plain").write_bytes(b"x" * 1234)
    os.chmod(tmp_path / "plain", 0o640)
    os.mkdir(tmp_path / "dir")
    os.symlink("plain", tmp_path / "link")
    os.mkfifo(tmp_path / "fifo")
    out = lines("""
fun describe(r : Either IO.IOError File.Stat) -> String = match r {
    Right(s) -> show(s.kind) + " " + show(s.size) + " " + show(s.permissions) + " "
                + show(s.modified)
    Left(e) -> show(e.kind) + ": " + message(e)
}

fun kindOf(r : Either IO.IOError File.Stat) -> String = match r {
    Right(s) -> show(s.kind)
    Left(e) -> message(e)
}

fun main() {
    print(describe(File.metadata("plain")))
    print(describe(File.metadata("link")))
    print(kindOf(File.symlinkMetadata("link")))
    print(kindOf(File.metadata("dir")))
    print(kindOf(File.symlinkMetadata("fifo")))
    print(describe(File.metadata("missing")))
    let f = must(File.openRead("plain"))
    print(describe(File.stat(f)))
    print(report(File.close(f)))
    print(report(File.stat(f)))
    -- The clock `modified` is counted on.
    let s = must(File.metadata("plain"))
    print(Time.now() >= s.modified)
}
""", tmp_path)
    plain = os.stat(tmp_path / "plain")
    expect = f"Regular 1234 {0o640} {plain.st_mtime_ns}"
    assert out == [
        expect,
        expect,
        "Symlink",
        "Directory",
        "Fifo",
        "NotFound: stat missing: no such file or directory",
        expect,
        "ok",
        "Closed: stat plain: file already closed",
        "True",
    ]


def test_a_stat_compares_by_kind():
    out = run(HEADER + """
fun main() {
    print(File.Regular == File.Regular)
    print(File.Regular == File.Directory)
    print(File.BlockDevice)
}
""").stdout.split("\n")[:-1]
    assert out == ["True", "False", "BlockDevice"]


def test_list_is_sorted_and_leaves_out_the_dots(tmp_path):
    os.mkdir(tmp_path / "d")
    for name in ["b", "a", "C", "é", "a.txt", "aa"]:
        (tmp_path / "d" / name).write_bytes(b"")
    os.mkdir(tmp_path / "d" / "sub")
    os.mkdir(tmp_path / "empty")
    (tmp_path / "plain").write_bytes(b"")
    out = lines("""
fun main() {
    print(names(File.listPath("d")))
    print(names(File.listPath("empty")))
    print(names(File.listPath("plain")))
    print(names(File.listPath("missing")))
    -- Listing a `Dir` twice reads it from the start each time.
    let d = must(File.openDir("d"))
    print(names(File.list(d)))
    print(names(File.list(d)))
    print(report(File.closeDir(d)))
    print(names(File.list(d)))
}
""", tmp_path)
    everything = "C a a.txt aa b sub é"
    assert out == [
        everything,
        "",
        "NotDirectory: open plain: not a directory",
        "NotFound: open missing: no such file or directory",
        everything,
        everything,
        "ok",
        "Closed: list d: file already closed",
    ]


def test_list_fails_on_a_name_that_is_not_utf8(tmp_path):
    os.mkdir(tmp_path / "d")
    (tmp_path / "d" / "fine").write_bytes(b"")
    try:
        with open(os.path.join(os.fsencode(tmp_path / "d"), b"bad\xff"), "wb"):
            pass
    except OSError as e:
        if e.errno != errno.EILSEQ:
            raise
        pytest.skip("this file system takes only UTF-8 names")
    out = lines("""
fun main() { print(names(File.listPath("d"))) }
""", tmp_path)
    assert out == [f"Other({errno.EILSEQ}): list d: illegal byte sequence"]


def test_createDirAll_makes_the_missing_parents(tmp_path):
    (tmp_path / "plain").write_bytes(b"")
    out = lines("""
fun main() {
    print(report(File.createDirAll("a/b/c/")))
    -- Again: everything is there.
    print(report(File.createDirAll("a/b/c")))
    print(report(File.createDirAll("a")))
    print(report(File.createDirAll("plain/below")))
    print(report(File.createDirAll("plain")))
    print(report(File.createDir("x/y")))
    print(report(File.createDir("a")))
}
""", tmp_path)
    assert out == [
        "ok", "ok", "ok",
        "NotDirectory: createDir plain/below: not a directory",
        "AlreadyExists: createDir plain: file exists",
        "NotFound: createDir x/y: no such file or directory",
        "AlreadyExists: createDir a: file exists",
    ]
    assert (tmp_path / "a" / "b" / "c").is_dir()
    assert stat.S_IMODE(os.stat(tmp_path / "a" / "b").st_mode) == 0o777 & ~_umask()


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


def test_rename_replaces_in_one_step(tmp_path):
    (tmp_path / "old").write_bytes(b"old")
    (tmp_path / "staged").write_bytes(b"new")
    os.mkdir(tmp_path / "dir")
    out = lines("""
fun main() {
    print(report(File.rename("staged", "old")))
    print(report(File.rename("staged", "old")))
    print(report(File.rename("old", "dir/moved")))
}
""", tmp_path)
    assert out == ["ok", "NotFound: rename staged: no such file or directory", "ok"]
    assert sorted(os.listdir(tmp_path)) == ["dir"]
    assert (tmp_path / "dir" / "moved").read_bytes() == b"new"


def test_remove_a_file_a_directory_and_a_tree(tmp_path):
    (tmp_path / "plain").write_bytes(b"")
    os.makedirs(tmp_path / "tree" / "a" / "b")
    os.mkdir(tmp_path / "empty")
    (tmp_path / "tree" / "top").write_bytes(b"1")
    (tmp_path / "tree" / "a" / "b" / "deep").write_bytes(b"2")
    os.symlink("../top", tmp_path / "tree" / "a" / "link")
    os.mkfifo(tmp_path / "tree" / "fifo")
    out = lines("""
fun main() {
    print(report(File.removeFile("plain")))
    print(report(File.removeFile("plain")))
    print(report(File.removeDir("tree")))
    print(report(File.removeDir("empty")))
    print(report(File.removeTree("tree")))
    print(report(File.removeTree("tree")))
}
""", tmp_path)
    assert out == [
        "ok",
        "NotFound: remove plain: no such file or directory",
        "NotEmpty: removeDir tree: directory not empty",
        "ok",
        "ok",
        "NotFound: stat tree: no such file or directory",
    ]
    assert os.listdir(tmp_path) == []


def test_removeTree_does_not_follow_a_symlink_out_of_the_tree(tmp_path):
    outside = tmp_path / "outside"
    os.makedirs(outside / "inner")
    (outside / "keep").write_bytes(b"keep")
    (outside / "inner" / "keep").write_bytes(b"keep")
    os.makedirs(tmp_path / "tree" / "sub")
    os.symlink(outside, tmp_path / "tree" / "sub" / "escape")
    os.symlink(outside, tmp_path / "tree" / "escape")
    # A link at the top is removed, and what it points to is not.
    os.symlink(outside, tmp_path / "toplink")
    out = lines("""
fun main() {
    print(report(File.removeTree("tree")))
    print(report(File.removeTree("toplink")))
}
""", tmp_path)
    assert out == ["ok", "ok"]
    assert sorted(os.listdir(tmp_path)) == ["outside"]
    assert (outside / "keep").read_bytes() == b"keep"
    assert (outside / "inner" / "keep").read_bytes() == b"keep"


def test_operations_relative_to_a_dir(tmp_path):
    os.mkdir(tmp_path / "base")
    out = lines("""
fun main() {
    let d = must(File.openDir("base"))
    print(File.dirPath(d))
    let w = must(File.createIn(d, "made", File.Truncate))
    print(File.path(w))
    print(report(IO.writeText(w, "text")))
    print(report(File.close(w)))
    print(report(File.createIn(d, "made", File.Exclusive)))
    print(report(File.createDirIn(d, "sub")))
    print(report(File.createDirAllIn(d, "sub/deeper/still")))
    -- The directory moves; the `Dir` follows it.
    print(report(File.rename("base", "moved")))
    print(names(File.list(d)))
    let r = must(File.openReadIn(d, "made"))
    print(String.fromBytes(must(IO.readAll(r))))
    print(report(File.close(r)))
    match File.metadataIn(d, "made") {
        Right(s) -> print(s.size)
        Left(e) -> print(message(e))
    }
    print(report(File.renameIn(d, "made", File.workingDir(), "out")))
    let sub = must(File.openDirIn(d, "sub"))
    print(File.dirPath(sub))
    print(names(File.list(sub)))
    print(report(File.removeDirIn(sub, "deeper")))
    print(report(File.removeTreeIn(sub, "deeper")))
    print(report(File.closeDir(sub)))
    print(report(File.removeDirIn(d, "sub")))
    print(report(File.removeFileIn(d, "made")))
    print(report(File.closeDir(d)))
    print(report(File.closeDir(d)))
    print(report(File.closeDir(File.workingDir())))
    print(names(File.list(File.workingDir())))
}
""", tmp_path)
    assert out == [
        "base",
        "base/made",
        "ok",
        "ok",
        "AlreadyExists: open base/made: file exists",
        "ok",
        "ok",
        "ok",
        "made sub",
        "Some(text)",
        "ok",
        "4",
        "ok",
        "base/sub",
        "deeper",
        "NotEmpty: removeDir base/sub/deeper: directory not empty",
        "ok",
        "ok",
        "ok",
        "NotFound: remove base/made: no such file or directory",
        "ok",
        "ok",
        "ok",
        "moved out",
    ]


def test_a_lock_is_exclusive(tmp_path):
    out = lines("""
fun main() {
    let a = must(File.openAppend("lock"))
    let b = must(File.openAppend("lock"))
    print(File.tryLock(a))
    -- The lock is on the open file, so a second one in this program is kept
    -- out too.
    print(File.tryLock(b))
    print(report(File.unlock(a)))
    print(File.tryLock(b))
    print(report(File.close(b)))
    print(report(File.lock(a)))
    print(report(File.close(a)))
    print(report(File.lock(a)))
}
""", tmp_path)
    assert out == ["Right(True)", "Right(False)", "ok", "Right(True)", "ok", "ok", "ok",
                   "Closed: lock lock: file already closed"]


def test_a_lock_held_by_another_process_keeps_this_one_out(tmp_path):
    with open(tmp_path / "lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        out = lines("""
fun main() { print(File.tryLock(must(File.openAppend("lock")))) }
""", tmp_path)
    assert out == ["Right(False)"]


def test_temporaryDir_is_made_under_TMPDIR(tmp_path):
    out = lines("""
fun main() {
    let made = must(File.temporaryDir("cache-"))
    print(made)
    print(names(File.listPath(made)))
    print(report(File.removeDir(made)))
}
""", tmp_path, env={"TMPDIR": str(tmp_path) + "/"})
    made, listed, removed = out
    assert made.startswith(str(tmp_path) + "/cache-")
    assert len(made) == len(str(tmp_path) + "/cache-") + 6
    assert (listed, removed) == ("", "ok")


def test_temporaryDir_is_private(tmp_path):
    out = lines("""
fun main() { print(must(File.temporaryDir("p"))) }
""", tmp_path, env={"TMPDIR": str(tmp_path)})
    assert stat.S_IMODE(os.stat(out[0]).st_mode) == 0o700


# Fill one cache entry, unless it is there: take the lock, look, and if it is
# missing, write it under a staging name in many pieces and rename it into
# place. Both processes start together, when the test writes their go line.
RACER = HEADER + """
fun main() {
    let _ = IO.readLine(IO.stdin())
    must(File.createDirAll("cache"))
    let held = must(File.openAppend("cache/lock"))
    must(File.lock(held))
    match File.metadata("cache/entry") {
        Right(_) -> print("found")
        Left(e) -> {
            if e.kind != IO.NotFound { error(message(e)) }
            must(File.createDirAll("cache/staging"))
            let w = must(File.create("cache/staging/entry", File.Exclusive))
            for var i = 0; i < 2000; i = i + 1 {
                must(IO.writeText(w, String.repeat("x", 99) + "\\n"))
            }
            must(File.close(w))
            must(File.rename("cache/staging/entry", "cache/entry"))
            must(File.removeTree("cache/staging"))
            print("filled")
        }
    }
    must(File.unlock(held))
    let text = must(IO.readAll(must(File.openRead("cache/entry"))))
    print(IO.filled(text))
}
"""


def test_two_processes_racing_to_fill_one_cache_entry(tmp_path):
    program = binary(RACER)
    racers = [subprocess.Popen(toolchain.command(program), cwd=tmp_path,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
              for _ in range(2)]
    for racer in racers:
        racer.stdin.write(b"go\n")
        racer.stdin.flush()
    results = [racer.communicate(timeout=120) for racer in racers]
    for racer, (_, err) in zip(racers, results):
        assert racer.returncode == 0, err.decode()
    outs = sorted(out.decode().split() for out, _ in results)
    assert outs == [["filled", "200000"], ["found", "200000"]]
    assert sorted(os.listdir(tmp_path / "cache")) == ["entry", "lock"]


def test_readLink_answers_the_target_as_written(tmp_path):
    os.symlink("plain", tmp_path / "relative")
    os.symlink("/no/such/place", tmp_path / "absolute")
    # Longer than the first buffer `readLink` tries, so it has to grow.
    long_target = "d/" * 300 + "end"
    os.symlink(long_target, tmp_path / "long")
    os.mkdir(tmp_path / "dir")
    os.symlink("../plain", tmp_path / "dir" / "up")
    (tmp_path / "plain").write_bytes(b"x")
    out = lines("""
fun target(r : Either IO.IOError String) -> String = match r {
    Right(t) -> t
    Left(e) -> show(e.kind) + ": " + message(e)
}

fun main() {
    print(target(File.readLink("relative")))
    print(target(File.readLink("absolute")))
    print(String.byteLength(target(File.readLink("long"))))
    let d = must(File.openDir("dir"))
    print(target(File.readLinkIn(d, "up")))
    print(report(File.closeDir(d)))
    print(target(File.readLink("plain")))
    print(target(File.readLink("missing")))
}
""", tmp_path)
    assert out == [
        "plain",
        "/no/such/place",
        str(len(long_target)),
        "../plain",
        "ok",
        f"Other({errno.EINVAL}): readlink plain: invalid argument",
        "NotFound: readlink missing: no such file or directory",
    ]
