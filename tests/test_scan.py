"""`Turkey.Scan`: the newline search and the UTF-8 check, against what
Python finds in the same bytes.

The cases put the interesting byte at every offset across a few words, so that
each of the UTF-8 check's paths -- 32 bytes at a time, a word at a time, the
bytes left over -- is the one that meets it.
"""

from __future__ import annotations

import os
import random

import pytest

from tests import bootc
from tests.lang import run


@pytest.fixture(autouse=True)
def internal(monkeypatch):
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       os.environ[bootc.INTERNAL_IMPORTS] + ",Turkey.Scan")


def literal(data: bytes) -> str:
    return "[" + ", ".join(f"Byte.truncate({b})" for b in data) + "]"


def outputs(cases: list[bytes], call: str) -> list[str]:
    body = "\n".join(f"    check({literal(case)})" for case in cases)
    src = f"""
import Turkey.Scan as Scan

fun check(bytes : Array Byte) {{
    print({call})
}}

fun main() {{
{body}
}}
"""
    result = run(src)
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


def test_newline_is_found_at_every_offset():
    cases = []
    for length in (0, 1, 7, 8, 9, 31, 33, 70):
        cases.append(b"x" * length)
        for at in range(length):
            data = bytearray(b"y" * length)
            data[at] = 10
            if at + 3 < length:
                data[at + 3] = 10
            if at > 0:
                data[at - 1] = 11
            cases.append(bytes(data))
    out = outputs(cases, "Scan.newline(bytes, 0, len(bytes))")
    assert out == [str(case.find(b"\n")) for case in cases]


def test_newline_searches_only_its_window():
    out = outputs([b"\nabcdefghijklmnopq\nrst"],
                  "(Scan.newline(bytes, 1, 18), Scan.newline(bytes, 1, 19),"
                  " Scan.newline(bytes, 19, 22))")
    assert out == ["(-1, 18, -1)"]


def valid(data: bytes) -> bool:
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    return True


def test_well_formed_agrees_with_python():
    rng = random.Random(331)
    pieces = ["a", "é", "€", "𝄞", "߿", "￿", "\U0010ffff"]
    bad = [b"\x80", b"\xc0\xaf", b"\xe0\x80\xaf", b"\xed\xa0\x80", b"\xf4\x90\x80\x80",
           b"\xf5", b"\xff", b"\xe2\x82", b"\xc3"]
    cases = []
    for length in (0, 5, 8, 31, 32, 33, 40, 64, 65, 100):
        for _ in range(6):
            text = "".join(rng.choice(pieces) if rng.random() < 0.2 else "a"
                           for _ in range(length))
            data = text.encode("utf-8")
            cases.append(data)
            at = rng.randrange(len(data) + 1)
            cases.append(data[:at] + rng.choice(bad) + data[at:])
    # A long ASCII run with one bad byte at each offset across the strides.
    for at in range(70):
        data = bytearray(b"q" * 70)
        data[at] = 0x80 | (at % 64)
        cases.append(bytes(data))
    src_call = "Option.isSome(String.fromBytes(bytes))"
    out = outputs(cases, src_call)
    assert out == [str(valid(case)) for case in cases]
