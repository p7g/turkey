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


@pytest.fixture(scope="module")
def region_probe(tmp_path_factory):
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("C compiler unavailable")
    root = Path(__file__).resolve().parents[1]
    directory = tmp_path_factory.mktemp("region-probe")
    source = directory / "probe.c"
    source.write_text(r'''
#include <assert.h>
#include "turkey_runtime.c"
int main(void) {
    RootFrame frame;
    void *held[96] = {0};
    turkey_root_enter(&frame, held, 96, "region probe");
    frame.live = -1;
    unsigned char *bytes = malloc(200000);
    memset(bytes, 'q', 200000);
    size_t sizes[] = {0, 1, 7, 24, 31, 232, 1024, 32720, 65536, 200000};
    for (int cycle = 0; cycle < 4; cycle++) {
        for (int i = 0; i < 1000; i++) {
            int slot = i % 96;
            held[slot] = turkey_string_new(bytes, sizes[i % 10]);
            assert(!turkey_has_panicked);
            if (i % 137 == 0) turkey_collect();
            for (int j = 0; j < 96; j++) if (held[j]) {
                TurkeyString *s = held[j];
                assert(find_header(s));
                assert(!find_header((unsigned char *)s + 1));
                if (s->length) assert(s->bytes[s->length - 1] == 'q');
            }
        }
        mark_epoch = UINT32_MAX;
        turkey_collect();
        assert(mark_epoch == 1);
        assert(heap_count == 96);
        assert(find_header(held[0]));
        void *dead = held[0];
        memset(held, 0, sizeof(held));
        turkey_collect();
        assert(!find_header(dead));
        assert(heap_count == 0);
        assert(region_bytes == 0);
    }
    /* Keep a single object while repeatedly filling and reusing its size
       class. A leak of slots in partly live regions grows without bound. */
    held[0] = turkey_string_new(bytes, 31);
    for (int cycle = 0; cycle < 30; cycle++) {
        for (int i = 0; i < 2000; i++) turkey_string_new(bytes, 31);
        turkey_collect();
        assert(heap_count == 1);
        assert(region_bytes == REGION_BYTES);
        assert(((TurkeyString *)held[0])->bytes[30] == 'q');
    }
    turkey_root_leave(&frame);
    turkey_collect();
    assert(region_bytes == 0);
    free(bytes);
    return turkey_has_panicked != 0;
}
''')
    binary = directory / "probe"
    subprocess.run([cc, "-std=c11", "-O1", "-fsanitize=undefined",
                    "-I", str(root / "runtime"), str(source), "-lm", "-pthread",
                    "-o", str(binary)], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize("stress", [False, True])
def test_regions_reuse_holes_and_reclaim_small_and_large_objects(region_probe, stress):
    env = dict(os.environ)
    env.pop("TURKEY_GC_STATS", None)
    env.pop("TURKEY_GC_STRESS", None)
    if stress:
        env["TURKEY_GC_STRESS"] = "1"
    result = subprocess.run([str(region_probe)], env=env, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
