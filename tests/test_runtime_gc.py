"""GC configuration and accounting, in a fresh runtime per invocation."""

import os
import subprocess

import pytest

from tests import bootc, toolchain
from tests.allocator_probe import allocator_object


@pytest.fixture(scope="module")
def gc_probe(tmp_path_factory, allocator_object):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    directory = tmp_path_factory.mktemp("gc-probe")
    source = directory / "probe.c"
    source.write_text('''
#include "turkey_exports.h"
int main(int argc, char **argv) {
    turkey_giblets_initialize();
    if (argc > 1) turkey_gc_set_stress(0);
    if (argc > 2) turkey_array_new(0, 0, 8, 0);
    turkey_string_new((const unsigned char *)"a", 1);
    turkey_string_new((const unsigned char *)"aa", 2);
    turkey_collect();
    turkey_gc_report();
    return 0;
}
''')
    binary = directory / "probe"
    subprocess.run([*toolchain.cc(), "-std=c11",
                    "-I", str(bootc.PROBE_INCLUDE),
                    str(source), str(allocator_object),
                    "-lm", "-pthread", "-o", str(binary)], check=True,
                   capture_output=True, text=True)
    return binary


def probe(binary, scale="2", *args):
    env = dict(os.environ, TURKEY_GC_STATS="1", TURKEY_GC_THRESHOLD_SCALE=scale)
    env.pop("TURKEY_GC_STRESS", None)
    result = subprocess.run(toolchain.command(binary, *args), env=env,
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
    # A string is a byte array, so it is counted as one.
    assert f"array {count}," in out
    assert "next threshold 4096," in out


@pytest.fixture(scope="module")
def region_probe(tmp_path_factory, allocator_object):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    toolchain.needs_sanitizer()
    directory = tmp_path_factory.mktemp("region-probe")
    source = directory / "probe.c"
    source.write_text(r'''
#include "turkey_exports.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>

/* An object's header and slots, as `Turkey.Alloc` lays them out. A `String`
   is a byte array: its length is the count and its bytes are the slots. */
typedef struct TurkeyObject {
    int32_t kind;
    int32_t tag;
    int64_t count;
    uint64_t pointer_bitmap;
    uint64_t slots[];
} TurkeyObject;

/* The shadow-stack frame `turkey_root_enter` fills in: the previous frame,
   the function's name, the slot count, the slots, and which of the first 64
   are live. */
typedef struct RootFrame {
    struct RootFrame *previous;
    const char *function_name;
    int64_t count;
    void **values;
    int64_t live;
} RootFrame;
int main(void) {
    turkey_giblets_initialize();
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
                TurkeyObject *s = held[j];
                assert(turkey_heap_contains(s));
                assert(!turkey_heap_contains((unsigned char *)s + 1));
                if (s->count)
                    assert(((unsigned char *)s->slots)[s->count - 1] == 'q');
            }
        }
        turkey_heap_set_mark_epoch(UINT32_MAX);
        turkey_collect();
        assert(turkey_heap_mark_epoch() == 1);
        assert(turkey_heap_objects() == 96);
        assert(turkey_heap_contains(held[0]));
        void *dead = held[0];
        memset(held, 0, sizeof(held));
        turkey_collect();
        assert(!turkey_heap_contains(dead));
        assert(turkey_heap_objects() == 0);
        assert(turkey_heap_region_bytes() == 0);
    }
    /* Keep a single object while repeatedly filling and reusing its size
       class. A leak of slots in partly live regions grows without bound. */
    held[0] = turkey_string_new(bytes, 31);
    for (int cycle = 0; cycle < 30; cycle++) {
        for (int i = 0; i < 2000; i++) turkey_string_new(bytes, 31);
        turkey_collect();
        assert(turkey_heap_objects() == 1);
        assert(turkey_heap_region_bytes() == 65536);
        assert(((unsigned char *)((TurkeyObject *)held[0])->slots)[30] == 'q');
    }
    turkey_root_leave(&frame);
    turkey_collect();
    assert(turkey_heap_region_bytes() == 0);
    free(bytes);
    return turkey_has_panicked != 0;
}
''')
    binary = directory / "probe"
    subprocess.run([*toolchain.cc(), "-std=c11",
                    "-O1", "-fsanitize=undefined",
                    "-I", str(bootc.PROBE_INCLUDE), str(source), str(allocator_object), "-lm", "-pthread",
                    "-o", str(binary)], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize("stress", [False, True])
def test_regions_reuse_holes_and_reclaim_small_and_large_objects(region_probe, stress):
    env = dict(os.environ, TURKEY_GC_VERIFY="1")
    env.pop("TURKEY_GC_STATS", None)
    env.pop("TURKEY_GC_STRESS", None)
    if stress:
        env["TURKEY_GC_STRESS"] = "1"
    result = subprocess.run(toolchain.command(region_probe), env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.fixture(scope="module")
def code_probe(tmp_path_factory, allocator_object):
    """Layout code 7 is traced and 6 is not, for both of the collector's readers.

    Reading `>= 6` as "traced" would pass every test that had no untraced
    pointer in it, since the two codes would be one code. This is what makes
    the distinction a property rather than a convention: an array's element tag and an object's three-bit slot metadata
    are read by different branches of `mark_children`, and both are pinned.
    """
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    toolchain.needs_sanitizer()
    directory = tmp_path_factory.mktemp("code-probe")
    source = directory / "probe.c"
    source.write_text(r'''
#include "turkey_exports.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>

/* An object's header and slots, as `Turkey.Alloc` lays them out. A `String`
   is a byte array: its length is the count and its bytes are the slots. */
typedef struct TurkeyObject {
    int32_t kind;
    int32_t tag;
    int64_t count;
    uint64_t pointer_bitmap;
    uint64_t slots[];
} TurkeyObject;

/* The shadow-stack frame `turkey_root_enter` fills in: the previous frame,
   the function's name, the slot count, the slots, and which of the first 64
   are live. */
typedef struct RootFrame {
    struct RootFrame *previous;
    const char *function_name;
    int64_t count;
    void **values;
    int64_t live;
} RootFrame;

/* Whether a string referenced only from `holder` survives a collection. */
static int survives(void *holder, void *s) {
    RootFrame frame;
    void *roots[1];
    roots[0] = holder;
    turkey_root_enter(&frame, roots, 1, "code probe");
    frame.live = 1;
    turkey_collect();
    int alive = turkey_heap_contains(s) != 0;
    turkey_root_leave(&frame);
    return alive;
}

static void *array_holding(int32_t code, void *s) {
    TurkeyObject *a = turkey_array_new(1, 0, 8, code);
    a->slots[0] = (uint64_t)(uintptr_t)s;
    return a;
}

static void *object_holding(int32_t code, void *s) {
    /* One slot, so the three-bit metadata is the code itself. */
    TurkeyObject *o = turkey_object_new(0, 0, 1, (uint64_t)code);
    o->slots[0] = (uint64_t)(uintptr_t)s;
    return o;
}

int main(void) {
    turkey_giblets_initialize();
    void *a7 = turkey_string_new((const unsigned char *)"a7", 2);
    assert(survives(array_holding(7, a7), a7));

    void *a6 = turkey_string_new((const unsigned char *)"a6", 2);
    assert(!survives(array_holding(6, a6), a6));

    void *o7 = turkey_string_new((const unsigned char *)"o7", 2);
    assert(survives(object_holding(7, o7), o7));

    void *o6 = turkey_string_new((const unsigned char *)"o6", 2);
    assert(!survives(object_holding(6, o6), o6));

    return turkey_has_panicked != 0;
}
''')
    binary = directory / "probe"
    subprocess.run([*toolchain.cc(), "-std=c11",
                    "-O1", "-fsanitize=undefined",
                    "-I", str(bootc.PROBE_INCLUDE), str(source), str(allocator_object), "-lm", "-pthread",
                    "-o", str(binary)], check=True, capture_output=True, text=True)
    return binary


def test_only_layout_code_seven_is_traced(code_probe):
    result = subprocess.run(toolchain.command(code_probe), capture_output=True,
                            text=True)
    assert result.returncode == 0, result.stdout + result.stderr
