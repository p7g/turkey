"""`llvm-dwarfdump`, and the line tables it reads out of a file.

Shared by the tests that read DWARF: `tests/test_debug_info.py`, and
`tests/test_executable.py`, which compares an executable's line table with
the system assembler's row by row where the two encode it differently.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import toolchain


@functools.lru_cache(maxsize=None)
def find() -> str | None:
    """Returns `llvm-dwarfdump`, or macOS's `dwarfdump`, which is the same
    program; None if neither is installed."""
    for candidate in ("llvm-dwarfdump", "/opt/homebrew/opt/llvm/bin/llvm-dwarfdump",
                      "/usr/local/opt/llvm/bin/llvm-dwarfdump"):
        if shutil.which(candidate):
            return candidate
    if toolchain.target() == "arm64-darwin" and shutil.which("dwarfdump"):
        return "dwarfdump"
    # Debian and Ubuntu install each LLVM release under its own prefix.
    for found in sorted(Path("/usr/lib").glob("llvm-*/bin/llvm-dwarfdump"), reverse=True):
        return str(found)
    return None


def needs() -> str:
    """Returns `find()`'s tool, skipping the calling test if there is none."""
    tool = find()
    if tool is None:
        pytest.skip("no llvm-dwarfdump on $PATH to read the DWARF with")
    return tool


def line_rows(tool: str, obj: Path) -> list[tuple]:
    """Returns every row of `obj`'s line table, in order: the address, line,
    column, file by its full path, and the flags that are set."""
    text = subprocess.run([tool, "--debug-line", str(obj)], capture_output=True,
                          text=True, check=True).stdout
    directories = [m.group(1) for m in
                   re.finditer(r'include_directories\[\s*\d+\] = "([^"]*)"', text)]
    files: dict[int, str] = {}
    for m in re.finditer(r'file_names\[\s*(\d+)\]:\s*\n\s*name:\s*"([^"]*)"\s*\n'
                         r'\s*dir_index:\s*(\d+)', text):
        name, directory = m.group(2), directories[int(m.group(3))]
        files[int(m.group(1))] = os.path.normpath(
            os.path.join(directories[0], directory, name))
    rows = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 4 and re.fullmatch(r"0x[0-9a-f]{16}", fields[0]):
            rows.append((int(fields[0], 16), int(fields[1]), int(fields[2]),
                         files.get(int(fields[3]), "?"), tuple(sorted(fields[7:]))))
    return rows


def kept_by_gnu_as(rows: list[tuple]) -> list[tuple]:
    """Returns the rows of a line table that GNU `as` writes too.

    GNU `as` drops a `.loc` at line 0, which marks code with no source
    position, so its previous row runs on over that code. An end of sequence
    carries the line of the row before it, which is then a dropped row's, so
    only its address is compared."""
    kept = []
    for row in rows:
        address, line, _, _, flags = row
        if "end_sequence" in flags:
            kept.append((address, "end_sequence"))
        elif line != 0:
            kept.append(row)
    return kept
