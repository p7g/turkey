"""`src/Turkey/Sites.gob`: counting allocations by the instruction that made them.

`TURKEY_ALLOC_SITES=1 boot native` numbers every allocating instruction on
stderr and makes the program write one counter per site when `main` returns.
These pin that the numbering and the counts line up, and that a site runs as
often as the program says it does.
"""

import os
import struct
import subprocess

from tests import bootc, lang, toolchain

PROGRAM = """
type Pair = Pair(Int, Int)

fun keep(xs : Array Pair, n : Int) -> Unit {
    for var i = 0; i < n; i = i + 1 { Array.push(xs, Pair(i, n)) }
}

fun main() {
    let xs = []
    keep(xs, 37)
    print(len(xs))
}
"""


def _counted(tmp_path):
    entry = tmp_path / "main.gob"
    entry.write_text(PROGRAM, encoding="utf-8")
    env = dict(os.environ, TURKEY_ALLOC_SITES="1", TURKEY_LIB=str(lang.LIB))
    compiled = subprocess.run(
        toolchain.command(bootc.binary(), "native", str(entry)),
        cwd=tmp_path, env=env, capture_output=True, check=True)
    source = tmp_path / "main.s"
    source.write_bytes(compiled.stdout)
    binary = tmp_path / "main"
    subprocess.run([*toolchain.cc(), "-o", str(binary), str(source),
                    *toolchain.libraries()], check=True)
    run = subprocess.run([str(binary)], cwd=tmp_path, capture_output=True,
                         text=True, check=True)
    table = [line.split("\t") for line in compiled.stderr.decode().splitlines()
             if line.startswith("site\t")]
    data = (tmp_path / "turkey-alloc-sites.bin").read_bytes()
    count = struct.unpack_from("<q", data)[0]
    counters = struct.unpack_from(f"<{count}q", data, 8)
    return run.stdout, table, counters


def test_every_site_has_a_counter_and_the_counts_are_the_runs(tmp_path):
    out, table, counters = _counted(tmp_path)
    assert out == "37\n"
    assert len(table) == len(counters)
    assert [int(row[1]) for row in table] == list(range(len(table)))
    # One site per copy of the constructor call: `keep` is inlined into
    # `main` and kept too, and between them they run once per element.
    pairs = [counters[int(row[1])] for row in table if row[4].endswith("Pair")]
    assert sum(pairs) == 37, pairs
