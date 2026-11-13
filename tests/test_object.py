"""The object writer: `boot`'s relocatable object means what the system
assembler's means for the same program.

Not the same bytes. The two may spell a reference differently -- the assembler
reaches a label in another section through a section-start symbol and an
addend, `boot` through a symbol of the label's own for a page relocation and
through the label's section for an address word; the assembler leaves a call
to a global function in the same section to the linker, `boot` fills it in --
and either is correct. What has to agree is what the linker makes of them.

So each object is linked here, roughly: its sections gathered by name in file
order, each output section placed at an address chosen by its name alone, and
each undefined symbol given an address chosen by its name alone. Then every
relocation is applied, and the two objects must give the same bytes in every
output section and define the same named symbols at the same addresses, with
the same binding, and leave the same symbols undefined.

Only for the target `boot` was built for, since the assembler is
`$TURKEY_CC -c`. `tests/lang.py` links every behavioral test's program from
`boot`'s object, which is the check that decides; this one says where a
difference is when that check fails.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests import bootc, toolchain

REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = REPO_ROOT / "tests" / "programs"

CORPUS = [path.name for path in bootc.corpus()]


@dataclass
class Section:
    name: str
    align: int
    data: bytearray
    # Offset of this input section in its output section, once placed.
    at: int = 0


@dataclass
class Symbol:
    name: str
    # None when undefined.
    section: int | None
    value: int
    exported: bool
    # Whether the symbol is something a program or a tool names, rather than
    # an assembler's or this writer's own label.
    named: bool


@dataclass
class Reloc:
    section: int
    offset: int
    kind: str
    # Either a symbol index, or a section index for a section-relative target.
    symbol: int | None
    target_section: int | None
    addend: int
    # The symbol subtracted from the target, for a `delta64` or a `delta32`:
    # a Mach-O SUBTRACTOR's.
    minus: int | None = None


@dataclass
class Obj:
    sections: list[Section] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    relocs: list[Reloc] = field(default_factory=list)


def _cstr(data: bytes, at: int) -> str:
    return data[at:data.index(b"\0", at)].decode("utf-8", "surrogateescape")


def _macho(data: bytes) -> Obj:
    obj = Obj()
    (ncmds,) = struct.unpack_from("<I", data, 16)
    at = 32
    addresses = []
    raw_relocs = []
    symoff = nsyms = stroff = 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, at)
        if cmd == 0x19:
            (nsects,) = struct.unpack_from("<I", data, at + 64)
            for k in range(nsects):
                h = at + 72 + 80 * k
                sect = data[h:h + 16].rstrip(b"\0").decode()
                seg = data[h + 16:h + 32].rstrip(b"\0").decode()
                addr, length, offset, align, reloff, nreloc = struct.unpack_from(
                    "<QQIIII", data, h + 32)
                obj.sections.append(Section(f"{seg},{sect}", 1 << align,
                                            bytearray(data[offset:offset + length])))
                addresses.append(addr)
                raw_relocs.append((len(obj.sections) - 1, reloff, nreloc))
        elif cmd == 0x2:
            symoff, nsyms, stroff, _ = struct.unpack_from("<IIII", data, at + 8)
        at += size
    for k in range(nsyms):
        strx, ntype, sect, _, value = struct.unpack_from("<IBBHQ", data, symoff + 16 * k)
        name = _cstr(data, stroff + strx)
        if ntype & 0xE == 0xE:
            index: int | None = sect - 1
            value -= addresses[sect - 1]
        else:
            index, value = None, 0
        obj.symbols.append(Symbol(name, index, value, bool(ntype & 1),
                                  not name.startswith(("L", "l"))))
    for index, reloff, nreloc in raw_relocs:
        addend = 0
        minus = None
        for k in range(nreloc):
            address, info = struct.unpack_from("<iI", data, reloff + 8 * k)
            number = info & 0xFFFFFF
            extern = (info >> 27) & 1
            kind = info >> 28
            if kind == 10:  # ARM64_RELOC_ADDEND, for the relocation after it
                addend = number if number < 0x800000 else number - 0x1000000
                continue
            if kind == 1:  # ARM64_RELOC_SUBTRACTOR, for the UNSIGNED after it
                minus = number
                continue
            name = {0: "abs64", 2: "branch26", 3: "page21", 4: "pageoff12"}.get(kind)
            assert name, f"relocation type {kind}"
            section = obj.sections[index]
            if name == "abs64" and minus is not None and (info >> 25) & 3 == 2:
                # A four-byte difference: the frame table's return addresses.
                (implicit,) = struct.unpack_from("<i", section.data, address)
                struct.pack_into("<i", section.data, address, 0)
                obj.relocs.append(Reloc(index, address, "delta32", number, None,
                                        implicit, minus))
                minus = None
                continue
            if name == "abs64":
                (implicit,) = struct.unpack_from("<q", section.data, address)
                struct.pack_into("<q", section.data, address, 0)
                if minus is not None:
                    obj.relocs.append(Reloc(index, address, "delta64", number, None,
                                            implicit, minus))
                    minus = None
                    continue
                if not extern:
                    # Non-extern: the word holds the target's object address.
                    target = number - 1
                    obj.relocs.append(Reloc(index, address, name, None, target,
                                            implicit - addresses[target]))
                    continue
                addend = implicit
            assert extern, "a non-extern code relocation"
            obj.relocs.append(Reloc(index, address, name, number, None, addend))
            addend = 0
    return obj


ELF_KINDS = {257: "abs64", 261: "prel32", 282: "branch26", 283: "branch26", 280: "branch19",
             275: "page21", 277: "pageoff12", 278: "pageoff12", 284: "pageoff12",
             285: "pageoff12", 286: "pageoff12", 299: "pageoff12"}


def _elf(data: bytes) -> Obj:
    obj = Obj()
    (shoff,) = struct.unpack_from("<Q", data, 0x28)
    entsize, count, names = struct.unpack_from("<HHH", data, 0x3A)
    headers = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * entsize)
               for i in range(count)]
    shstr = headers[names][4]
    # ELF section index to ours, for the sections a program's bytes are in.
    ours: dict[int, int] = {}
    for i, (name, kind, flags, _, offset, size, *_) in enumerate(headers):
        align = headers[i][8]
        if kind == 1 and flags & 2:  # PROGBITS, allocated
            ours[i] = len(obj.sections)
            obj.sections.append(Section(_cstr(data, shstr + name), max(align, 1),
                                        bytearray(data[offset:offset + size])))
    symbols: list[tuple[str, int, int, int]] = []
    for name, kind, _, _, offset, size, link, *_ in headers:
        if kind == 2:
            strings = headers[link][4]
            for k in range(size // 24):
                st_name, info, _, shndx, value, _ = struct.unpack_from(
                    "<IBBHQQ", data, offset + 24 * k)
                symbols.append((_cstr(data, strings + st_name), info, shndx, value))
    # Section symbols become section-relative targets; the rest are symbols.
    index_of: dict[int, int] = {}
    for k, (name, info, shndx, value) in enumerate(symbols):
        if k == 0 or info & 0xF == 3:
            continue
        index_of[k] = len(obj.symbols)
        defined = shndx != 0
        obj.symbols.append(Symbol(
            name, ours.get(shndx) if defined else None, value, info >> 4 == 1,
            not name.startswith((".L", "$"))))
    for name, kind, _, _, offset, size, link, info, *_ in headers:
        if kind != 4 or info not in ours:
            continue
        for k in range(size // 24):
            r_offset, r_info, addend = struct.unpack_from("<QQq", data, offset + 24 * k)
            sym, kind_number = r_info >> 32, r_info & 0xFFFFFFFF
            kind_name = ELF_KINDS.get(kind_number)
            assert kind_name, f"relocation type {kind_number}"
            _, sinfo, shndx, _ = symbols[sym]
            if sinfo & 0xF == 3:
                obj.relocs.append(Reloc(ours[info], r_offset, kind_name, None,
                                        ours[shndx], addend))
            else:
                obj.relocs.append(Reloc(ours[info], r_offset, kind_name, index_of[sym],
                                        None, addend))
    return obj


def _parse(data: bytes) -> Obj:
    if data[:4] == b"\xcf\xfa\xed\xfe":
        return _macho(data)
    if data[:4] == b"\x7fELF":
        return _elf(data)
    raise AssertionError("neither Mach-O nor ELF")


def _scale(word: int) -> int:
    """The access size of a load or store with a 12-bit unsigned offset, as a
    power of two; 0 for an `add`."""
    if word & 0x3B000000 == 0x39000000:
        size = word >> 30
        if word & (1 << 26) and word & (1 << 23):
            return 4
        return size
    return 0


@dataclass
class Linked:
    contents: dict[str, bytes]
    defined: dict[str, tuple[str, int, bool]]
    undefined: set[str]


def _link(obj: Obj) -> Linked:
    names = sorted({s.name for s in obj.sections})
    base = {name: 0x10000000 * (k + 1) for k, name in enumerate(names)}
    size = dict.fromkeys(names, 0)
    for s in obj.sections:
        at = (size[s.name] + s.align - 1) // s.align * s.align
        s.at = at
        size[s.name] = at + len(s.data)

    def address(section: int, offset: int) -> int:
        s = obj.sections[section]
        return base[s.name] + s.at + offset

    def symbol_address(sym: Symbol) -> int:
        if sym.section is None:
            return 0x7000000000 + (zlib.crc32(sym.name.encode()) << 12)
        return address(sym.section, sym.value)

    for r in obj.relocs:
        if r.symbol is not None:
            target = symbol_address(obj.symbols[r.symbol]) + r.addend
        else:
            assert r.target_section is not None
            target = address(r.target_section, r.addend)
        place = address(r.section, r.offset)
        data = obj.sections[r.section].data
        if r.kind == "abs64":
            struct.pack_into("<Q", data, r.offset, target)
            continue
        if r.kind == "delta64":
            assert r.minus is not None
            struct.pack_into("<q", data, r.offset,
                             target - symbol_address(obj.symbols[r.minus]))
            continue
        if r.kind == "delta32":
            assert r.minus is not None
            struct.pack_into("<i", data, r.offset,
                             target - symbol_address(obj.symbols[r.minus]))
            continue
        if r.kind == "prel32":
            struct.pack_into("<i", data, r.offset, target - place)
            continue
        (word,) = struct.unpack_from("<I", data, r.offset)
        if r.kind == "branch26":
            word |= ((target - place) >> 2) & 0x3FFFFFF
        elif r.kind == "branch19":
            word |= (((target - place) >> 2) & 0x7FFFF) << 5
        elif r.kind == "page21":
            pages = (target >> 12) - (place >> 12)
            word |= (pages & 3) << 29 | ((pages >> 2) & 0x7FFFF) << 5
        elif r.kind == "pageoff12":
            word |= ((target & 0xFFF) >> _scale(word)) << 10
        struct.pack_into("<I", data, r.offset, word)

    contents = {name: bytearray(size[name]) for name in names}
    for s in obj.sections:
        contents[s.name][s.at:s.at + len(s.data)] = s.data
    defined = {}
    undefined = set()
    for sym in obj.symbols:
        if not sym.named:
            continue
        if sym.section is None:
            undefined.add(sym.name)
        else:
            defined[sym.name] = (obj.sections[sym.section].name,
                                 symbol_address(sym), sym.exported)
    return Linked({k: bytes(v) for k, v in contents.items()}, defined, undefined)


def _objects(entry: Path) -> tuple[bytes, bytes]:
    """`boot`'s object for the program, and the assembler's from `boot`'s
    assembly."""
    with tempfile.TemporaryDirectory() as directory:
        ours = Path(directory) / "ours.o"
        source = Path(directory) / "t.s"
        theirs = Path(directory) / "theirs.o"
        env = dict(os.environ, TURKEY_LIB=str(REPO_ROOT / "lib"))
        for args in (["-f", "obj", "-o", str(ours)], ["-f", "asm", "-o", str(source)]):
            result = subprocess.run(
                toolchain.command(bootc.binary(), "build", *args, str(entry)),
                capture_output=True, env=env, timeout=600)
            assert result.returncode == 0, result.stderr.decode()[:4000]
        result = subprocess.run([*toolchain.cc(), "-c", "-o", str(theirs), str(source)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[:4000]
        return ours.read_bytes(), theirs.read_bytes()


def _compare(entry: Path) -> None:
    ours_bytes, theirs_bytes = _objects(entry)
    ours, theirs = _link(_parse(ours_bytes)), _link(_parse(theirs_bytes))
    assert ours.contents.keys() == theirs.contents.keys()
    for name in ours.contents:
        a, b = ours.contents[name], theirs.contents[name]
        if a != b:
            first = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]),
                         min(len(a), len(b)))
            word = first // 4 * 4
            raise AssertionError(
                f"{name}: {len(a)} bytes against {len(b)}, first difference at "
                f"{first:#x}: {a[word:word + 4].hex()} against {b[word:word + 4].hex()}")
    missing = theirs.defined.keys() - ours.defined.keys()
    extra = ours.defined.keys() - theirs.defined.keys()
    assert not missing and not extra, (sorted(missing)[:10], sorted(extra)[:10])
    wrong = {n: (ours.defined[n], theirs.defined[n]) for n in ours.defined
             if ours.defined[n] != theirs.defined[n]}
    assert not wrong, list(wrong.items())[:10]
    assert ours.undefined == theirs.undefined


@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
@pytest.mark.parametrize("name", CORPUS)
def test_the_object_means_what_the_assemblers_does(name):
    _compare(PROGRAMS / name)


@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
def test_the_object_means_what_the_assemblers_does_on_the_compiler():
    _compare(bootc.BOOT_MAIN)


# The linker records every symbol in the code as a function start, and a
# profiler or symbolicator ends a function at the next start. So a label
# inside a function -- a safepoint's return address, which the frame table
# holds -- must reach the linker without a symbol of its own.
@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
def test_no_label_in_the_code_has_a_symbol():
    obj = _parse(_objects(PROGRAMS / "adt.gob")[0])
    labels = [s.name for s in obj.symbols
              if not s.named and s.section is not None
              and obj.sections[s.section].name in ("__TEXT,__text", ".text")]
    assert not labels, labels[:10]


def _ours(entry: Path) -> bytes:
    """`boot`'s object for the program, alone."""
    with tempfile.TemporaryDirectory() as directory:
        ours = Path(directory) / "ours.o"
        env = dict(os.environ, TURKEY_LIB=str(REPO_ROOT / "lib"))
        result = subprocess.run(
            toolchain.command(bootc.binary(), "build", "-f", "obj", "-o", str(ours),
                              str(entry)),
            capture_output=True, env=env, timeout=600)
        assert result.returncode == 0, result.stderr.decode()[:4000]
        return ours.read_bytes()


