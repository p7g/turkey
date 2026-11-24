"""`src/Turkey/Wasm/`: the WebAssembly module writer.

`src/WasmCases.gob` builds small modules with `Turkey.Wasm.Module` and prints
each as hex, then every instruction it has an encoding for, then LEB128
integers. The expected bytes here are written out from the binary format, one
section and one instruction at a time, and not read back from the writer.

An engine, where there is one, checks what the bytes mean: `wasmtime` compiles
each module, which validates it, and runs the WASI program that prints
"hello". Those tests skip when `wasmtime` is not installed.
"""

import functools
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import lang

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "src" / "WasmCases.gob"

WASMTIME = shutil.which("wasmtime") or "wasmtime"
needs_wasmtime = pytest.mark.skipif(shutil.which("wasmtime") is None, reason="needs wasmtime")

MAGIC = "0061736d01000000"


@functools.lru_cache(maxsize=1)
def _lines() -> list[tuple[str, str]]:
    lines = []
    for line in lang.output(DRIVER).splitlines():
        name, hexed = line.split(": ", 1)
        lines.append((name, hexed))
    return lines


def _named(prefix: str) -> list[tuple[str, str]]:
    return [(name, hexed) for name, hexed in _lines() if name == prefix
            or name.startswith(prefix + " ")]


def _module(name: str) -> str:
    [(_, hexed)] = _named(name)
    return hexed


# -- the binary format, written out ---------------------------------------------


def hx(*parts: str) -> str:
    """Hex pieces joined; spaces are only for reading."""
    return "".join(parts).replace(" ", "")


def size(hexed: str) -> str:
    """The unsigned LEB128 of the byte length of `hexed`, for sizes under 16384."""
    n = len(hexed) // 2
    return f"{n:02x}" if n < 128 else f"{n % 128 + 128:02x}{n // 128:02x}"


def section(id_: int, *parts: str) -> str:
    body = hx(*parts)
    return f"{id_:02x}" + size(body) + body


def name_of(s: str) -> str:
    return f"{len(s):02x}" + s.encode().hex()


def names(*functions: str) -> str:
    """The `name` custom section naming functions 0, 1, 2, ... in order."""
    entries = "".join(f"{k:02x}" + name_of(n) for k, n in enumerate(functions))
    sub = f"{len(functions):02x}" + entries
    return section(0, name_of("name"), "01", size(sub), sub)


def body(*parts: str) -> str:
    """A function's code entry: its size, then locals and instructions."""
    code = hx(*parts)
    return size(code) + code


I32, I64, F32, F64, FUNCREF = "7f", "7e", "7d", "7c", "70"


def test_the_empty_module_is_the_header():
    assert _module("empty") == MAGIC


def test_an_exported_function_has_type_function_export_code_and_name_sections():
    assert _module("add") == hx(
        MAGIC,
        section(1, "01", "60", "02", I32, I32, "01", I32),
        section(3, "01", "00"),
        section(7, "01", name_of("add"), "00", "00"),
        section(10, "01", body("00", "2000 2001 6a 0b")),
        names("add"),
    )


def test_every_section_but_the_imports_comes_in_the_formats_order():
    assert _module("sections") == hx(
        MAGIC,
        # One type, shared by both functions.
        section(1, "01", "60", "00", "01", I32),
        section(3, "02", "00", "00"),
        # A funcref table of exactly two, a memory of one to two pages, and a
        # mutable i64 global starting at -1.
        section(4, "01", FUNCREF, "01", "02", "02"),
        section(5, "01", "01", "01", "02"),
        section(6, "01", I64, "01", "42 7f 0b"),
        section(7, "04",
                name_of("table"), "01", "00",
                name_of("memory"), "02", "00",
                name_of("counter"), "03", "00",
                name_of("two"), "00", "01"),
        # i32.const 0, then the functions 0 and 1.
        section(9, "01", "00", "41 00 0b", "02", "00", "01"),
        section(10, "02", body("00", "41 01 0b"), body("00", "41 02 0b")),
        # Offsets 16 and 300; 300 is the signed LEB128 `ac 02`.
        section(11, "02",
                "00", "41 10 0b", "02", "07 08",
                "00", "41 ac02 0b", "01", "09"),
        names("one", "two"),
    )


