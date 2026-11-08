"""The executable writer: `boot build -f exe` writes a signed Mach-O that the
loader takes as it takes the linker's, with nothing linked.

`scripts/test.sh --exe` runs every program through this path against the
same goldens, which is what decides whether the image is right. These tests
check what a run cannot see: that the signature verifies, that the loader is
asked for the binds the linker would ask for and for no rebase, that the
program starts where the linker's does, that a module always writes the same
bytes, and what is refused.

Most run only on arm64-darwin, with the system's `codesign` and `dyld_info`
as the oracles. One runs on any host: the executable is written for
arm64-darwin wherever `boot` runs, and the signature and the UUID are checked
against Python's own SHA-256, so a Linux run checks that writing it needs
nothing from a Mac.
"""

from __future__ import annotations

import hashlib
import os
import re
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


def _boot(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(toolchain.command(bootc.binary(), "build", *args),
                          capture_output=True, text=True, env=ENV, timeout=600)


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


def test_a_program_that_needs_openssl_is_refused(tmp_path):
    result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(tmp_path / "tls"),
                   str(_tls(tmp_path)))
    assert result.returncode == 2
    assert re.search(r"it calls '\w+' from OpenSSL's libssl and libcrypto", result.stderr), \
        result.stderr
    assert "-f obj" in result.stderr


# The output is written only once its bytes are complete, so a build that
# fails leaves what was there before, and nothing beside it.
def test_a_refused_build_leaves_the_output_as_it_was(tmp_path):
    entry = _tls(tmp_path)
    existing = tmp_path / "existing"
    existing.write_bytes(b"before")
    for output in (existing, tmp_path / "absent"):
        result = _boot("-f", "exe", "--target", "arm64-darwin", "-o", str(output), str(entry))
        assert result.returncode == 2, result.stderr
    assert existing.read_bytes() == b"before"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing", "tls.gob"]


def test_an_executable_is_written_only_when_asked_for(tmp_path):
    result = _boot("--target", "arm64-darwin", "-o", str(tmp_path / "adt"),
                   str(PROGRAMS / "adt.gob"))
    assert result.returncode == 2
    assert "only with -f exe" in result.stderr


def test_an_executable_with_debug_information_is_refused(tmp_path):
    result = _boot("-f", "exe", "--target", "arm64-darwin", "-g", "-o", str(tmp_path / "adt"),
                   str(PROGRAMS / "adt.gob"))
    assert result.returncode == 2
    assert "no debug information" in result.stderr


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
