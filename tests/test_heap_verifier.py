"""Fault injection checks the verifier independently of successful collection."""

import os
import shutil
import subprocess

import pytest

from tests import bootc, toolchain
from tests.allocator_probe import allocator_object


@pytest.fixture(scope="module")
def verifier_probe(tmp_path_factory, allocator_object):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    toolchain.needs_sanitizer()
    directory = tmp_path_factory.mktemp("heap-verifier")
    source = directory / "probe.c"
    source.write_text(r'''
#include "turkey_exports.h"
#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The heap's format, as the probe corrupts it: a copy of its own, so that the
   fault injected is the one named here whatever the collector or the verifier
   believes. */
typedef struct Header { uint64_t generation, size; uint32_t kind, marked; } Header;
typedef struct Region {
    struct Region *next, *available_next;
    uint64_t slot_size, reserved;
    uint32_t capacity, used, live, search_word, size_class;
    uint64_t allocated[32], marked_slots[32];
    unsigned char data[];
} Region;
typedef struct Frame {
    struct Frame *previous;
    const char *function_name;
    int64_t count;
    void **values;
    int64_t live;
} Frame;
typedef struct Object {
    int32_t kind, tag;
    int64_t count;
    uint64_t pointer_bitmap;
    uint64_t slots[];
} Object;
typedef struct Entry { uintptr_t retaddr; int64_t count; const int64_t *offsets; } Entry;
_Static_assert(sizeof(Header) == 24, "header");
_Static_assert(offsetof(Region, marked_slots) == 312, "marked slots");
_Static_assert(offsetof(Region, data) == 568, "region data");

static Region *regions(void) { return turkey_heap_regions(); }
static Region **available(void) { return turkey_heap_available(); }
static Region **region_table(void) { return turkey_heap_region_table(); }

static Header *header_of(void *value) { return (Header *)value - 1; }
static Region *region_of(Header *h) { return (Region *)((uintptr_t)h & ~(uintptr_t)65535); }

static int64_t expected_count;
static void check_stopped(void) {
    assert(turkey_heap_objects() == expected_count);
    puts("allocation stopped");
}

static void forget(void *value) {
    Header *h = header_of(value);
    Region *r = region_of(h);
    size_t i = ((unsigned char *)h - r->data) / r->slot_size;
    h->marked = 0;
    h->generation = 0;
    r->marked_slots[i / 64] &= ~(UINT64_C(1) << (i % 64));
    r->live--;
}

/* What the collector's mark does to one object, done here instead so that
   the verifier is not checked against the collector's own tracing. */
static void remember(void *value) {
    Header *h = header_of(value);
    Region *r = region_of(h);
    size_t i = ((unsigned char *)h - r->data) / r->slot_size;
    h->marked = (uint32_t)turkey_heap_mark_epoch();
    h->generation = 1;
    r->marked_slots[i / 64] |= UINT64_C(1) << (i % 64);
    r->live++;
}

/* The region set's hash, to put an entry out of its place. */
static size_t region_hash(const Region *region) {
    uint64_t h = ((uintptr_t)region / 65536) * UINT64_C(0x9E3779B97F4A7C15);
    return (size_t)(h ^ (h >> 32)) & ((size_t)turkey_heap_region_table_capacity() - 1);
}

int main(int argc, char **argv) {
    assert(argc == 2);
    const char *which = argv[1];
    turkey_giblets_initialize();
    turkey_gc_set_stress(0);
    turkey_gc_set_verify(0);
    Frame frame;
    void *held[66] = {0};
    turkey_root_enter(&frame, held, 66, "verifier probe");
    frame.live = 1;
    Object *child = turkey_string_new((const unsigned char *)"ok", 2);
    Object *parent = turkey_object_new(1, 0, 2, 7 | (6 << 3));
    parent->slots[0] = (uintptr_t)child;
    parent->slots[1] = 1; /* A raw pointer must never be followed. */
    held[0] = parent;
    held[1] = (void *)1; /* Dead shadow slots must never be followed. */
    held[65] = child; /* Slots beyond the mask are always roots. */
    assert(turkey_heap_check(0, NULL, turkey_heap_state()));
    int phase = 0;
    if (!strcmp(which, "valid")) {
        /* Cycles, every field code, pointer arrays, closures/environments,
           cells and all array widths are independently decoded. */
        Object *cycle = turkey_object_new(0, 0, 8,
            0 | (1 << 3) | (2 << 6) | (3 << 9) | (4 << 12) | (5 << 15) | (6 << 18) | (7 << 21));
        for (int i = 0; i < 7; i++) cycle->slots[i] = 1;
        cycle->slots[7] = (uintptr_t)cycle;
        held[2] = cycle;
        held[3] = turkey_array_new(3, (uintptr_t)child, 8, 7);
        held[4] = turkey_cell_new((uintptr_t)child, 1);
        held[5] = turkey_closure_shell(123);
        Object *env = turkey_object_new(4, 0, 2, 1);
        env->slots[0] = (uintptr_t)child;
        env->slots[1] = 1;
        ((Object *)held[5])->slots[1] = (uintptr_t)env;
        held[6] = turkey_cell_new(123, 0);
        held[7] = turkey_array_new(8, 1, 1, 2);
        held[8] = turkey_array_new(8, 1, 4, 3);
        held[9] = turkey_cell_new(1, 0);
        held[10] = turkey_array_new(0, 0, 8, 7);
        /* Words below 4096 in a traced slot or a root are immediates -- a
           compact sum's nullary constructors -- and are never followed. */
        ((Object *)held[3])->slots[1] = 4095;
        held[11] = (void *)1;
        held[1] = NULL;
        frame.live = -1;
        /* Exactly 12 objects reachable (the closure's environment included). */
        for (int i = 0; i < 100; i++) turkey_cell_new(i, 0);
        turkey_gc_set_verify(1);
        turkey_heap_set_mark_epoch(UINT32_MAX);
        turkey_collect();
        assert(!turkey_has_panicked && turkey_heap_objects() == 12);
        assert(turkey_heap_mark_epoch() == 1);
        for (int i = 0; i < 20; i++) { turkey_cell_new(i, 0); turkey_collect(); }
        assert(turkey_heap_objects() == 12);
        memset(held, 0, sizeof held);
        turkey_collect();
        assert(!turkey_has_panicked && turkey_heap_objects() == 0);
        assert(turkey_heap_region_bytes() == 0);
        puts("valid");
        return 0;
    }
    if (!strncmp(which, "mark-", 5) || !strcmp(which, "sweep-survivor")) {
        turkey_heap_set_mark_epoch(turkey_heap_mark_epoch() + 1);
        remember(parent);
        remember(child);
        phase = 1;
    }
    Region *r = region_of(header_of(parent));
    if (!strcmp(which, "mark-child")) { forget(child); held[65] = NULL; }
    else if (!strcmp(which, "mark-root")) forget(parent);
    else if (!strcmp(which, "mark-high-root")) {
        parent->slots[0] = 0; forget(child);
    }
    else if (!strcmp(which, "mark-header")) header_of(parent)->marked = 0;
    else if (!strcmp(which, "mark-free")) r->marked_slots[0] |= UINT64_C(1) << 2;
    else if (!strcmp(which, "mark-count")) r->live++;
    else if (!strcmp(which, "size")) header_of(parent)->size = UINT64_MAX;
    else if (!strcmp(which, "short-header")) header_of(parent)->size = 8;
    else if (!strcmp(which, "heap-kind")) header_of(parent)->kind = 99;
    else if (!strcmp(which, "kind")) parent->kind = 99;
    else if (!strcmp(which, "negative-kind")) parent->kind = -1;
    else if (!strcmp(which, "negative-count")) parent->count = -1;
    else if (!strcmp(which, "huge-count")) parent->count = INT64_MAX;
    else if (!strcmp(which, "payload")) parent->count = 1;
    else if (!strcmp(which, "array-width")) child->pointer_bitmap = 3;
    else if (!strcmp(which, "array-pointer-width")) child->tag = 7;
    else if (!strcmp(which, "array-tag")) child->tag = 99;
    else if (!strcmp(which, "negative-array-tag")) child->tag = -1;
    else if (!strcmp(which, "cell-size")) { held[2] = turkey_cell_new(0, 0); header_of(held[2])->size = 1; }
    else if (!strcmp(which, "interior")) parent->slots[0]++;
    /* The first word that is not an immediate, and in no region. */
    else if (!strcmp(which, "foreign")) parent->slots[0] = 4096;
    else if (!strcmp(which, "high-address")) parent->slots[0] = UINT64_C(0xfffffffffffffff8);
    else if (!strcmp(which, "past-region")) parent->slots[0] = (uintptr_t)r + (1 << 20);
    else if (!strcmp(which, "root")) held[0] = (void *)4096;
    else if (!strcmp(which, "high-root")) held[65] = (void *)4096;
    else if (!strcmp(which, "root-cycle")) frame.previous = &frame;
    else if (!strcmp(which, "root-frame")) frame.count = -1;
    else if (!strcmp(which, "region-cycle")) regions()->next = regions();
    else if (!strcmp(which, "region-size")) r->slot_size = 0;
    else if (!strcmp(which, "region-huge-slot")) r->slot_size = UINT64_MAX;
    else if (!strcmp(which, "region-reserved")) r->reserved = UINT64_MAX & ~(uint64_t)65535;
    else if (!strcmp(which, "region-capacity")) r->capacity = UINT32_MAX;
    else if (!strcmp(which, "region-count")) r->used++;
    else if (!strcmp(which, "region-class")) r->size_class = 0;
    else if (!strcmp(which, "region-search")) r->search_word = 1;
    else if (!strcmp(which, "heap-count")) turkey_heap_set_objects(turkey_heap_objects() + 1);
    else if (!strcmp(which, "heap-bytes"))
        turkey_heap_set_region_bytes(turkey_heap_region_bytes() + 1);
    else if (!strcmp(which, "bitmap")) r->allocated[31] |= UINT64_C(1) << 63;
    else if (!strcmp(which, "available")) available()[r->size_class] = NULL;
    else if (!strcmp(which, "available-cycle")) r->available_next = r;
    else if (!strcmp(which, "available-foreign")) available()[0] = (void *)1;
    else if (!strcmp(which, "region-table-missing")) {
        Region **table = region_table();
        for (int64_t i = 0; i < turkey_heap_region_table_capacity(); i++)
            if (table[i] == r) table[i] = NULL;
    }
    else if (!strcmp(which, "region-table-probe")) {
        /* Moved one slot past its home with the home left empty. */
        Region **table = region_table();
        size_t mask = (size_t)turkey_heap_region_table_capacity() - 1;
        size_t home = region_hash(r);
        table[home] = NULL;
        table[(home + 1) & mask] = r;
    }
    else if (!strcmp(which, "sweep-survivor")) {
        forget(parent);
        phase = 2;
    }
    else if (!strcmp(which, "sticky-marks")) {
        turkey_collect();
        header_of(parent)->generation = 0;
    }
    else if (!strcmp(which, "generation")) header_of(parent)->generation = 7;
    else if (!strcmp(which, "unremembered") || !strcmp(which, "remembered")) {
        /* An old object given a young one: remembered only by the barrier. */
        turkey_gc_set_generational(1);
        turkey_collect();
        parent->slots[0] = (uintptr_t)turkey_string_new((const unsigned char *)"young", 5);
        if (!strcmp(which, "remembered")) {
            turkey_write_barrier(parent);
            assert(turkey_heap_check(0, NULL, turkey_heap_state()));
            puts("valid");
            return 0;
        }
    }
    else if (!strcmp(which, "remembered-count")) {
        turkey_gc_set_generational(1);
        turkey_collect();
        turkey_write_barrier(parent);
        header_of(parent)->generation = 1;
    }
    else if (!strcmp(which, "generational")) {
        /* A young object survives a minor collection through a remembered
           parent, and an unreachable young one does not; an old object that
           dies is kept by minor collections until a full one. */
        turkey_gc_set_generational(1);
        turkey_gc_set_verify(1);
        turkey_collect();
        Object *young = turkey_string_new((const unsigned char *)"young", 5);
        parent->slots[0] = (uintptr_t)young;
        turkey_write_barrier(parent);
        void *dead = turkey_string_new((const unsigned char *)"dead", 4);
        turkey_collect_minor();
        assert(!turkey_has_panicked);
        assert(turkey_heap_contains(young) && !turkey_heap_contains(dead));
        assert(header_of(young)->generation == 1 && header_of(parent)->generation == 1);
        parent->slots[0] = 0;
        turkey_collect_minor();
        assert(turkey_heap_contains(young));
        turkey_collect();
        assert(!turkey_has_panicked && !turkey_heap_contains(young));
        puts("valid");
        return 0;
    }
    else if (!strcmp(which, "reclaimed")) {
        void *dead = turkey_cell_new(5, 0);
        turkey_collect();
        parent->slots[0] = (uintptr_t)dead;
    }
    else if (!strcmp(which, "allocation-stops")) {
        parent->count = -1;
        turkey_gc_set_verify(1);
        turkey_gc_set_stress(1);
        expected_count = turkey_heap_objects();
        atexit(check_stopped);
        turkey_cell_new(3, 0);
        abort();
    }
    else if (!strncmp(which, "native-", 7)) {
        _Alignas(16) uintptr_t stack[16] = {0};
        stack[0] = (uintptr_t)&stack[8]; stack[1] = 1234;
        stack[6] = (uintptr_t)child;
        int64_t offset = -16;
        Entry entry = {1234, 1, &offset};
        /* The collector's state with this frame table and stack in it: the
           frame table, its length, the mark epoch and the stack's top. */
        int64_t state[16];
        memcpy(state, turkey_heap_state(), sizeof state);
        state[8] = (int64_t)(uintptr_t)&entry;
        state[9] = 1;
        state[10] = (int64_t)(uintptr_t)&stack[15];
        int64_t native_phase = 0;
        if (!strcmp(which, "native-invalid")) stack[6] = 4096;
        if (!strcmp(which, "native-offset")) offset = INT64_MIN;
        if (!strcmp(which, "native-table")) state[8] = 0;
        if (!strcmp(which, "native-unmarked")) { native_phase = 1; state[7] = 1; }
        int64_t ok = turkey_heap_check_frames(native_phase, (uintptr_t)stack, state);
        if (!strcmp(which, "native-valid")) { assert(ok); puts("valid"); return 0; }
        assert(!ok); puts(turkey_heap_check_message()); return 0;
    }
    int64_t ok = turkey_heap_check(phase, NULL, turkey_heap_state());
    assert(!ok);
    puts(turkey_heap_check_message());
    return 0;
}
''')
    binary = directory / "probe"
    subprocess.run([*toolchain.cc(), "-std=c11", "-O1", "-fsanitize=undefined",
                    "-I", str(bootc.PROBE_INCLUDE), str(source), str(allocator_object),
                    "-lm", "-pthread", "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    return binary


CASES = {
    "mark-child": "reachable object is unmarked",
    "mark-root": "reachable object is unmarked",
    "mark-high-root": "reachable object is unmarked",
    "mark-header": "header and region marks disagree",
    "mark-free": "marked/free slot",
    "mark-count": "region counts disagree",
    "size": "object exceeds allocation slot",
    "short-header": "invalid heap header",
    "heap-kind": "invalid heap header",
    "kind": "invalid object header",
    "negative-kind": "invalid object header",
    "negative-count": "invalid object header",
    "huge-count": "invalid object count",
    "payload": "payload size disagrees",
    "array-width": "invalid array layout",
    "array-pointer-width": "invalid array layout",
    "array-tag": "invalid array layout",
    "negative-array-tag": "invalid array layout",
    "cell-size": "invalid cell size",
    "interior": "invalid traced pointer",
    "foreign": "invalid traced pointer",
    "high-address": "invalid traced pointer",
    "past-region": "invalid traced pointer",
    "root": "invalid traced pointer",
    "high-root": "invalid traced pointer",
    "root-cycle": "cyclic root chain",
    "root-frame": "invalid root frame",
    "region-cycle": "cyclic region chain",
    "region-size": "invalid region geometry",
    "region-huge-slot": "invalid region geometry",
    "region-reserved": "invalid region geometry",
    "region-capacity": "invalid region geometry",
    "region-count": "region counts disagree",
    "region-class": "invalid region size class",
    "region-search": "allocation search skips free slots",
    "heap-count": "heap totals disagree",
    "heap-bytes": "heap totals disagree",
    "bitmap": "bitmap outside region",
    "available": "missing available region",
    "available-cycle": "invalid available-region list",
    "available-foreign": "invalid available-region list",
    "region-table-missing": "region table disagrees with region list",
    "region-table-probe": "region table entry unreachable by probing",
    "sweep-survivor": "unmarked sweep survivor",
    "sticky-marks": "region marks and generation disagree",
    "generation": "invalid generation word",
    "unremembered": "old object holds a young one and is not remembered",
    "remembered": "valid",
    "remembered-count": "remembered objects miscounted",
    "generational": "valid",
    "reclaimed": "invalid traced pointer",
    "allocation-stops": "invalid object header",
    "native-invalid": "invalid traced pointer",
    "native-offset": "invalid native root offset",
    "native-table": "invalid native frame table",
    "native-unmarked": "reachable object is unmarked",
    "valid": "valid",
    "native-valid": "valid",
}


@pytest.mark.parametrize("case,diagnostic", CASES.items())
def test_verifier_detects_corruption(verifier_probe, case, diagnostic):
    result = subprocess.run(toolchain.command(verifier_probe, case), capture_output=True,
                            text=True, timeout=20, env=dict(os.environ, UBSAN_OPTIONS="halt_on_error=1"))
    if case == "allocation-stops":
        assert result.returncode == 1, result.stderr
        assert "allocation stopped" in result.stdout
        assert "heap verifier: " + diagnostic in result.stderr
        return
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert diagnostic in result.stdout
    if diagnostic != "valid":
        assert result.stdout.startswith("heap verifier:")


@pytest.mark.parametrize("defect", ["native-roots", "children"])
def test_broken_collector_is_stopped_before_sweeping(tmp_path, defect):
    source = tmp_path / "main.gob"
    source.write_text('''
fun main() {
    let x = [42]
    let y = [x]
    let z = [y]
    print(z[0][0][0])
}
''')
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    # The defect goes into a copy of the library, which the compiler reads
    # through TURKEY_LIB, so that the collector itself is what is broken.
    library = tmp_path / "lib"
    shutil.copytree(bootc.REPO_ROOT / "lib", library)
    heap = library / "Turkey" / "Heap.gob"
    text = heap.read_text()
    if defect == "native-roots":
        before, after = ("    scanNativeFrames(frame)\n    if Process.pending()",
                         "    if Process.pending()")
    else:
        before = "        let object = Prim.loadPtr(markStack, times8(top))\n"
        after = before + "        if !Prim.ptrIsNull(object) { continue }\n"
    assert text.count(before) == 1, before
    heap.write_text(text.replace(before, after))
    result = subprocess.run(toolchain.command(bootc.binary(), "native", str(source)),
                            cwd=bootc.REPO_ROOT, capture_output=True, text=True,
                            check=True, env=dict(os.environ, TURKEY_LIB=str(library)))
    generated = tmp_path / "main.s"
    generated.write_text(result.stdout)
    binary = tmp_path / "broken"
    subprocess.run([*toolchain.cc(), str(generated), *toolchain.libraries(),
                    "-o", str(binary)],
                   capture_output=True, text=True, check=True)
    result = subprocess.run(toolchain.command(binary), capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, TURKEY_GC_STRESS="1", TURKEY_GC_VERIFY="1"))
    assert result.returncode == 1, result.stderr
    assert "heap verifier: reachable object is unmarked" in result.stderr
    assert "42" not in result.stdout