def test_imports_come_first_in_each_index_space():
    assert _module("imports") == hx(
        MAGIC,
        section(1, "02", "60 01", I32, "00", "60 00 00"),
        section(2, "04",
                name_of("env"), name_of("log"), "00", "00",
                name_of("env"), name_of("table"), "01", FUNCREF, "00", "01",
                name_of("env"), name_of("memory"), "02", "00", "01",
                name_of("env"), name_of("base"), "03", I32, "00"),
        # `main` has type 1 and function index 1, after the imported `log`.
        section(3, "01", "01"),
        # The defined global is index 1, after the imported `base`.
        section(6, "01", I32, "00", "23 00 0b"),
        section(7, "02", name_of("main"), "00", "01", name_of("local"), "03", "01"),
        # global.get 0, call 0.
        section(10, "01", body("00", "23 00 10 00 0b")),
        names("env.log", "main"),
    )


def test_control_flow_multi_value_blocks_and_locals_in_runs():
    # (i32) -> (i32, i32) is type 0 and (i32) -> i32 is type 1.
    assert _module("control") == hx(
        MAGIC,
        section(1, "02", "60 01", I32, "02", I32, I32, "60 01", I32, "01", I32),
        section(3, "01", "01"),
        section(7, "01", name_of("control"), "00", "00"),
        section(10, "01", body(
            # Locals i32 i32 i64 i32 i32 are three runs.
            "03", "02", I32, "01", I64, "02", I32,
            "2000", "02 00", "2000", "0b", "6a", "1a",
            "03 40", "2000", "0d 00", "0b",
            "02 40", "02 40", "2000", "0e 02 00 01 01", "0b", "0b",
            "2000", "04", I32, "41 01", "05", "41 02", "0b",
            "0b")),
        names("control"),
    )


def test_tail_calls_by_index_and_through_a_table():
    assert _module("tail") == hx(
        MAGIC,
        section(1, "01", "60 01", I32, "01", I32),
        section(3, "02", "00", "00"),
        section(4, "01", FUNCREF, "00", "01"),
        section(9, "01", "00", "41 00 0b", "01", "00"),
        section(10, "02",
                # return_call 0, then return_call_indirect type 0 table 0.
                body("00", "2000", "12 00", "0b"),
                body("00", "2000", "41 00", "13 00 00", "0b")),
        names("go", "indirect"),
    )


def test_typed_function_references():
    # `(ref null 0)` is `63 00` and `(ref 0)` is `64 00`.
    assert _module("refs") == hx(
        MAGIC,
        section(1, "02", "60 01", I32, "01", I32, "60 02", I32, "63 00", "01", I32),
        section(3, "02", "00", "01"),
        section(7, "01", name_of("go"), "00", "00"),
        section(10, "02",
                # ref.func 0, return_call_ref type 0.
                body("00", "2000", "d2 00", "15 00", "0b"),
                body("01 01 64 00", "2000", "2001", "14 00", "0b")),
        names("go", "call"),
    )


def test_a_wasi_program_that_writes_hello():
    text = "hello\n".encode().hex()
    wasi = "wasi_snapshot_preview1"
    assert _module("hello") == hx(
        MAGIC,
        section(1, "02", "60 04", I32, I32, I32, I32, "01", I32, "60 00 00"),
        section(2, "01", name_of(wasi), name_of("fd_write"), "00", "00"),
        section(3, "01", "01"),
        section(5, "01", "00", "01"),
        section(7, "02", name_of("memory"), "02", "00", name_of("_start"), "00", "01"),
        # fd_write(1, 0, 1, 20), dropped.
        section(10, "01", body("00", "41 01 41 00 41 01 41 14 10 00 1a 0b")),
        # The iovec {8, 6} at 0 and the text at 8.
        section(11, "01", "00", "41 00 0b", "0e", "08000000 06000000", text),
        names(f"{wasi}.fd_write", "_start"),
    )


# -- LEB128 ------------------------------------------------------------------------


@pytest.mark.parametrize("n, expected", [
    (0, "00"), (1, "01"), (127, "7f"), (128, "8001"), (255, "ff01"), (300, "ac02"),
    (16384, "808001"), (624485, "e58e26"), (0xFFFFFFFF, "ffffffff0f"),
])
def test_unsigned_leb128(n, expected):
    [(_, hexed)] = _named(f"unsigned {n}")
    assert hexed == expected


@pytest.mark.parametrize("n, expected", [
    (0, "00"), (1, "01"), (-1, "7f"), (63, "3f"), (64, "c000"), (-64, "40"),
    (-65, "bf7f"), (127, "ff00"), (-128, "807f"), (-123456, "c0bb78"),
    (2**63 - 1, "ff" * 9 + "00"), (-(2**63), "80" * 9 + "7f"),
])
def test_signed_leb128(n, expected):
    [(_, hexed)] = _named(f"signed {n}")
    assert hexed == expected