# A loaded section that holds an absolute address costs the loader a rebase
# per address at every start, and the linker writes one without complaint, so
# nothing else notices one coming back. `__DWARF` stays in the object and
# `__LD` is the linker's own; the ELF reader keeps only allocated sections.
@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
def test_no_loaded_section_of_the_compiler_holds_an_absolute_address():
    obj = _parse(_ours(bootc.BOOT_MAIN))
    absolute = [(obj.sections[r.section].name, hex(r.offset)) for r in obj.relocs
                if r.kind == "abs64"
                and not obj.sections[r.section].name.startswith(("__DWARF,", "__LD,"))]
    assert not absolute, absolute[:10]


# The frame table holds no relocated field, so it is read-only: its pages are
# shared and never written at load.
@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
def test_the_frame_table_is_read_only():
    obj = _parse(_ours(PROGRAMS / "adt.gob"))
    tables = [s for s in obj.symbols if s.name == toolchain.c_symbol("turkey_frame_table")]
    assert len(tables) == 1 and tables[0].section is not None, tables
    assert obj.sections[tables[0].section].name in ("__TEXT,__const", ".rodata")


@pytest.mark.skipif(toolchain.target() != "arm64-darwin" or not shutil.which("dyld_info"),
                    reason="dyld_info reads Mach-O executables")
def test_the_loader_applies_binds_only(tmp_path):
    executable = tmp_path / "adt"
    bootc.link(PROGRAMS / "adt.gob", executable,
               env=dict(os.environ, TURKEY_LIB=str(REPO_ROOT / "lib")))
    result = subprocess.run(["dyld_info", "-fixups", str(executable)],
                            capture_output=True, text=True, check=True)
    kinds = [line.split()[3] for line in result.stdout.splitlines()
             if len(line.split()) >= 5 and line.split()[0].startswith("__")]
    assert kinds and set(kinds) == {"bind"}, sorted(set(kinds))
