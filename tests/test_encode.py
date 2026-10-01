"""The `listing` dump: the encoder's words, checked against the system
assembler.

The oracle is exact and needs no list of instructions to test. `boot build`
writes a program's code as assembly and the `listing` dump lists the words the
encoder makes of the same instructions; the assembler turns the printed text into an object, and
its code section has to equal the listing word for word. So every instruction
form the backend produces is checked, in the programs that produce it, and a
mismatch names the instruction.

A field a fixup fills in -- a branch offset, a page, an offset within one -- is
masked in both words: the encoder leaves it zero for layout to fill, and the
assembler either leaves it to the linker or resolves it against a layout of
its own. Everything else in the word is compared.

The assembler is `$TURKEY_CC -c`, so on Linux it is the GNU or LLVM one rather
than Apple's, and the check is for whichever target `boot` was built for.
"""

from __future__ import annotations

import struct
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import bootc, toolchain

REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRAMS = REPO_ROOT / "tests" / "programs"

CORPUS = sorted(
    path.name for path in PROGRAMS.glob("*.gob")
    if not path.name.startswith("err_")
)


def _split(text: str, paths: list[Path]) -> list[str]:
    modules = bootc.split_before(text, "// === ")
    return [modules[p.name] for p in paths]


def _code(obj: bytes) -> bytes:
    """The code section of a 64-bit little-endian Mach-O or ELF object."""
    if obj[:4] == b"\xcf\xfa\xed\xfe":
        (ncmds,) = struct.unpack_from("<I", obj, 16)
        at = 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", obj, at)
            if cmd == 0x19:  # LC_SEGMENT_64
                (nsects,) = struct.unpack_from("<I", obj, at + 64)
                for k in range(nsects):
                    section = at + 72 + 80 * k
                    if obj[section:section + 16].rstrip(b"\0") == b"__text":
                        (length,) = struct.unpack_from("<Q", obj, section + 40)
                        (offset,) = struct.unpack_from("<I", obj, section + 48)
                        return obj[offset:offset + length]
            at += size
    elif obj[:4] == b"\x7fELF":
        (shoff,) = struct.unpack_from("<Q", obj, 0x28)
        entsize, count, names = struct.unpack_from("<HHH", obj, 0x3A)

        def header(i: int) -> tuple:
            return struct.unpack_from("<IIQQQQIIQQ", obj, shoff + i * entsize)

        strings = header(names)[4]
        for i in range(count):
            name, _, _, _, offset, length, *_ = header(i)
            if obj[strings + name:].split(b"\0", 1)[0] == b".text":
                return obj[offset:offset + length]
    raise AssertionError("the object has no code section")


def _assembled(text: str) -> list[int]:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "t.s"
        obj = Path(directory) / "t.o"
        source.write_text(text, encoding="utf-8")
        result = subprocess.run(
            [*toolchain.cc(), "-c", "-o", str(obj), str(source)],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[:4000]
        code = _code(obj.read_bytes())
    return list(struct.unpack(f"<{len(code) // 4}I", code))


def _compare(listing: str, native: str) -> None:
    ours = [line.split(" ", 2) for line in listing.splitlines()
            if line and not line.startswith("//")]
    theirs = _assembled(native)
    wrong = []
    for k, (word, mask, text) in enumerate(ours[:len(theirs)]):
        w, m = int(word, 16), int(mask, 16)
        if w & m != theirs[k] & m:
            wrong.append(f"word {k}: {text}: encoded {w:08x}, "
                         f"assembled {theirs[k]:08x}, compared {m:08x}")
    assert not wrong, f"{len(wrong)} words differ:\n" + "\n".join(wrong[:20])
    assert len(ours) == len(theirs), (
        f"{len(ours)} words encoded, {len(theirs)} assembled")


def _both(paths: list[Path]) -> tuple[dict[Path, str], dict[Path, str]]:
    return (bootc.boot_each("listing", paths, _split),
            bootc.boot_each("asm", paths, _split))


@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
@pytest.mark.parametrize("name", CORPUS)
def test_the_encoder_agrees_with_the_assembler(name):
    paths = [PROGRAMS / n for n in CORPUS]
    listings, natives = _both(paths)
    path = PROGRAMS / name
    _compare(listings[path], natives[path])


@pytest.mark.skipif(toolchain.missing(), reason="no C compiler")
def test_the_encoder_agrees_with_the_assembler_on_the_compiler():
    """`boot` itself: two million words, and every instruction form the
    corpus is too small to reach."""
    listings, natives = _both([bootc.BOOT_MAIN])
    _compare(listings[bootc.BOOT_MAIN], natives[bootc.BOOT_MAIN])