# -- single instructions -------------------------------------------------------------


def _every_op() -> list[str]:
    """The instruction table of the binary format, in the order `everyOp` lists."""
    ops = ["00", "01", "1a", "1b"]
    loads = [0x28 + k for k in range(14)]
    load_align = [2, 3, 2, 3, 0, 0, 1, 1, 0, 0, 1, 1, 2, 2]
    ops += [f"{o:02x}{a:02x}04" for o, a in zip(loads, load_align)]
    stores = [0x36 + k for k in range(9)]
    store_align = [2, 3, 2, 3, 0, 1, 0, 1, 2]
    ops += [f"{o:02x}{a:02x}04" for o, a in zip(stores, store_align)]
    ops += ["3f00", "4000", "4105", "4205", "43" + "0000c03f", "44" + "000000000000f83f"]
    ops += ["45", "50"]
    for first in (0x46, 0x51):  # i32 then i64: eq ne lt_s lt_u gt_s gt_u le_s le_u ge_s ge_u
        ops += [f"{first + k:02x}" for k in range(10)]
    for first in (0x5B, 0x61):  # f32 then f64: eq ne lt gt le ge
        ops += [f"{first + k:02x}" for k in range(6)]
    for first in (0x67, 0x79):  # clz ctz popcnt
        ops += [f"{first + k:02x}" for k in range(3)]
    for first in (0x6A, 0x7C):  # add sub mul div_s div_u rem_s rem_u and or xor shl shr_s shr_u rotl rotr
        ops += [f"{first + k:02x}" for k in range(15)]
    for first in (0x8B, 0x99):  # abs neg ceil floor trunc nearest sqrt
        ops += [f"{first + k:02x}" for k in range(7)]
    for first in (0x92, 0xA0):  # add sub mul div min max copysign
        ops += [f"{first + k:02x}" for k in range(7)]
    ops += [f"{0xA7 + k:02x}" for k in range(0xC4 - 0xA7 + 1)]
    ops += [f"fc{k:02x}" for k in range(8)]
    ops += ["fc0a0000", "fc0b00", "d070", "d1", "d200"]
    return ops


_OTHER_OPS = [
    "0240", "027f", "0203", "0340", "0440", "05", "0b",
    "0c01", "0d02", "0e02000102", "0e0000", "0f",
    "1005", "110300", "1404", "12c801", "130301", "1504",
    "d002", "2000", "2101", "22c801", "2302", "24ac02",
    "280200", "2903ac02", "2d0001", "320101", "350201",
    "360208", "390308", "3a0008", "3d0108", "3e0208",
    "4100", "417f", "413f", "41c000", "4140", "41bf7f", "41ffffffff07",
    "418080808078", "417f", "418080808078",
    "4200", "427f", "42" + "ff" * 9 + "00", "42" + "80" * 9 + "7f",
    "43" + "00" * 4, "43ffffffff", "44" + "00" * 8, "44" + "00" * 6 + "04c0",
]


def test_every_instruction_encodes_as_the_binary_format_numbers_it():
    ops = [hexed for name, hexed in _lines() if name == "op"]
    assert ops == _every_op() + _OTHER_OPS


# -- an engine ---------------------------------------------------------------------


def _write(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.wasm"
    path.write_bytes(bytes.fromhex(_module(name)))
    return path


@needs_wasmtime
@pytest.mark.parametrize("name", ["empty", "add", "sections", "imports", "control", "tail",
                                  "hello", "ops"])
def test_wasmtime_validates_each_module(tmp_path, name):
    result = subprocess.run([WASMTIME, "compile", "-o", str(tmp_path / "out.cwasm"),
                             str(_write(tmp_path, name))], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@needs_wasmtime
def test_wasmtime_runs_the_wasi_program_that_prints_hello(tmp_path):
    result = subprocess.run([WASMTIME, "run", str(_write(tmp_path, "hello"))],
                            capture_output=True, text=True)
    assert (result.returncode, result.stdout) == (0, "hello\n"), result.stderr


@needs_wasmtime
def test_wasmtime_runs_the_exported_add(tmp_path):
    result = subprocess.run([WASMTIME, "run", "--invoke", "add",
                             str(_write(tmp_path, "add")), "2", "40"],
                            capture_output=True, text=True)
    assert result.stdout.strip() == "42", result.stderr
