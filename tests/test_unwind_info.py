"""`src/Turkey/UnwindInfo.gob`: `__unwind_info` across page boundaries.

`src/UnwindInfoCases.gob` builds tables from synthetic entries that never
fold, at one entry, a full page of 511, one past it, two full pages and one
past those, and prints each one's bytes. Each table has to hold every entry
in order, in regular pages of at most 511, with an index entry per page and a
sentinel at the end of the code.
"""

import functools
import struct
from pathlib import Path

import pytest

from tests import lang
from tests.test_executable import _unwind_entries

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = REPO_ROOT / "src" / "UnwindInfoCases.gob"


@functools.lru_cache(maxsize=1)
def _tables() -> dict[int, bytes]:
    tables = {}
    for line in lang.output(DRIVER).splitlines():
        count, values = line.split(": ")
        tables[int(count)] = bytes(int(v) for v in values.split(","))
    return tables


@pytest.mark.parametrize("n", [1, 511, 512, 1022, 1023])
def test_each_page_holds_at_most_511_entries_and_the_index_names_each(n):
    table = _tables()[n]
    entries, sentinel, kinds = _unwind_entries(table)
    assert entries == [(0x1000 + 4 * k, 0x04000000 | k % 2) for k in range(n)]
    assert sentinel == 0x1000 + 4 * n
    assert kinds == [2] * -(-n // 511)
    (_, _, _, _, _, index_at, index_n) = struct.unpack_from("<7I", table, 0)
    for page in range(index_n - 1):
        first, at, _ = struct.unpack_from("<3I", table, index_at + 12 * page)
        _, count = struct.unpack_from("<HH", table, at + 4)
        assert first == 0x1000 + 4 * 511 * page
        assert count == min(511, n - 511 * page)
