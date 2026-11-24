"""A long line costs the front end time in proportion to its length.

The parser reads each literal's text from its line. Decoding the whole line
again for every literal made a one-line array of 20,000 numbers take a
hundred times as long as the same array over many lines.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from tests import bootc, toolchain


def test_a_one_line_array_of_many_literals_checks_quickly(tmp_path: Path) -> None:
    entry = tmp_path / "main.gob"
    numbers = ", ".join(str(i) for i in range(20000))
    entry.write_text(f"let xs = [{numbers}]\nfun main() {{ print(len(xs)) }}\n")
    # Built, or found in the cache, before the clock starts.
    command = toolchain.command(bootc.binary(), "check", str(entry))
    start = time.monotonic()
    result = subprocess.run(command,
                            cwd=bootc.REPO_ROOT, capture_output=True, text=True, timeout=120)
    elapsed = time.monotonic() - start
    assert result.returncode == 0, result.stderr
    # About half a second when the cost is linear, and twenty when it is not.
    assert elapsed < 8, elapsed
