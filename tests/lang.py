"""The language as `boot` compiles it: check, run, and what either reports.

The behavioral tests -- a program's output, a diagnostic's text, a panic's
message -- say what Turkey does, and none of that is a question about which
compiler answered. They used to ask `turkey.driver`, the Python implementation,
which is the one being retired (TIX-96). This module asks the compiled `boot`
instead, and imports nothing from `turkey`, so a test written against it keeps
working once `turkey/` is gone.

A program is compiled the way a user would compile it: `boot native` from the
program's own directory, so a diagnostic quotes the file by its bare name, then
assembled and linked against the runtime with `cc`, then run. The library is
found through `$TURKEY_LIB`, since the directory the program is compiled from is
not the repository root.

Both steps are cached on disk, keyed by `bootc.build_key()` and the program's
sources, because a behavioral suite is hundreds of small programs and under
`pytest -n auto` any of them may be asked for by any worker. Running is not
cached: it is the thing being tested, and it takes milliseconds.

A compile error ends a `boot` process -- a diagnostic is reported by exiting --
so each program is its own invocation. That is also what makes this cheap:
the native `boot` checks a small program in a fraction of a second.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tests import bootc

REPO_ROOT = bootc.REPO_ROOT
LIB = REPO_ROOT / "lib"
WORK = Path(tempfile.gettempdir()) / "turkey-lang"

Source = str | Path


class CompileError(AssertionError):
    """`boot` rejected the program. `message` is what it printed on stderr."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass(frozen=True)
class Result:
    """A program's run: what it printed, and its exit status.

    `stderr` starts with the compile's warnings, since that is what `turkey
    run` printed on stderr before the program's own.
    """
    stdout: str
    stderr: str
    code: int


@dataclass(frozen=True)
class _Compiled:
    code: int
    stderr: str
    binary: Path | None


def program(src: str, modules: dict[str, str] | None = None) -> Path:
    """`src` as `Main.gob` in a directory of its own, beside `modules`.

    Content-addressed and written once, so two workers asking for the same
    program share one directory and neither sees it half-written.
    """
    files = {"Main.gob": src, **(modules or {})}
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode() + b"\0" + files[name].encode() + b"\0")
    directory = WORK / "src" / h.hexdigest()[:24]
    if not directory.exists():
        staging = directory.with_name(f"{directory.name}.{os.getpid()}")
        for name, text in files.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        try:
            os.rename(staging, directory)
        except OSError:
            # Another worker got there first with the same bytes.
            _remove(staging)
    return directory / "Main.gob"


