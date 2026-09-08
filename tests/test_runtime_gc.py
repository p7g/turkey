"""GC configuration and accounting, in a fresh runtime per invocation."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def gc_probe(tmp_path_factory):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler unavailable")
    root = Path(__file__).resolve().parents[1]
    directory = tmp_path_factory.mktemp("gc-probe")
    source = directory / "probe.c"
    source.write_text('''
#include "turkey_runtime.h"
int main(int argc, char **argv) {
    if (argc > 1) turkey_gc_set_stress(0);
    if (argc > 2) turkey_array_new(0, 0, 8, 0);
    TurkeyString *s = turkey_string_new((const unsigned char *)"a", 1);
    turkey_string_concat(s, s);
    turkey_collect();
    turkey_gc_report();
    return 0;
}
''')
    binary = directory / "probe"
    subprocess.run([cc, "-std=c11", "-I", str(root / "runtime"),
                    str(source), str(root / "runtime/turkey_runtime.c"),
                    "-lm", "-pthread", "-o", str(binary)], check=True,
                   capture_output=True, text=True)
    return binary


def probe(binary, scale="2", *args):
    env = dict(os.environ, TURKEY_GC_STATS="1", TURKEY_GC_THRESHOLD_SCALE=scale)
    env.pop("TURKEY_GC_STRESS", None)
    result = subprocess.run([str(binary), *args], env=env,
                            capture_output=True, text=True, check=True)
    return result.stderr


@pytest.mark.parametrize("scale", ["", "garbage", "2junk", "nan", "inf",
                                    "1e999", "0", "-1"])
def test_invalid_scale_uses_default(gc_probe, scale):
    assert "next threshold 2048," in probe(gc_probe, scale)


@pytest.mark.parametrize("scale,threshold", [
    ("1", 1024), ("4", 4096), (" 4 ", 4096),
    ("1e308", 9223372036854775807),
])
def test_scale_is_applied_without_overflow(gc_probe, scale, threshold):
    assert f"next threshold {threshold}," in probe(gc_probe, scale)


@pytest.mark.parametrize("args", [(), ("override",), ("override", "array")])
def test_allocation_kinds_and_options_survive_stress_override(gc_probe, args):
    out = probe(gc_probe, "4", *args)
    count = 3 if len(args) == 2 else 2
    assert f"allocations {count}," in out
    assert "by kind: string 2," in out
    assert f"array {count - 2}," in out
    assert "next threshold 4096," in out
