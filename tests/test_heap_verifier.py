"""Fault injection checks the verifier independently of successful collection."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.allocator_probe import allocator_object


@pytest.fixture(scope="module")
def verifier_probe(tmp_path_factory, allocator_object):
    root = Path(__file__).resolve().parents[1]
    directory = tmp_path_factory.mktemp("heap-verifier")
    source = directory / "probe.c"
    source.write_text(r'''
#include <assert.h>
#include "turkey_runtime.c"

static int64_t expected_count;
static void check_stopped(void) {
    assert(heap_count == expected_count);
    assert(turkey_has_panicked);
    puts("allocation stopped");
}

static void forget(void *value) {
    HeapHeader *h = header_of(value);
    HeapRegion *r = region_of(h);
    size_t i = ((unsigned char *)h - r->data) / r->slot_size;
    h->marked = 0;
    r->marked_slots[i / 64] &= ~(UINT64_C(1) << (i % 64));
    r->live--;
}

int main(int argc, char **argv) {
    assert(argc == 2);
    const char *which = argv[1];
    turkey_gc_set_stress(0);
    gc_verify = 0;
    RootFrame frame;
    void *held[66] = {0};
    turkey_root_enter(&frame, held, 66, "verifier probe");
    frame.live = 1;
    TurkeyObject *child = turkey_string_new((const unsigned char *)"ok", 2);
    TurkeyObject *parent = turkey_object_new(1, 0, 2, 7 | (6 << 3));
    parent->slots[0] = (uintptr_t)child;
    parent->slots[1] = 1; /* A raw pointer must never be followed. */
    held[0] = parent;
    held[1] = (void *)1; /* Dead shadow slots must never be followed. */
    held[65] = child; /* Slots beyond the mask are always roots. */
    assert(heap_verify(0));
    int phase = 0;
    if (!strcmp(which, "valid")) {
        /* Cycles, every field code, pointer arrays, closures/environments,
           cells, scalar boxes and all array widths are independently decoded. */
        TurkeyObject *cycle = turkey_object_new(0, 0, 8,
            0 | (1 << 3) | (2 << 6) | (3 << 9) | (4 << 12) | (5 << 15) | (6 << 18) | (7 << 21));
        for (int i = 0; i < 7; i++) cycle->slots[i] = 1;
        cycle->slots[7] = (uintptr_t)cycle;
        held[2] = cycle;
        held[3] = turkey_array_new(3, (uintptr_t)child, 8, 7);
        held[4] = turkey_cell_new((uintptr_t)child, 1);
        held[5] = turkey_closure_shell(123);
        TurkeyObject *env = turkey_object_new(4, 0, 2, 1);
        env->slots[0] = (uintptr_t)child;
        env->slots[1] = 1;
        ((TurkeyObject *)held[5])->slots[1] = (uintptr_t)env;
        held[6] = turkey_box(123, 4);
        held[7] = turkey_array_new(8, 1, 1, 2);
        held[8] = turkey_array_new(8, 1, 4, 3);
        held[9] = turkey_cell_new(1, 0);
        held[10] = turkey_array_new(0, 0, 8, 7);
        held[1] = NULL;
        frame.live = -1;
        /* Exactly 12 objects reachable (the closure's environment included). */
        for (int i = 0; i < 100; i++) turkey_box(i, 4);
        gc_verify = 1;
        mark_epoch = UINT32_MAX;
        turkey_collect();
        assert(!turkey_has_panicked && heap_count == 12 && mark_epoch == 1);
        for (int i = 0; i < 20; i++) { turkey_box(i, 4); turkey_collect(); }
        assert(heap_count == 12);
        memset(held, 0, sizeof held);
        turkey_collect();
        assert(!turkey_has_panicked && heap_count == 0 && region_bytes == 0);
        puts("valid");
        return 0;
    }
    if (!strncmp(which, "mark-", 5) || !strcmp(which, "sweep-survivor")) {
        mark_epoch++;
        mark(parent);
        phase = 1;
    }
    HeapRegion *r = region_of(header_of(parent));
    if (!strcmp(which, "mark-child")) { forget(child); held[65] = NULL; }
    else if (!strcmp(which, "mark-root")) forget(parent);
    else if (!strcmp(which, "mark-high-root")) {
        parent->slots[0] = 0; forget(child);
    }
    else if (!strcmp(which, "mark-header")) header_of(parent)->marked = 0;
    else if (!strcmp(which, "mark-free")) r->marked_slots[0] |= UINT64_C(1) << 2;
    else if (!strcmp(which, "mark-count")) r->live++;
    else if (!strcmp(which, "size")) header_of(parent)->size = SIZE_MAX;
    else if (!strcmp(which, "short-header")) header_of(parent)->size = 8;
    else if (!strcmp(which, "heap-kind")) header_of(parent)->kind = 99;
    else if (!strcmp(which, "kind")) parent->kind = 99;
    else if (!strcmp(which, "negative-count")) parent->count = -1;
    else if (!strcmp(which, "huge-count")) parent->count = INT64_MAX;
    else if (!strcmp(which, "payload")) parent->count = 1;
    else if (!strcmp(which, "array-width")) child->pointer_bitmap = 3;
    else if (!strcmp(which, "array-pointer-width")) child->tag = 7;
    else if (!strcmp(which, "array-tag")) child->tag = 99;
    else if (!strcmp(which, "cell-size")) { held[2] = turkey_cell_new(0, 0); header_of(held[2])->size = 1; }
    else if (!strcmp(which, "interior")) parent->slots[0]++;
    else if (!strcmp(which, "foreign")) parent->slots[0] = 1;
    else if (!strcmp(which, "root")) held[0] = (void *)1;
    else if (!strcmp(which, "high-root")) held[65] = (void *)1;
    else if (!strcmp(which, "root-cycle")) frame.previous = &frame;
    else if (!strcmp(which, "region-cycle")) regions->next = regions;
    else if (!strcmp(which, "region-size")) r->slot_size = 0;
    else if (!strcmp(which, "region-capacity")) r->capacity = UINT32_MAX;
    else if (!strcmp(which, "region-count")) r->used++;
    else if (!strcmp(which, "region-class")) r->size_class = 0;
    else if (!strcmp(which, "region-search")) r->search_word = 1;
    else if (!strcmp(which, "heap-count")) heap_count++;
    else if (!strcmp(which, "heap-bytes")) region_bytes++;
    else if (!strcmp(which, "bitmap")) r->allocated[31] |= UINT64_C(1) << 63;
    else if (!strcmp(which, "available")) available[r->size_class] = NULL;
    else if (!strcmp(which, "available-cycle")) r->available_next = r;
    else if (!strcmp(which, "available-foreign")) available[0] = (void *)1;
    else if (!strcmp(which, "sweep-survivor")) {
        forget(parent);
        for (HeapRegion *s = regions; s; s = s->next) {
            memset(s->marked_slots, 0, sizeof s->marked_slots); s->live = 0;
        }
        phase = 2;
    }
    else if (!strcmp(which, "reclaimed")) {
        void *dead = turkey_box(5, 4);
        turkey_collect();
        parent->slots[0] = (uintptr_t)dead;
    }
    else if (!strcmp(which, "allocation-stops")) {
        parent->count = -1;
        gc_verify = 1;
        turkey_gc_set_stress(1);
        expected_count = heap_count;
        atexit(check_stopped);
        turkey_box(3, 4);
        abort();
    }
    else if (!strncmp(which, "native-", 7)) {
        _Alignas(16) uintptr_t stack[16] = {0};
        stack[0] = (uintptr_t)&stack[8]; stack[1] = 1234;
        stack[6] = (uintptr_t)child;
        int64_t offset = -16;
        FrameEntry entry = {1234, 1, &offset};
        frame_entries = &entry; frame_entry_count = 1;
        HeapRegion *list[8]; size_t n = 0;
        for (HeapRegion *s = regions; s; s = s->next) list[n++] = s;
        qsort(list, n, sizeof(*list), heap_check_order);
        HeapCheck check = {list, n, 0};
        if (!strcmp(which, "native-invalid")) stack[6] = 1;
        if (!strcmp(which, "native-offset")) offset = INT64_MIN;
        if (!strcmp(which, "native-table")) frame_entries = NULL;
        if (!strcmp(which, "native-unmarked")) { check.phase = 1; mark_epoch = 1; }
        int ok = heap_check_native(&check, (uintptr_t)stack, (uintptr_t)&stack[15]);
        if (!strcmp(which, "native-valid")) { assert(ok); puts("valid"); return 0; }
        assert(!ok); puts(panic_buffer); return 0;
    }
    assert(!heap_verify(phase));
    assert(turkey_has_panicked);
    puts(panic_buffer);
    return 0;
}
''')
    binary = directory / "probe"
    subprocess.run([shutil.which("cc"), "-std=c11", "-O1", "-fsanitize=undefined",
                    "-I", str(root / "runtime"), str(source), str(allocator_object),
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
    "negative-count": "invalid object header",
    "huge-count": "invalid object count",
    "payload": "payload size disagrees",
    "array-width": "invalid array layout",
    "array-pointer-width": "invalid array layout",
    "array-tag": "invalid array layout",
    "cell-size": "invalid cell size",
    "interior": "invalid traced pointer",
    "foreign": "invalid traced pointer",
    "root": "invalid traced pointer",
    "high-root": "invalid traced pointer",
    "root-cycle": "cyclic root chain",
    "region-cycle": "cyclic region chain",
    "region-size": "invalid region geometry",
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
    "sweep-survivor": "unmarked sweep survivor",
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
    result = subprocess.run([str(verifier_probe), case], capture_output=True,
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


@pytest.mark.parametrize("backend,defect", [
    ("native", "native-roots"), ("llvm", "shadow-roots"),
    ("native", "children"), ("llvm", "children"),
])
def test_broken_collector_is_stopped_before_sweeping(tmp_path, backend, defect):
    from tests import bootc

    source = tmp_path / "main.gob"
    source.write_text('''
fun main() {
    let x = [42]
    let y = [x]
    let z = [y]
    print(z[0][0][0])
}
''')
    result = subprocess.run([str(bootc.binary()), backend, str(source)],
                            cwd=bootc.REPO_ROOT, capture_output=True, text=True,
                            check=True)
    generated = tmp_path / ("main.s" if backend == "native" else "main.ll")
    generated.write_text(result.stdout)
    runtime = (bootc.REPO_ROOT / "runtime/turkey_runtime.c").read_text()
    if defect == "native-roots":
        runtime = runtime.replace("    scan_native_frames();", "    /* injected omission */")
    elif defect == "shadow-roots":
        runtime = runtime.replace("for (RootFrame *frame = roots;", "for (RootFrame *frame = NULL;")
    else:
        runtime = runtime.replace("static void mark_children(void *value) {",
                                  "static void mark_children(void *value) { return;")
    mutated = tmp_path / "runtime.c"
    mutated.write_text(runtime)
    binary = tmp_path / "broken"
    subprocess.run(["cc", "-std=c11", "-O1", "-Wno-override-module", "-I",
                    str(bootc.REPO_ROOT / "runtime"), str(generated), str(mutated),
                    "-lm", "-pthread", "-o", str(binary)],
                   capture_output=True, text=True, check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, TURKEY_GC_STRESS="1", TURKEY_GC_VERIFY="1"))
    assert result.returncode == 1, result.stderr
    assert "heap verifier: reachable object is unmarked" in result.stderr
    assert "42" not in result.stdout