def _remove(directory: Path) -> None:
    for path in sorted(directory.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    directory.rmdir()


def _entry(src: Source, modules: dict[str, str] | None) -> Path:
    if isinstance(src, Path):
        assert modules is None, "a program on disk brings its own modules"
        return src.resolve()
    return program(src, modules)


def _key(command: str, entry: Path) -> str:
    """The cache key for `boot <command>` on `entry`.

    The entry's own bytes, and -- for a `Main.gob`, the one name a program with
    modules has -- every `.gob` beside and below it, which is what it can import.
    A single-file corpus program imports only the library, which `build_key`
    covers.
    """
    h = hashlib.sha256()
    h.update(bootc.build_key().encode())
    h.update(command.encode())
    h.update(str(entry).encode())
    sources = (sorted(entry.parent.rglob("*.gob")) if entry.name == "Main.gob"
               else [entry])
    for path in sources:
        h.update(str(path.relative_to(entry.parent)).encode() + b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()[:24]


def _boot(command: str, entry: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(bootc.binary()), command, entry.name],
        cwd=entry.parent,
        env=dict(os.environ, TURKEY_LIB=str(LIB)),
        capture_output=True,
        timeout=600,
    )


def _cached(command: str, entry: Path, build) -> _Compiled:
    """One compile, done once per key under a lock and kept on disk."""
    directory = WORK / "out" / _key(command, entry)
    status = directory / "status.json"

    def load() -> _Compiled | None:
        if not status.exists():
            return None
        data = json.loads(status.read_text(encoding="utf-8"))
        binary = directory / "bin"
        return _Compiled(data["code"], data["stderr"],
                         binary if binary.exists() else None)

    if (found := load()) is not None:
        return found
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (found := load()) is not None:
            return found
        code, stderr = build(directory)
        staging = status.with_suffix(f".{os.getpid()}")
        staging.write_text(json.dumps({"code": code, "stderr": stderr}),
                           encoding="utf-8")
        os.replace(staging, status)
    found = load()
    assert found is not None
    return found


def _native(entry: Path) -> _Compiled:
    def build(directory: Path) -> tuple[int, str]:
        result = _boot("native", entry)
        stderr = result.stderr.decode("utf-8")
        if result.returncode != 0:
            return result.returncode, stderr
        assembly = result.stdout
        failed = [line for line in assembly.decode("utf-8").splitlines()
                  if line.startswith("// FAILED") or line.startswith("// skipped")]
        if failed:
            return 1, stderr + "boot: the backend refused this program\n" + \
                "\n".join(failed) + "\n"
        source = directory / f"main.{os.getpid()}.s"
        source.write_bytes(assembly)
        try:
            bootc.replace_built(
                ["cc", "-o", str(directory / f"bin.{os.getpid()}"),
                 str(source), str(bootc.runtime_object())],
                directory / "bin")
        finally:
            source.unlink(missing_ok=True)
        return 0, stderr
    return _cached("native", entry, build)


def _checked(entry: Path) -> _Compiled:
    def build(_: Path) -> tuple[int, str]:
        result = _boot("check", entry)
        return result.returncode, result.stderr.decode("utf-8")
    return _cached("check", entry, build)


def check(src: Source, modules: dict[str, str] | None = None) -> str:
    """Compile without running. Answers the warnings; raises `CompileError`."""
    compiled = _checked(_entry(src, modules))
    if compiled.code != 0:
        raise CompileError(compiled.stderr, compiled.code)
    return compiled.stderr


def run(src: Source, modules: dict[str, str] | None = None,
        args: tuple[str, ...] = (), stdin: str | None = None,
        env: dict[str, str] | None = None) -> Result:
    """Compile and run. Raises `CompileError` if it does not compile.

    Bytes, decoded here rather than by `text=True`, so a `\\r` the program
    prints is not rewritten as a newline on the way.
    """
    entry = _entry(src, modules)
    compiled = _native(entry)
    if compiled.code != 0 or compiled.binary is None:
        raise CompileError(compiled.stderr, compiled.code)
    result = subprocess.run(
        [str(compiled.binary), *args],
        cwd=entry.parent,
        input=None if stdin is None else stdin.encode("utf-8"),
        env=None if env is None else dict(os.environ, **env),
        capture_output=True,
        timeout=600,
    )
    return Result(result.stdout.decode("utf-8"),
                  compiled.stderr + result.stderr.decode("utf-8"),
                  result.returncode)


def output(src: Source, modules: dict[str, str] | None = None) -> str:
    """A program that is expected to succeed, and its stdout."""
    result = run(src, modules)
    assert result.code == 0, (
        f"exited {result.code}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def fails(src: Source, modules: dict[str, str] | None = None) -> str:
    """A program that is expected not to compile, and the diagnostic.

    Checked with `boot check`, which reports what the front end and the Core
    checks refuse -- the same thing `driver.check` raised on.
    """
    try:
        warnings = check(src, modules)
    except CompileError as error:
        return error.message
    raise AssertionError(f"expected a compile error, but it compiled\n{warnings}")


def panics(src: Source, modules: dict[str, str] | None = None) -> Result:
    """A program that is expected to compile and then panic."""
    result = run(src, modules)
    assert result.code != 0 and "panic: " in result.stderr, (
        f"expected a panic, exited {result.code}\n{result.stdout}\n{result.stderr}")
    return result


def panic_message(result: Result) -> str:
    """The message of the panic in `result`, without its trace."""
    for line in result.stderr.splitlines():
        if line.startswith("panic: "):
            return line[len("panic: "):]
    raise AssertionError(f"no panic in\n{result.stderr}")


def types(src: Source, modules: dict[str, str] | None = None) -> dict[str, str]:
    """The entry module's signatures, as `boot types` prints them."""
    entry = _entry(src, modules)
    result = _boot("types", entry)
    stderr = result.stderr.decode("utf-8")
    if result.returncode != 0:
        raise CompileError(stderr, result.returncode)
    out: dict[str, str] = {}
    for line in result.stdout.decode("utf-8").splitlines():
        name, _, scheme = line.partition(" : ")
        out[name] = scheme
    return out
