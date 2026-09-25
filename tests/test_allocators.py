"""The Turkey allocator exports, called from C before module initialization."""

import os
import subprocess

import pytest

from tests import bootc, toolchain
from tests.allocator_probe import allocator_object


@pytest.fixture(scope="module")
def allocator_probe(allocator_object, tmp_path_factory):
    directory = tmp_path_factory.mktemp("allocator-probe")
    source = directory / "probe.c"
    source.write_text(r'''
#include "turkey_runtime.c"
#include <assert.h>

static void panic_is(const char *message) {
    assert(turkey_has_panicked);
    assert(strcmp(turkey_panic_message(), message) == 0);
    turkey_panic_clear();
}

int main(void) {
    RootFrame frame;
    void *held[32] = {0};
    turkey_root_enter(&frame, held, 32, "allocator probe");
    frame.live = -1;
    assert(sizeof(TurkeyCell) == 16);
    assert(sizeof(TurkeyObject) == 24);

    held[0] = turkey_object_new(0, -1, 21, 0);
    TurkeyObject *object = held[0];
    assert(object->kind == 0 && object->tag == -1 && object->count == 21);
    assert(object->pointer_bitmap == 0);
    for (int i = 0; i < 21; i++) assert(object->slots[i] == 0);
    held[1] = turkey_object_new(4, -1, 63, UINT64_MAX);
    object = held[1];
    assert(object->pointer_bitmap == UINT64_MAX);
    for (int i = 0; i < 63; i++) assert(object->slots[i] == 0);
    held[2] = turkey_object_new(1, 123, 0, 0);
    assert(((TurkeyObject *)held[2])->count == 0);

    int slot = 3;
    for (int width = 1; width <= 8; width *= 2) {
        if (width == 2) continue;
        for (int filled = 0; filled < 2; filled++) {
            uint64_t bits = filled ? UINT64_C(0xfedcba98deadbeef) : 0;
            held[slot] = turkey_array_new(3, bits, width, 6);
            TurkeyObject *array = held[slot++];
            assert(array->kind == 2 && array->tag == 6 && array->count == 3);
            assert(array->pointer_bitmap == (uint64_t)width);
            for (int i = 0; i < 3; i++) {
                uint64_t actual = 0;
                memcpy(&actual, (unsigned char *)array->slots + i * width, width);
                uint64_t mask = width == 8 ? UINT64_MAX : (UINT64_C(1) << (8 * width)) - 1;
                assert(actual == (bits & mask));
            }
        }
        held[slot] = turkey_array_new(0, 42, width, 6);
        assert(((TurkeyObject *)held[slot++])->count == 0);
    }

    held[12] = turkey_cell_new((uintptr_t)held[0], 1);
    assert(((TurkeyCell *)held[12])->value == (uintptr_t)held[0]);
    assert(((TurkeyCell *)held[12])->pointer_value == 1);
    held[13] = turkey_cell_new(UINT64_MAX, 0);
    assert(((TurkeyCell *)held[13])->value == UINT64_MAX);
    assert(((TurkeyCell *)held[13])->pointer_value == 0);
    for (int layout = 0; layout <= 6; layout++) {
        uint64_t bits = UINT64_C(0xfff812345678abcd);
        held[14] = turkey_box(bits, layout);
        assert(turkey_unbox(held[14], layout) == bits);
    }
    held[15] = turkey_closure_shell(UINT64_C(0xabcdef1234567890));
    object = held[15];
    assert(object->kind == 3 && object->tag == -1 && object->count == 2);
    assert(object->pointer_bitmap == 2);
    assert(object->slots[0] == UINT64_C(0xabcdef1234567890));
    assert(object->slots[1] == 0);
    unsigned char bytes[] = {'a', 0, 'b'};
    held[16] = turkey_string_new(bytes, 3);
    bytes[0] = 'z';
    assert(string_length(held[16]) == 3);
    assert(memcmp(string_bytes(held[16]), "a\0b", 3) == 0);
    held[17] = turkey_string_new(NULL, 0);
    assert(string_length(held[17]) == 0);
    held[18] = turkey_array_new(3, (uintptr_t)held[16], 8, 7);
    held[16] = NULL;
    turkey_collect();
    object = held[18];
    assert(memcmp(string_bytes((void *)(uintptr_t)object->slots[2]), "a\0b", 3) == 0);
    assert(!turkey_has_panicked);

    assert(turkey_object_new(0, 0, 22, 0) == NULL);
    panic_is("invalid object size");
    assert(turkey_object_new(4, 0, 64, 0) == NULL);
    panic_is("invalid object size");
    assert(turkey_object_new(4, 0, -1, 0) == NULL);
    panic_is("invalid object size");
    assert(turkey_array_new(-1, 0, 8, 0) == NULL);
    panic_is("array length cannot be negative");
    assert(turkey_string_new(NULL, -1) == NULL);
    panic_is("array length cannot be negative");
    assert(turkey_array_new(0, 0, 2, 0) == NULL);
    panic_is("invalid array size");
    assert(turkey_array_new(INT64_C(4611686018427387898), 0, 4, 0) == NULL);
    panic_is("invalid array size");
    assert(turkey_array_new(INT64_C(2305843009213693949), 0, 8, 0) == NULL);
    panic_is("invalid array size");
    /* Accepted payload sizes still have to leave room for the heap header. */
    assert(turkey_array_new(INT64_C(4611686018427387897), 0, 4, 0) == NULL);
    panic_is("allocation is too large");
    assert(turkey_array_new(INT64_C(2305843009213693948), 0, 8, 0) == NULL);
    panic_is("allocation is too large");
    assert(turkey_unbox(held[14], 5) == 0);
    panic_is("boxed value has the wrong scalar layout");
    assert(turkey_unbox(held[18], 4) == 0);
    panic_is("heap object has the wrong runtime kind");
    assert(turkey_unbox(NULL, 4) == 0);
    assert(!turkey_has_panicked);
    turkey_gc_set_stress(1);
    assert(turkey_unbox((void *)8, 4) == 0);
    panic_is("invalid or collected heap pointer");
    turkey_root_leave(&frame);
    turkey_collect();
    assert(turkey_heap_objects() == 0);
    assert(stats_allocations == 25);
    assert(stats_by_kind[0] == 1 && stats_by_kind[1] == 1);
    assert(stats_by_kind[2] == 12 && stats_by_kind[3] == 1);
    assert(stats_by_kind[4] == 1 && stats_by_kind[5] == 7);
    assert(stats_by_kind[6] == 0 && stats_by_kind[7] == 2);
    return 0;
}
''')
    binary = directory / "probe"
    subprocess.run([*toolchain.cc(), "-std=c11", "-O1", "-fsanitize=undefined",
                    "-I", str(bootc.REPO_ROOT / "runtime"), str(source),
                    str(allocator_object), "-lm", "-pthread", "-o",
                    str(binary)], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize("stress", [False, True])
def test_allocator_exports_before_initialization(allocator_probe, stress):
    env = dict(os.environ, TURKEY_GC_STATS="1")
    env.pop("TURKEY_GC_STRESS", None)
    if stress:
        env["TURKEY_GC_STRESS"] = "1"
    result = subprocess.run(toolchain.command(allocator_probe), env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr
    assert all(line.startswith("[gc ") for line in result.stderr.splitlines())


def test_allocator_symbols_are_defined_only_in_turkey(allocator_object):
    names = ["turkey_cell_new", "turkey_object_new", "turkey_array_new",
             "turkey_box", "turkey_unbox", "turkey_closure_shell", "turkey_string_new"]
    def defined(path):
        result = subprocess.run(["nm", "-g", str(path)], check=True,
                                capture_output=True, text=True)
        return [line.split()[-1].lstrip("_") for line in result.stdout.splitlines()
                if len(line.split()) >= 3 and line.split()[-2].upper() == "T"]
    generated = defined(allocator_object)
    runtime = defined(bootc.runtime_object())
    for name in names:
        assert generated.count(name) == 1
        assert name not in runtime
