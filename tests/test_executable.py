"""The executable writers: `boot build -f exe` writes a signed Mach-O, or an
ELF for glibc, that the loader takes as it takes the linker's, with nothing
linked.

`scripts/test.sh --exe` runs every program through this path against the
same goldens, which is what decides whether the image is right. These tests
check what a run cannot see: that the signature verifies, that the loader is
asked for the binds the linker would ask for and for no rebase, that the
program starts where the linker's does, that a module always writes the same
bytes, and what is refused.

The Mach-O tests that run the executable need arm64-darwin, with the
system's `codesign` and `dyld_info` as the oracles. The rest run on any
host: an executable is written for either target wherever `boot` runs, and
its structure, signature and build ID are checked here in Python, against
Python's own SHA-256, so each host checks that writing for the other needs
nothing from it.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from tests import bootc, toolchain

REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = REPO_ROOT / "tests" / "programs"
ENV = dict(os.environ, TURKEY_LIB=str(REPO_ROOT / "lib"))

darwin = pytest.mark.skipif(
    toolchain.target() != "arm64-darwin" or not shutil.which("dyld_info"),
    reason="runs the executable, and checks it with Apple's tools")


def _boot(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(toolchain.command(bootc.binary(), "build", *args),
                          capture_output=True, text=True, env=env or ENV, timeout=600)


def _write(entry: Path, output: Path) -> None:
    result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(output), str(entry))
    assert result.returncode == 0, result.stderr[-4000:]


def _fixups(executable: Path) -> list[tuple[str, str, str]]:
    """What `dyld_info -fixups` asks the loader for: each fixup's section,
    kind and target, in address order."""
    result = subprocess.run(["dyld_info", "-fixups", str(executable)],
                            capture_output=True, text=True, check=True)
    rows = [line.split() for line in result.stdout.splitlines()]
    return [(row[1], row[3], row[4] if len(row) > 4 else "") for row in rows
            if len(row) >= 4 and row[0].startswith("__")]


def _entry(executable: Path) -> str:
    """The symbol at the address `LC_MAIN` names."""
    commands = subprocess.run(["otool", "-l", str(executable)],
                              capture_output=True, text=True, check=True).stdout
    found = re.search(r"entryoff (\d+)", commands)
    assert found, commands
    entryoff = int(found.group(1))
    symbols = subprocess.run(["nm", str(executable)],
                             capture_output=True, text=True, check=True).stdout
    for line in symbols.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) == 3 and parts[1] in "tT" and int(parts[0], 16) == 0x100000000 + entryoff:
            return parts[2]
    raise AssertionError(f"no symbol at the entry, offset {entryoff:#x}")


@darwin
def test_the_executable_verifies_and_runs(tmp_path):
    executable = tmp_path / "adt"
    _write(PROGRAMS / "adt.gob", executable)
    subprocess.run(["codesign", "-v", "--strict", str(executable)], check=True)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == (PROGRAMS / "adt.expected").read_text(encoding="utf-8")


@darwin
def test_the_loader_is_asked_for_what_the_linker_asks_for(tmp_path):
    ours = tmp_path / "ours"
    theirs = tmp_path / "theirs"
    _write(PROGRAMS / "adt.gob", ours)
    bootc.link(PROGRAMS / "adt.gob", theirs, env=ENV)
    fixups = _fixups(ours)
    assert fixups and {kind for _, kind, _ in fixups} == {"bind"}, fixups[:10]
    assert fixups == _fixups(theirs)
    assert _entry(ours) == _entry(theirs) == "_main"


# The fixed point compares executables, so nothing in one may depend on when
# or where it was written, or what it is called.
def test_one_module_writes_the_same_bytes(tmp_path):
    first = tmp_path / "a" / "adt"
    second = tmp_path / "b" / "other"
    first.parent.mkdir()
    second.parent.mkdir()
    _write(PROGRAMS / "adt.gob", first)
    _write(PROGRAMS / "adt.gob", second)
    assert first.read_bytes() == second.read_bytes()


# macOS remembers the signature of a file it has run, and kills a process
# started from that file once its bytes change in place.
@darwin
def test_an_executable_written_over_one_that_ran_still_runs(tmp_path):
    executable = tmp_path / "program"
    _write(PROGRAMS / "adt.gob", executable)
    assert subprocess.run([str(executable)], capture_output=True).returncode == 0
    _write(PROGRAMS / "args.gob", executable)
    ran = subprocess.run([str(executable)], capture_output=True, timeout=60)
    assert ran.returncode == 0, ran


def _tls(tmp_path: Path) -> Path:
    entry = tmp_path / "tls.gob"
    entry.write_text("import Net.TLS as TLS\n\n"
                     "fun main() {\n"
                     "    let _ = TLS.clientConfig(TLS.SystemRoots, [])\n"
                     "}\n", encoding="utf-8")
    return entry


# A library beyond the C library is named by the install name of the file
# `lib<name>.<major>.dylib`, searched for first in `$TURKEY_DYLIB_PATH`. These
# write for arm64-darwin with only made-up libraries on that path, so they
# check the search and the load commands on any host.

CPU_ARM64, CPU_X86_64 = 0x0100000C, 0x01000007


def _dylib(path: Path, install_name: str, cpu: int = CPU_ARM64) -> bytes:
    """A thin dylib holding a header and an `LC_ID_DYLIB`, and nothing else,
    which is all the search reads. Written to `path`; also answered."""
    name = install_name.encode() + b"\0"
    size = -(-(24 + len(name)) // 8) * 8
    command = struct.pack("<6I", 0xD, size, 24, 2, 0x30000, 0x30000) + name
    command += bytes(size - len(command))
    data = struct.pack("<8I", 0xFEEDFACF, cpu, 0, 6, 1, size, 0, 0) + command
    path.write_bytes(data)
    return data


def _universal(path: Path, slices: list[tuple[int, bytes]]) -> None:
    """A universal file of `slices`, each a CPU type and a thin file."""
    header = struct.pack(">2I", 0xCAFEBABE, len(slices))
    at = 0x1000
    body = b""
    for cpu, data in slices:
        header += struct.pack(">5I", cpu, 0, at + len(body), len(data), 12)
        body += data + bytes(-len(data) % 0x1000)
    path.write_bytes(header + bytes(at - len(header)) + body)


def _dylibs(data: bytes) -> list[tuple[str, int, int]]:
    """Each `LC_LOAD_DYLIB`: its install name and its current and
    compatibility versions."""
    out = []
    for command, body in _commands(data):
        if command == 0xC:
            offset, _, current, compatibility = struct.unpack_from("<4I", body, 8)
            out.append((body[offset:].split(b"\0")[0].decode(), current, compatibility))
    return out


def _undefined(data: bytes) -> dict[str, int]:
    """Each undefined symbol, and the library ordinal its description holds."""
    commands = dict(_commands(data))
    symbols, count, strings, _ = struct.unpack_from("<4I", commands[0x2], 8)
    out = {}
    for k in range(count):
        name_at, kind, _, desc, _ = struct.unpack_from("<IBBHQ", data, symbols + 16 * k)
        if kind == 0x1:
            name = data[strings + name_at:data.index(b"\0", strings + name_at)].decode()
            out[name] = desc >> 8
    return out


def _uleb(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, at


def _binds(data: bytes) -> dict[str, int]:
    """What `LC_DYLD_INFO_ONLY`'s bind opcodes bind, each symbol to its
    library ordinal: the stream dyld itself reads. Only the opcodes the writer
    emits are understood."""
    _, _, _, _, offset, size = struct.unpack_from("<6I", dict(_commands(data))[0x80000022], 0)
    stream = data[offset:offset + size]
    binds, ordinal, symbol, at = {}, None, None, 0
    while True:
        op, at = stream[at], at + 1
        kind, immediate = op & 0xF0, op & 0x0F
        if op == 0:
            return binds
        if kind == 0x10:
            ordinal = immediate
        elif kind == 0x20:
            ordinal, at = _uleb(stream, at)
        elif kind == 0x40:
            end = stream.index(b"\0", at)
            symbol, at = stream[at:end].decode(), end + 1
        elif kind == 0x50:
            assert immediate == 1, "BIND_TYPE_POINTER"
        elif kind == 0x70:
            _, at = _uleb(stream, at)
        elif kind == 0x90:
            binds[symbol] = ordinal
        else:
            raise AssertionError(f"bind opcode {op:#x}")


def _openssl_dylibs(directory: Path) -> Path:
    directory.mkdir()
    _dylib(directory / "libssl.3.dylib", "/made/up/ssl/libssl.3.dylib")
    _dylib(directory / "libcrypto.3.dylib", "/made/up/crypto/libcrypto.3.dylib")
    return directory


def test_each_library_is_loaded_once_and_bound_by_its_ordinal(tmp_path):
    executable = tmp_path / "tls"
    found = _openssl_dylibs(tmp_path / "dylibs")
    result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(executable),
                   str(_tls(tmp_path)), env=dict(ENV, TURKEY_DYLIB_PATH=f"{found}:"))
    assert result.returncode == 0, result.stderr[-4000:]
    data = executable.read_bytes()
    # libSystem first, then the others by install name, at version 0.
    assert _dylibs(data) == [("/usr/lib/libSystem.B.dylib", 0x10000, 0x10000),
                             ("/made/up/crypto/libcrypto.3.dylib", 0, 0),
                             ("/made/up/ssl/libssl.3.dylib", 0, 0)]
    undefined = _undefined(data)
    assert undefined["_SSL_CTX_new"] == 3 and undefined["_ERR_get_error"] == 2
    assert undefined["_write"] == 1
    ordinals = list(undefined.values())
    assert ordinals == sorted(ordinals)
    # The symbol table and the binds dyld reads agree.
    assert _binds(data) == undefined


def test_a_library_must_be_installed_at_an_absolute_path(tmp_path):
    found = tmp_path / "dylibs"
    found.mkdir()
    _dylib(found / "libssl.3.dylib", "@rpath/libssl.3.dylib")
    _dylib(found / "libcrypto.3.dylib", "@rpath/libcrypto.3.dylib")
    result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(tmp_path / "tls"),
                   str(_tls(tmp_path)), env=dict(ENV, TURKEY_DYLIB_PATH=str(found)))
    assert result.returncode == 2
    assert re.search(r"and '\S+/dylibs/lib(ssl|crypto)\.3\.dylib' is installed as "
                     r"'@rpath/lib(ssl|crypto)\.3\.dylib', which is not an absolute path",
                     result.stderr), result.stderr


# A file whose header asks for more than the file holds is passed over, as any
# other malformed one is, rather than read at the size it claims.
def test_a_malformed_library_is_passed_over(tmp_path):
    corrupt, good = tmp_path / "corrupt", _openssl_dylibs(tmp_path / "good")
    corrupt.mkdir()
    huge = bytearray(_dylib(corrupt / "libssl.3.dylib", "/corrupt/libssl.3.dylib"))
    struct.pack_into("<I", huge, 20, 0xFFFFFFF0)
    (corrupt / "libssl.3.dylib").write_bytes(huge)
    (corrupt / "libcrypto.3.dylib").write_bytes(struct.pack(">2I", 0xCAFEBABE, 0x10000000))
    # An `LC_ID_DYLIB` too short to hold a name's offset, and in the next
    # directory one whose name starts past its command's end.
    short = tmp_path / "short"
    short.mkdir()
    (short / "libssl.3.dylib").write_bytes(
        struct.pack("<8I", 0xFEEDFACF, CPU_ARM64, 0, 6, 1, 8, 0, 0) + struct.pack("<2I", 0xD, 8))
    outside = bytearray(_dylib(short / "libcrypto.3.dylib", "/outside/libcrypto.3.dylib"))
    struct.pack_into("<I", outside, 32 + 8, 0x1000)
    (short / "libcrypto.3.dylib").write_bytes(outside)
    executable = tmp_path / "tls"
    result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(executable),
                   str(_tls(tmp_path)),
                   env=dict(ENV, TURKEY_DYLIB_PATH=f"{short}:{corrupt}:{good}"))
    assert result.returncode == 0, result.stderr[-4000:]
    assert [name for name, _, _ in _dylibs(executable.read_bytes())][1:] == [
        "/made/up/crypto/libcrypto.3.dylib", "/made/up/ssl/libssl.3.dylib"]


def test_a_library_is_found_in_a_universal_file_past_one_for_intel(tmp_path):
    intel, universal = tmp_path / "intel", tmp_path / "universal"
    intel.mkdir()
    universal.mkdir()
    _dylib(intel / "libssl.3.dylib", "/intel/libssl.3.dylib", CPU_X86_64)
    _dylib(intel / "libcrypto.3.dylib", "/intel/libcrypto.3.dylib", CPU_X86_64)
    for name in ("ssl", "crypto"):
        leaf = f"lib{name}.3.dylib"
        _universal(universal / leaf,
                   [(CPU_X86_64, _dylib(tmp_path / "x", f"/x86/{leaf}", CPU_X86_64)),
                    (CPU_ARM64, _dylib(tmp_path / "a", f"/universal/{leaf}"))])
    executable = tmp_path / "tls"
    result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(executable),
                   str(_tls(tmp_path)),
                   env=dict(ENV, TURKEY_DYLIB_PATH=f"{intel}:{universal}"))
    assert result.returncode == 0, result.stderr[-4000:]
    assert [name for name, _, _ in _dylibs(executable.read_bytes())] == [
        "/usr/lib/libSystem.B.dylib", "/universal/libcrypto.3.dylib",
        "/universal/libssl.3.dylib"]


def _never_calls(tmp_path: Path) -> Path:
    entry = tmp_path / "quiet.gob"
    entry.write_text("import Net.TLS as TLS\n\n"
                     "fun main() {\n"
                     '    print("no TLS")\n'
                     "}\n", encoding="utf-8")
    return entry


# Needs no `$TURKEY_DYLIB_PATH`, even for another machine: nothing is looked for.
def test_a_program_that_never_calls_openssl_loads_neither_library(tmp_path):
    env = {k: v for k, v in ENV.items() if k != "TURKEY_DYLIB_PATH"}
    macho, elf = tmp_path / "macho", tmp_path / "elf"
    for target, output in (("arm64-darwin", macho), ("arm64-linux", elf)):
        result = _boot("-f", "exe", "--target", target, "-o", str(output),
                       str(_never_calls(tmp_path)), env=env)
        assert result.returncode == 0, result.stderr[-4000:]
    assert [name for name, _, _ in _dylibs(macho.read_bytes())] == ["/usr/lib/libSystem.B.dylib"]
    assert Elf(elf.read_bytes()).needed() == ["libc.so.6"]


@pytest.fixture
def probe(monkeypatch, tmp_path):
    """Writes a throwaway module into the real `lib/`, which
    `TURKEY_TEST_FOREIGN` lets declare foreign functions, and an entry that
    imports it as `P` and runs `body`. Answers the entry."""
    stem = "Probe_executable_" + secrets.token_hex(4)
    module = REPO_ROOT / "lib" / f"{stem}.gob"
    monkeypatch.setitem(ENV, "TURKEY_TEST_FOREIGN", stem)

    def write(declarations: str, body: str) -> Path:
        module.write_text(declarations, encoding="utf-8")
        entry = tmp_path / "probe.gob"
        entry.write_text(f"import {stem} as P\n\nfun main() {{\n    {body}\n}}\n",
                         encoding="utf-8")
        return entry

    try:
        yield write
    finally:
        module.unlink(missing_ok=True)


@pytest.fixture
def missing_library(probe):
    """A program calling a function of a library no machine has."""
    return probe("export (nothing)\n"
                 'foreign "turkeynothere" 7 {\n'
                 '    "turkey_nothing" fun nothing() -> Int\n'
                 "}\n", "let _ = P.nothing()")


def test_a_library_not_found_is_an_error_naming_where_it_was_looked_for(tmp_path,
                                                                        missing_library):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(tmp_path / "out"),
                   str(missing_library), env=dict(ENV, TURKEY_DYLIB_PATH=str(empty)))
    assert result.returncode == 2
    assert ("it calls 'turkey_nothing' from library 'turkeynothere' 7, and no "
            f"'libturkeynothere.7.dylib' is in '{empty}'") in result.stderr, result.stderr
    if toolchain.target() == "arm64-darwin":
        assert "'/opt/homebrew/opt/*/lib', '/usr/local/opt/*/lib'" in result.stderr
    assert "set TURKEY_DYLIB_PATH" in result.stderr


@pytest.mark.skipif(toolchain.target() == "arm64-darwin",
                    reason="writes for another machine than this one")
def test_another_machines_library_needs_a_directory_to_read_it_from(tmp_path):
    env = {k: v for k, v in ENV.items() if k != "TURKEY_DYLIB_PATH"}
    result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(tmp_path / "tls"),
                   str(_tls(tmp_path)), env=env)
    assert result.returncode == 2
    assert re.search(r"cannot write '\S+' for arm64-darwin on this machine: it calls '\w+' "
                     r"from library '(ssl|crypto)' 3, whose install name is read from "
                     r"'lib(ssl|crypto)\.3\.dylib' itself; set TURKEY_DYLIB_PATH",
                     result.stderr), result.stderr


# The output is written only once its bytes are complete, so a build that
# fails leaves what was there before, and nothing beside it.
def test_a_refused_build_leaves_the_output_as_it_was(tmp_path, missing_library):
    builds = tmp_path / "builds"
    builds.mkdir()
    existing = builds / "existing"
    existing.write_bytes(b"before")
    env = dict(ENV, TURKEY_DYLIB_PATH=str(builds))
    for output in (existing, builds / "absent"):
        result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(output),
                       str(missing_library), env=env)
        assert result.returncode == 2, result.stderr
    assert existing.read_bytes() == b"before"
    assert sorted(p.name for p in builds.iterdir()) == ["existing"]


# Each build writes a partial file of its own, made exclusively, so one that
# is already there -- another build's, still being written -- is never
# touched.
def test_a_build_leaves_another_builds_partial_file_alone(tmp_path):
    other = tmp_path / ".adt.partial0"
    other.write_bytes(b"another build's")
    _write(PROGRAMS / "adt.gob", tmp_path / "adt")
    assert other.read_bytes() == b"another build's"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".adt.partial0", "adt"]


def test_an_executable_is_written_only_when_asked_for(tmp_path):
    result = _boot("--target", "arm64-darwin", "-o", str(tmp_path / "adt"),
                   str(PROGRAMS / "adt.gob"))
    assert result.returncode == 2
    assert "only with -f exe" in result.stderr


@pytest.mark.parametrize("target", ["arm64-darwin", "arm64-linux"])
def test_an_executable_takes_debug_information(tmp_path, target):
    result = _boot("-f", "exe", "--target", target, "-g", "-o", str(tmp_path / "adt"),
                   str(PROGRAMS / "adt.gob"))
    assert result.returncode == 0, result.stderr[-4000:]


def _commands(data: bytes):
    """Each load command of a Mach-O file: its number and its bytes."""
    count, size = struct.unpack_from("<II", data, 16)
    at = 32
    for _ in range(count):
        command, length = struct.unpack_from("<II", data, at)
        yield command, data[at:at + length]
        at += length


def test_the_signature_and_the_uuid_hash_the_file(tmp_path):
    executable = tmp_path / "adt"
    _write(PROGRAMS / "adt.gob", executable)
    data = executable.read_bytes()
    assert struct.unpack_from("<III", data, 0) == (0xFEEDFACF, 0x0100000C, 0)
    commands = dict(_commands(data))
    offset, size = struct.unpack_from("<II", commands[0x1D], 8)
    assert offset + size == len(data)
    magic, length, count, slot, at = struct.unpack_from(">5I", data, offset)
    assert (magic, length, count, slot) == (0xFADE0CC0, size, 1, 0)
    directory = data[offset + at:offset + size]
    (magic, _, version, flags, hashes, identifier, special, slots, limit,
     hash_size, hash_type, _, page) = struct.unpack_from(">9I4B", directory)
    assert (magic, version, flags, special, hash_type) == (0xFADE0C02, 0x20400, 0x20002, 0, 2)
    assert limit == offset and hash_size == 32 and page == 12
    assert directory[identifier:].split(b"\0")[0] == b"adt"
    step = 4096
    assert slots == -(-limit // step)
    for k in range(-(-limit // step)):
        expected = hashlib.sha256(data[k * step:min((k + 1) * step, limit)]).digest()
        assert directory[hashes + 32 * k:hashes + 32 * (k + 1)] == expected, f"page {k}"
    uuid_at = data.index(commands[0x1B])
    unsigned = bytearray(data[:limit])
    unsigned[uuid_at + 8:uuid_at + 24] = bytes(16)
    digest = bytearray(hashlib.sha256(unsigned).digest()[:16])
    digest[6] = digest[6] & 0x0F | 0x80
    digest[8] = digest[8] & 0x3F | 0x80
    assert commands[0x1B][8:24] == digest


def _segments(data: bytes):
    """Each `LC_SEGMENT_64`: its name, address, size, file offset, file size
    and sections, each section its segment's and own names, address, size,
    offset, flags and the two reserved words."""
    for command, body in _commands(data):
        if command != 0x19:
            continue
        name, address, size, offset, file_size, _, _, count, _ = struct.unpack_from(
            "<16s4Q4I", body, 8)
        sections = []
        for k in range(count):
            (section, segment, at, length, file_at, _, _, _, flags, first,
             second, _) = struct.unpack_from("<16s16s2Q8I", body, 72 + 80 * k)
            sections.append((segment.rstrip(b"\0"), section.rstrip(b"\0"), at, length,
                             file_at, flags, first, second))
        yield name.rstrip(b"\0"), address, size, offset, file_size, sections


def test_the_layout_is_what_the_loader_needs(tmp_path):
    executable = tmp_path / "adt"
    _write(PROGRAMS / "adt.gob", executable)
    data = executable.read_bytes()
    page = 0x4000
    segments = list(_segments(data))
    assert [s[0] for s in segments] == [b"__PAGEZERO", b"__TEXT", b"__DATA_CONST", b"__DATA",
                                        b"__LINKEDIT"]
    for name, address, size, offset, file_size, sections in segments[1:]:
        assert size % page == 0 and address % page == 0, name
        assert address - 0x100000000 == offset, name
        if name != b"__LINKEDIT":
            assert file_size == size, name
        for segment, section, at, length, file_at, *_ in sections:
            assert segment == name, (segment, section)
            assert address <= at and at + length <= address + size, section
            assert file_at == offset + (at - address), section
    linkedit = segments[-1]
    assert linkedit[3] + linkedit[4] == len(data)
    commands = dict(_commands(data))
    symbols, count, _, _ = struct.unpack_from("<4I", commands[0x2], 8)
    (_, nlocal, _, nextdef, _, nundef, _, _, _, _, _, _, _, nindirect) = struct.unpack_from(
        "<14I", commands[0xB], 8)
    assert nlocal + nextdef + nundef == count
    assert nundef > 0 and nindirect == 2 * nundef
    sections = {s[1]: s for s in segments[1][5] + segments[2][5]}
    stubs, got = sections[b"__stubs"], sections[b"__got"]
    assert stubs[3] == 12 * nundef and stubs[7] == 12 and stubs[6] == 0
    assert got[3] == 8 * nundef and got[6] == nundef


# The ELF writer, for glibc on arm64 Linux.

linux = pytest.mark.skipif(toolchain.target() != "arm64-linux",
                           reason="runs the executable against the host's glibc")

PT_LOAD, PT_DYNAMIC, PT_INTERP, PT_NOTE, PT_PHDR = 1, 2, 3, 4, 6
PT_GNU_STACK, PT_GNU_RELRO = 0x6474E551, 0x6474E552
DT_NEEDED, DT_STRTAB, DT_SYMTAB, DT_RELA = 1, 5, 6, 7
DT_FLAGS, DT_FLAGS_1 = 30, 0x6FFFFFFB
DT_GNU_HASH = 0x6FFFFEF5
SHT_DYNSYM, SHT_NOBITS, SHT_GNU_HASH = 11, 8, 0x6FFFFFF6
SHF_ALLOC = 0x2
R_AARCH64_GLOB_DAT, R_AARCH64_RELATIVE = 1025, 1027
PAGE = 0x10000


def _write_elf(entry: Path, output: Path) -> None:
    result = _boot("-f", "exe", "--target", "arm64-linux", "-o", str(output), str(entry))
    assert result.returncode == 0, result.stderr[-4000:]


def _hello(tmp_path: Path) -> Path:
    entry = tmp_path / "hello.gob"
    entry.write_text('fun main() {\n    print("hello, world")\n}\n', encoding="utf-8")
    return entry


def _maths(tmp_path: Path) -> Path:
    entry = tmp_path / "maths.gob"
    entry.write_text("fun main() {\n"
                     "    print(Float.toString(Float.floor(2.5) + Float.fmod(7.5, 2.0)))\n"
                     "}\n", encoding="utf-8")
    return entry


class Elf:
    """An ELF executable's header, program headers, section headers by name,
    and dynamic tags in order."""

    def __init__(self, data: bytes):
        self.data = data
        (self.ident, self.type, self.machine, _, self.entry, phoff, shoff, _, _, phentsize,
         phnum, shentsize, shnum, shstrndx) = struct.unpack_from("<16sHHIQQQIHHHHHH", data, 0)
        assert (phentsize, shentsize) == (56, 64)
        # Each: type, flags, offset, address, file size, memory size, align.
        self.segments = []
        for k in range(phnum):
            kind, flags, offset, address, _, filesz, memsz, align = struct.unpack_from(
                "<IIQQQQQQ", data, phoff + 56 * k)
            self.segments.append((kind, flags, offset, address, filesz, memsz, align))
        raw = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + 64 * k) for k in range(shnum)]
        names = raw[shstrndx][4]
        # Each: name, type, flags, address, offset, size, link, info.
        self.sections = []
        for name, kind, flags, address, offset, size, link, info, _, _ in raw:
            self.sections.append((self.string(names + name), kind, flags, address, offset,
                                  size, link, info))
        dynamic = self.segment(PT_DYNAMIC)
        self.dynamic = [struct.unpack_from("<qQ", data, dynamic[2] + 16 * k)
                        for k in range(dynamic[4] // 16)]

    def string(self, at: int) -> str:
        return self.data[at:self.data.index(b"\0", at)].decode()

    def segment(self, kind: int):
        found = [s for s in self.segments if s[0] == kind]
        assert len(found) == 1, (kind, self.segments)
        return found[0]

    def section(self, name: str):
        found = [s for s in self.sections if s[0] == name]
        assert len(found) == 1, name
        return found[0]

    def tag(self, tag: int) -> int:
        return next(value for t, value in self.dynamic if t == tag)

    def needed(self) -> list[str]:
        strings = self.section(".dynstr")[4]
        return [self.string(strings + value) for t, value in self.dynamic if t == DT_NEEDED]

    def symbols(self, name: str) -> list[tuple[str, int, int, int]]:
        """Each entry of the symbol table `name`: its name, info, section
        index and value."""
        _, _, _, _, offset, size, link, _ = self.section(name)
        strings = self.sections[link][4]
        out = []
        for at in range(offset, offset + size, 24):
            name_at, info, _, section, value, _ = struct.unpack_from("<IBBHQQ", self.data, at)
            out.append((self.string(strings + name_at), info, section, value))
        return out


def test_the_elf_is_what_the_loader_needs(tmp_path):
    executable = tmp_path / "hello"
    _write_elf(_hello(tmp_path), executable)
    elf = Elf(executable.read_bytes())
    assert elf.ident[:8] == b"\x7fELF\x02\x01\x01\x00"
    assert (elf.type, elf.machine) == (3, 183)
    assert [s[0] for s in elf.segments] == [PT_PHDR, PT_INTERP, PT_LOAD, PT_LOAD, PT_LOAD,
                                            PT_LOAD, PT_DYNAMIC, PT_GNU_RELRO, PT_GNU_STACK,
                                            PT_NOTE]
    interp = elf.segment(PT_INTERP)
    assert elf.data[interp[2]:interp[2] + interp[4]] == b"/lib/ld-linux-aarch64.so.1\0"
    assert elf.segment(PT_GNU_STACK)[1] == 6

    loads = [s for s in elf.segments if s[0] == PT_LOAD]
    assert [s[1] for s in loads] == [4, 5, 6, 6]
    assert loads[0][2:4] == (0, 0)
    end = 0
    for _, _, offset, address, filesz, memsz, align in loads:
        assert align == PAGE and address % PAGE == offset % PAGE
        assert filesz <= memsz and offset + filesz <= len(elf.data)
        # Each segment starts on a page of its own, whatever the page size.
        assert address // PAGE * PAGE >= -(-end // PAGE) * PAGE
        end = address + memsz
    # The RELRO segment is the third, and ends on a 64KB page, since the
    # loader rounds its end down.
    relro = elf.segment(PT_GNU_RELRO)
    assert relro[2:6] == loads[2][2:6]
    assert (relro[3] + relro[5]) % PAGE == 0

    # Every loaded section lies inside a segment, in the file and in memory.
    for name, kind, flags, address, offset, size, _, _ in elf.sections[1:]:
        if not flags & SHF_ALLOC:
            assert address == 0, name
            continue
        inside = [s for s in loads if s[3] <= address and address + size <= s[3] + s[5]]
        assert len(inside) == 1, name
        _, _, at, start, filesz, _, _ = inside[0]
        assert offset - at == address - start, name
        if kind != SHT_NOBITS:
            assert address + size <= start + filesz, name

    # The program headers lie in the first segment, the dynamic segment is
    # `.dynamic` exactly, and the entry is in the code segment.
    phdr = elf.segment(PT_PHDR)
    assert loads[0][3] <= phdr[3] and phdr[3] + phdr[5] <= loads[0][3] + loads[0][4]
    assert phdr[2] - loads[0][2] == phdr[3] - loads[0][3]
    dynamic = elf.segment(PT_DYNAMIC)
    _, _, _, address, offset, size, _, _ = elf.section(".dynamic")
    assert dynamic[2:6] == (offset, address, size, size)
    assert loads[1][3] <= elf.entry < loads[1][3] + loads[1][4]

    assert [t for t, _ in elf.dynamic] == [DT_NEEDED, 21, DT_RELA, 8, 9, DT_SYMTAB, 11,
                                           DT_STRTAB, 10, DT_GNU_HASH, DT_FLAGS, DT_FLAGS_1, 0]
    # The tables the loader reads are found by address, so each tag must
    # hold its section's address, not its file offset.
    for tag, name in [(DT_SYMTAB, ".dynsym"), (DT_STRTAB, ".dynstr"), (DT_RELA, ".rela.dyn"),
                      (DT_GNU_HASH, ".gnu.hash")]:
        assert elf.tag(tag) == elf.section(name)[3], name
    assert elf.needed() == ["libc.so.6"]
    assert elf.tag(DT_FLAGS) == 0x8 and elf.tag(DT_FLAGS_1) == 0x08000001

    names = [s[0] for s in elf.sections]
    assert not [n for n in names if n.startswith(".gnu.version")]
    assert not [s for s in elf.sections if s[1] in (0x6FFFFFFD, 0x6FFFFFFE, 0x6FFFFFFF)]
    assert ".plt" not in names and ".rela.plt" not in names

    # One `GLOB_DAT` per import, each filling its slot of the offset table,
    # and nothing else for the loader to relocate.
    dynsym = elf.symbols(".dynsym")
    imports = dynsym[1:]
    assert imports and all(info == 0x12 and section == 0 for _, info, section, _ in imports)
    assert "__libc_start_main" in [n for n, *_ in imports]
    _, _, _, got, _, got_size, _, _ = elf.section(".got")
    _, _, _, _, rela, rela_size, _, _ = elf.section(".rela.dyn")
    relocations = [struct.unpack_from("<QQq", elf.data, at)
                   for at in range(rela, rela + rela_size, 24)]
    assert [info & 0xFFFFFFFF for _, info, _ in relocations] == \
        [R_AARCH64_GLOB_DAT] * len(imports)
    assert R_AARCH64_RELATIVE not in [info & 0xFFFFFFFF for _, info, _ in relocations]
    assert [(offset, info >> 32, addend) for offset, info, addend in relocations] == \
        [(got + 8 * k, k + 1, 0) for k in range(len(imports))]
    assert got_size == 8 * len(imports)

    # An empty GNU hash table: one bucket holding no chain, and a zero bloom
    # word, with the hashed symbols starting past the last one.
    _, _, _, _, hashed, hash_size, link, _ = elf.section(".gnu.hash")
    assert elf.sections[link][0] == ".dynsym" and hash_size == 28
    nbuckets, symoffset, bloom_size, _ = struct.unpack_from("<4I", elf.data, hashed)
    assert (nbuckets, symoffset, bloom_size) == (1, len(dynsym), 1)
    assert elf.data[hashed + 16:hashed + 28] == bytes(12)

    # The entry is `_start`, a global function in `.text`, which calls
    # `__libc_start_main` through its stub.
    symtab = elf.symbols(".symtab")
    starts = [s for s in symtab if s[0] == "_start"]
    assert len(starts) == 1 and starts[0][1] == 0x12 and starts[0][3] == elf.entry
    assert elf.sections[starts[0][2]][0] == ".text"
    _, _, _, symtab_offset, _, _, _, locals_end = elf.section(".symtab")
    assert all(info >> 4 == 0 for _, info, _, _ in symtab[:locals_end])
    assert all(info >> 4 == 1 for _, info, _, _ in symtab[locals_end:])
    assert "main" in [n for n, info, section, _ in symtab[locals_end:] if section != 0]
    assert not [n for n, *_ in symtab if n.startswith(".L")]


def test_the_build_id_hashes_the_elf(tmp_path):
    executable = tmp_path / "hello"
    _write_elf(_hello(tmp_path), executable)
    data = executable.read_bytes()
    elf = Elf(data)
    _, _, offset, _, size, _, _ = elf.segment(PT_NOTE)
    namesz, descsz, kind = struct.unpack_from("<3I", data, offset)
    assert (namesz, descsz, kind, size) == (4, 20, 3, 36)
    assert data[offset + 12:offset + 16] == b"GNU\0"
    unsigned = bytearray(data)
    unsigned[offset + 16:offset + 36] = bytes(20)
    assert data[offset + 16:offset + 36] == hashlib.sha256(unsigned).digest()[:20]


def test_libm_is_needed_only_for_its_names(tmp_path):
    executable = tmp_path / "maths"
    _write_elf(_maths(tmp_path), executable)
    elf = Elf(executable.read_bytes())
    assert elf.needed() == ["libc.so.6", "libm.so.6"]
    imports = [name for name, *_ in elf.symbols(".dynsym")[1:]]
    # Grouped by library, libc's first, each group by name.
    maths = [name for name in imports if name in ("floor", "fmod")]
    assert maths == ["floor", "fmod"] and imports[-2:] == maths
    assert imports[:-2] == sorted(imports[:-2])


def test_an_elf_needs_each_library_by_its_soname(tmp_path):
    executable = tmp_path / "tls"
    _write_elf(_tls(tmp_path), executable)
    elf = Elf(executable.read_bytes())
    needed = elf.needed()
    # libc first, then libm if it is needed, then the others by soname.
    assert needed[0] == "libc.so.6" and needed[-2:] == ["libcrypto.so.3", "libssl.so.3"]
    assert len(needed) == len(set(needed))
    imports = [name for name, *_ in elf.symbols(".dynsym")[1:]]
    ssl = [name for name in imports if name.startswith(("SSL_", "TLS_"))]
    assert ssl and imports[-len(ssl):] == ssl == sorted(ssl)
    assert "ERR_get_error" in imports[:-len(ssl)]


# A block that names one of the C library's own sonames adds its imports to
# that library's group, so the library is still needed once.
def test_a_block_naming_libm_needs_libm_once(tmp_path, probe):
    entry = probe("export (cbrt)\n"
                  'foreign "m" 6 {\n'
                  '    "cbrt" fun cbrt(x : Float) -> Float\n'
                  "}\n",
                  "print(Float.toString(P.cbrt(Float.floor(8.5))))")
    executable = tmp_path / "maths"
    _write_elf(entry, executable)
    elf = Elf(executable.read_bytes())
    assert elf.needed() == ["libc.so.6", "libm.so.6"]
    imports = [name for name, *_ in elf.symbols(".dynsym")[1:]]
    assert imports[-2:] == ["cbrt", "floor"]


def test_one_module_writes_the_same_elf(tmp_path):
    first = tmp_path / "a" / "adt"
    second = tmp_path / "b" / "other"
    first.parent.mkdir()
    second.parent.mkdir()
    _write_elf(PROGRAMS / "adt.gob", first)
    _write_elf(PROGRAMS / "adt.gob", second)
    assert first.read_bytes() == second.read_bytes()


@linux
def test_the_elf_runs(tmp_path):
    for entry, expected in [(_hello(tmp_path), "hello, world\n"),
                            (_maths(tmp_path), "3.5\n"),
                            (PROGRAMS / "adt.gob",
                             (PROGRAMS / "adt.expected").read_text(encoding="utf-8"))]:
        executable = tmp_path / entry.stem
        _write_elf(entry, executable)
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
        assert (ran.returncode, ran.stdout) == (0, expected), ran.stderr


# The executable's references are unversioned, so each binds to its
# library's oldest version of the name. That is right for every name whose
# oldest version is the default, and for these, whose older versions behave
# as the default does: `__libc_start_main` and the three `pthread_*` are one
# function under both versions, and `fmod@GLIBC_2.17` differs from the
# default only in how it sets `errno`. Any other name with an older version
# than its default has to be looked at before it is bound unversioned.
UNVERSIONED_OLDEST = {"__libc_start_main", "pthread_create", "pthread_join",
                      "pthread_attr_setstacksize", "fmod"}


def _foreign_names() -> set[str]:
    """Every C name the standard library declares from the C library."""
    names = {"__libc_start_main"}
    for module in ("Libc", "Entry"):
        text = (REPO_ROOT / "lib" / "Turkey" / f"{module}.gob").read_text(encoding="utf-8")
        names.update(re.findall(r'^foreign "([^"]+)"', text, re.MULTILINE))
    return names


def _versions(library: str) -> dict[str, list[tuple[tuple[int, ...], bool]]]:
    """Each name `library` defines: its versions, as numbers, and whether
    each is the default."""
    path = subprocess.run([*toolchain.cc(), f"-print-file-name={library}"],
                          capture_output=True, text=True, check=True).stdout.strip()
    listing = subprocess.run(["readelf", "--dyn-syms", "--wide", path],
                             capture_output=True, text=True, check=True).stdout
    found: dict[str, list[tuple[tuple[int, ...], bool]]] = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) < 8 or not fields[0].endswith(":") or fields[6] == "UND":
            continue
        named = re.fullmatch(r"([^@]+)(@@?)GLIBC_([\d.]+)", fields[7])
        if named:
            version = tuple(int(part) for part in named.group(3).split("."))
            found.setdefault(named.group(1), []).append((version, named.group(2) == "@@"))
    return found


@linux
def test_every_import_binds_unversioned_to_what_it_means():
    surface = _foreign_names()
    unexpected = []
    for library in ("libc.so.6", "libm.so.6"):
        defined = _versions(library)
        assert {"libc.so.6": "__libc_start_main", "libm.so.6": "fmod"}[library] in defined
        for name, versions in defined.items():
            if name not in surface:
                continue
            oldest = min(versions)
            if not oldest[1] and name not in UNVERSIONED_OLDEST:
                unexpected.append((library, name, sorted(versions)))
    assert not unexpected, unexpected
