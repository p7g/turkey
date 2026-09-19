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

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tests import bootc

REPO_ROOT = bootc.REPO_ROOT
LIB = REPO_ROOT / "lib"
CORPUS = REPO_ROOT / "tests" / "programs"
WORK = Path(tempfile.gettempdir()) / "turkey-lang"

Source = str | Path


class CompileError(AssertionError):
    """`boot` rejected the program.

    `rendered` is what it printed on stderr -- `file:line:col: stage: message`
    -- and `message` is the message alone, which is what a test usually means.
    """

    def __init__(self, rendered: str, code: int) -> None:
        super().__init__(rendered)
        self.rendered = rendered
        self.message = _message_of(rendered)
        self.code = code


_STAGES = ("lex error", "parse error", "type error", "internal error",
           "giblets", "error")


def _message_of(rendered: str) -> str:
    """The message of a rendered diagnostic, without where and which stage."""
    text = rendered.rstrip("\n")
    for stage in _STAGES:
        marker = f": {stage}: "
        if marker in text:
            return text.split(marker, 1)[1]
    return text


class Panic(AssertionError):
    """A program panicked. `message` is what followed `panic: `."""

    def __init__(self, result: Result) -> None:
        self.message = panic_message(result)
        super().__init__(self.message)
        self.result = result


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

    The entry's own bytes and every `.gob` beside and below it, which is what
    it can import -- except in `tests/programs`, whose single-file programs
    import only the library, which `build_key` covers. Hashing the whole corpus
    into each of its programs' keys would make an edit to one a recompile of
    all of them.
    """
    h = hashlib.sha256()
    h.update(bootc.build_key().encode())
    h.update(command.encode())
    h.update(str(entry).encode())
    sources = ([entry] if entry.parent == CORPUS
               else sorted(entry.parent.rglob("*.gob")))
    for path in sources:
        h.update(str(path.relative_to(entry.parent)).encode() + b"\0")
        h.update(path.read_bytes())
    # A test's throwaway module in `lib/` (`test_foreign`, `test_giblets`),
    # which `build_key` leaves out so that writing one is not a rebuild. It is
    # still an input to whatever imports it.
    for path in sorted(LIB.rglob("Probe_*.gob")):
        h.update(str(path.relative_to(LIB)).encode() + b"\0")
        with contextlib.suppress(FileNotFoundError):
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
    """A program that is expected not to compile, and its message.

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


def execute(src: Source, modules: dict[str, str] | None = None,
            args: tuple[str, ...] = ()) -> None:
    """`driver.run`'s shape: print the program's stdout, raise on failure.

    For tests that read what was printed with `capsys`. A compile error raises
    `CompileError`, a panic `Panic`, after the output before it is printed.
    """
    result = run(src, modules, args)
    sys.stdout.write(result.stdout)
    if result.code != 0:
        if "panic: " in result.stderr:
            raise Panic(result)
        raise AssertionError(
            f"exited {result.code}\n{result.stdout}\n{result.stderr}")


def panic_message(result: Result) -> str:
    """The message of the panic in `result`, without its trace."""
    for line in result.stderr.splitlines():
        if line.startswith("panic: "):
            return line[len("panic: "):]
    raise AssertionError(f"no panic in\n{result.stderr}")


def dump(command: str, src: Source,
         modules: dict[str, str] | None = None) -> Result:
    """`boot <command>` on one program -- `types`, `core`, `mono`, `opt` --
    from the program's own directory. Not cached; a dump is what is tested."""
    result = _boot(command, _entry(src, modules))
    return Result(result.stdout.decode("utf-8"), result.stderr.decode("utf-8"),
                  result.returncode)


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


_KIND = re.compile(r"^type (\S+)(?: \S+)* :: (.+?)(?: = alias)?$")


def kinds(src: Source, modules: dict[str, str] | None = None) -> dict[str, str]:
    """Every type constructor in the program and the kind inferred for it, as
    `boot decls` prints them: keyed by qualified name (`Main#Boxed`,
    `Data.Array#Array`, `Prim.Array`)."""
    result = dump("decls", src, modules)
    if result.code != 0:
        raise CompileError(result.stderr, result.code)
    out: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if (m := _KIND.match(line)):
            out[m.group(1)] = m.group(2)
    return out


@dataclass(frozen=True)
class ClassInfo:
    kind: str
    methods: tuple[str, ...]


_CLASS = re.compile(r"^class (\S+)(?: \S+)* :: (.+)$")
_METHOD = re.compile(r"^  method (\S+) : ")


def classes(src: Source,
            modules: dict[str, str] | None = None) -> dict[str, ClassInfo]:
    """Every class in the program, its parameter's kind and its methods, as
    `boot classes` prints them, keyed by qualified name (`Main#Egal`)."""
    result = dump("classes", src, modules)
    if result.code != 0:
        raise CompileError(result.stderr, result.code)
    out: dict[str, ClassInfo] = {}
    current: tuple[str, str, list[str]] | None = None
    for line in result.stdout.splitlines():
        if (m := _CLASS.match(line)):
            if current:
                out[current[0]] = ClassInfo(current[1], tuple(current[2]))
            current = (m.group(1), m.group(2), [])
        elif (m := _METHOD.match(line)) and current:
            current[2].append(m.group(1))
    if current:
        out[current[0]] = ClassInfo(current[1], tuple(current[2]))
    return out
