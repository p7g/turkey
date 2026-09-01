"""`System.Env` and `System.IO`, and the recursion depth a compiler needs.

What `tests/programs/system.tl` cannot check, because a golden file is run with
no arguments, in a fixed directory, and is compared on its output alone: the
arguments a program is handed, the status it chooses to exit with, the file it
writes, and how deep it may recurse before the host gives out.

This is the floor roadmap item 9 is built on -- a compiler reads a file named
on its command line and answers with a status -- so it is checked as a floor,
end to end through `python -m turkey run`, rather than by unit-testing the
primitives underneath it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(program: str, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "prog.tl"
    source.write_text(program, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "turkey", "run", str(source), "--", *args],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)),
        capture_output=True,
        text=True,
    )


ARGS = """
import System.Env as Env

fun main() {
    let a = Env.args()
    print(Int.toString(len(a)))
    for x in a { print(x) }
}
"""


def test_arguments_reach_the_program(tmp_path: Path) -> None:
    # Element zero is the program's first argument, not its own name. The C
    # backend hands over `argv + 1` for the same reason: the two hosts have to
    # agree on this, or a self-compiled compiler reads a different command line
    # than the one that built it (M26).
    result = _run(ARGS, tmp_path, "input.tl", "-o", "out.c")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "3\ninput.tl\n-o\nout.c\n"


def test_no_arguments_is_an_empty_array(tmp_path: Path) -> None:
    result = _run(ARGS, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "0\n"


def test_a_dash_dash_separator_is_not_itself_an_argument(tmp_path: Path) -> None:
    # An option-shaped argument has to survive: argparse must not claim `-o`
    # for itself, which is why `run` takes the rest verbatim.
    result = _run(ARGS, tmp_path, "-o")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1\n-o\n"


EXIT = """
import System.Env as Env

fun main() {
    print("before")
    Env.exit(3)
    print("after")
}
"""


def test_exit_chooses_the_status_and_stops(tmp_path: Path) -> None:
    result = _run(EXIT, tmp_path)
    assert result.returncode == 3
    assert result.stdout == "before\n"


ROUND_TRIP = """
import System.IO as IO
import Data.String as S

fun main() {
    print(Bool.toString(IO.writeFile("out.txt", "wrote\\nthis\\n")))
    match IO.readFile("out.txt") {
        Some(text) -> print(Int.toString(len(S.lines(text))))
        None -> print("could not read back")
    }
    print(Bool.toString(IO.writeFile("no/such/dir/out.txt", "x")))
}
"""


def test_write_then_read(tmp_path: Path) -> None:
    result = _run(ROUND_TRIP, tmp_path)
    assert result.returncode == 0, result.stderr
    # Written, read back as two lines, and a write into a directory that is not
    # there answers False rather than panicking.
    assert result.stdout == "True\n2\nFalse\n"
    assert (tmp_path / "out.txt").read_text() == "wrote\nthis\n"


INVALID_UTF8 = """
import System.IO as IO

fun main() {
    match IO.readFile("bytes.bin") {
        Some(_) -> print("read ill-formed bytes as a String")
        None -> print("not UTF-8")
    }
}
"""


def test_a_file_that_is_not_utf8_reads_as_none(tmp_path: Path) -> None:
    # The invariant is the point: a `String` is well-formed UTF-8, so the
    # validating constructor is what stands between a file and one.
    (tmp_path / "bytes.bin").write_bytes(b"\xff\xfe\x00")
    result = _run(INVALID_UTF8, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "not UTF-8\n"


DEEP = """
fun depth(n : Int) -> Int {
    if n == 0 { 0 } else { 1 + depth(n - 1) }
}

fun main() { print(Int.toString(depth(50000))) }
"""


def test_recursion_is_not_capped_at_the_host_default(tmp_path: Path) -> None:
    # CPython's 1000 frames is a fact about the host, not about the language.
    # A compiler walking its own AST passes that depth on an ordinary file.
    result = _run(DEEP, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "50000\n"
