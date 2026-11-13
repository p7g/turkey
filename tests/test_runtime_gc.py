"""GC configuration and accounting, in a fresh runtime per invocation."""

import os
import subprocess

import pytest

from tests import bootc, toolchain
from tests.allocator_probe import allocator_object, stats_object, verify_object


@pytest.fixture(scope="module")
def gc_probe(tmp_path_factory, stats_object):
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
                    str(source), str(stats_object),
                    "-lm", "-pthread", "-o", str(binary)], check=True,
                   capture_output=True, text=True)
    return binary


def probe(binary, scale="2", *args):
    env = dict(os.environ, TURKEY_GC_THRESHOLD_SCALE=scale)
    env.pop("TURKEY_GC_STRESS", None)
    result = subprocess.run(toolchain.command(binary, *args), env=env,
                            capture_output=True, text=True, check=True)
    return result.stderr


@pytest.mark.parametrize("scale", ["", "garbage", "2junk", "nan", "inf",
                                    "1e999", "0", "-1"])
def test_invalid_scale_uses_default(gc_probe, scale):
    assert "next threshold 131072," in probe(gc_probe, scale)


@pytest.mark.parametrize("scale,threshold", [
    ("1", 65536), ("4", 262144), (" 4 ", 262144),
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
    assert "next threshold 262144," in out


@pytest.fixture(scope="module")
def region_probe(tmp_path_factory, verify_object):
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

/* An array as `Turkey.Alloc` lays it out, which a `String` is: its header,
   its length and its elements. */
typedef struct TurkeyArray {
    uint64_t header;
    int64_t length;
    unsigned char elements[];
} TurkeyArray;

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
    /* 32752 bytes and a header and a length are the largest object a size
       class holds, and 32753 the smallest given a region of its own. */
    size_t sizes[] = {0, 1, 7, 24, 31, 232, 1024, 32752, 32753, 200000};
    for (int cycle = 0; cycle < 4; cycle++) {
        for (int i = 0; i < 1000; i++) {
            int slot = i % 96;
            held[slot] = turkey_string_new(bytes, sizes[i % 10]);
            assert(!turkey_has_panicked);
            if (i % 137 == 0) turkey_collect();
            for (int j = 0; j < 96; j++) if (held[j]) {
                TurkeyArray *s = held[j];
                assert(turkey_heap_contains(s));
                assert(!turkey_heap_contains((unsigned char *)s + 1));
                if (s->length) assert(s->elements[s->length - 1] == 'q');
            }
        }
        turkey_collect();
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
        assert(((TurkeyArray *)held[0])->elements[30] == 'q');
    }
    turkey_root_leave(&frame);
    /* Empty regions are kept up to the heap's peak since the last full
       collection or between the two before it, so the third full collection
       that allocates nothing gives every one back. A stressed run collects
       at every allocation, so its peaks are smaller. */
    for (int i = 0; i < 2000; i++) turkey_string_new(bytes, 31);
    turkey_collect();
    assert(turkey_heap_region_bytes() == 0);
    if (!getenv("TURKEY_GC_STRESS")) assert(turkey_heap_spare_bytes() >= 65536);
    turkey_collect();
    if (!getenv("TURKEY_GC_STRESS")) assert(turkey_heap_spare_bytes() >= 65536);
    turkey_collect();
    assert(turkey_heap_spare_bytes() == 0);
    free(bytes);
    return turkey_has_panicked != 0;
}
''')
    binary = directory / "probe"
    subprocess.run([*toolchain.cc(), "-std=c11",
                    "-O1", "-fsanitize=undefined",
                    "-I", str(bootc.PROBE_INCLUDE), str(source), str(verify_object), "-lm", "-pthread",
                    "-o", str(binary)], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize("stress", [False, True])
def test_regions_reuse_holes_and_reclaim_small_and_large_objects(region_probe, stress):
    env = dict(os.environ)
    env.pop("TURKEY_GC_STRESS", None)
    if stress:
        env["TURKEY_GC_STRESS"] = "1"
    result = subprocess.run(toolchain.command(region_probe), env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.fixture(scope="module")
def code_probe(tmp_path_factory, allocator_object):
    """A word the header counts as traced is followed and one it does not is
    not, for both of the collector's readers.

    A collector that followed every pointer-sized word would pass every test
    that had no untraced pointer in it. An array's count of traced words per
    element and an object's count of traced fields are read by different
    branches of `drain`, and both are pinned, each with a word that points at
    a live string and is not counted.
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

/* An object as `Turkey.Alloc` lays it out: its header and its fields; and an
   array: its header, its length and its elements. */
typedef struct TurkeyObject {
    uint64_t header;
    uint64_t slots[];
} TurkeyObject;
typedef struct TurkeyArray {
    uint64_t header;
    int64_t length;
    uint64_t elements[];
} TurkeyArray;

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

/* One element of one word, traced if the header counts it. */
static void *array_holding(int64_t traced, void *s) {
    TurkeyArray *a = turkey_array_new(1, 0, 8, traced);
    assert(a != NULL);
    a->elements[0] = (uint64_t)(uintptr_t)s;
    return a;
}

/* One word of fields, traced if the header counts it. */
static void *object_holding(int64_t traced, void *s) {
    TurkeyObject *o = turkey_object_new(0, 0, 8, traced);
    o->slots[0] = (uint64_t)(uintptr_t)s;
    return o;
}

int main(void) {
    turkey_giblets_initialize();
    void *a1 = turkey_string_new((const unsigned char *)"a1", 2);
    assert(survives(array_holding(1, a1), a1));

    void *a0 = turkey_string_new((const unsigned char *)"a0", 2);
    assert(!survives(array_holding(0, a0), a0));

    void *o1 = turkey_string_new((const unsigned char *)"o1", 2);
    assert(survives(object_holding(1, o1), o1));

    void *o0 = turkey_string_new((const unsigned char *)"o0", 2);
    assert(!survives(object_holding(0, o0), o0));

    return turkey_has_panicked != 0;
}
''')
    binary = directory / "probe"
    subprocess.run([*toolchain.cc(), "-std=c11",
                    "-O1", "-fsanitize=undefined",
                    "-I", str(bootc.PROBE_INCLUDE), str(source), str(allocator_object), "-lm", "-pthread",
                    "-o", str(binary)], check=True, capture_output=True, text=True)
    return binary


def test_only_the_traced_words_a_header_counts_are_followed(code_probe):
    result = subprocess.run(toolchain.command(code_probe), capture_output=True,
                            text=True)
    assert result.returncode == 0, result.stdout + result.stderr
