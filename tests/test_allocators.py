"""The Turkey allocator exports, called from C without running the program."""

import os
import subprocess

import pytest

from tests import bootc, toolchain
from tests.allocator_probe import allocator_object, stats_object


@pytest.fixture(scope="module")
def allocator_probe(stats_object, tmp_path_factory):
    toolchain.needs_sanitizer()
    directory = tmp_path_factory.mktemp("allocator-probe")
    source = directory / "probe.c"
    source.write_text(r'''
#include "turkey_exports.h"
#include <assert.h>
#include <string.h>

/* An object's header, as `Turkey.Alloc` writes it: the generation, the
   kind, the count -- the traced words the fields start with, an array's
   element layout code, or a cell's flag -- and the tag. Then the fields; an
   array's length and elements; or a cell's value. A `String` is a byte
   array. */
typedef struct Header { uint8_t generation, kind; uint16_t count; int32_t tag; } Header;
typedef struct TurkeyObject { Header header; uint64_t slots[]; } TurkeyObject;
typedef struct TurkeyArray { Header header; int64_t length; unsigned char elements[]; } TurkeyArray;
static int64_t string_length(void *s) { return ((TurkeyArray *)s)->length; }
static const unsigned char *string_bytes(void *s) { return ((TurkeyArray *)s)->elements; }

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
/* A cell: its header, whose count is 1 if the value is a pointer, and the
   value. */
typedef struct TurkeyCell { Header header; uint64_t value; } TurkeyCell;

static void panic_is(const char *message) {
    assert(turkey_has_panicked);
    assert(strcmp(turkey_panic_message(), message) == 0);
    turkey_panic_clear();
}

int main(void) {
    turkey_giblets_initialize();
    RootFrame frame;
    void *held[32] = {0};
    turkey_root_enter(&frame, held, 32, "allocator probe");
    frame.live = -1;
    assert(sizeof(Header) == 8);
    assert(sizeof(TurkeyCell) == 16);
    assert(sizeof(TurkeyObject) == 8);

    held[0] = turkey_object_new(0, -1, 168, 0);
    TurkeyObject *object = held[0];
    assert(object->header.generation == 0 && object->header.kind == 0);
    assert(object->header.tag == -1 && object->header.count == 0);
    for (int i = 0; i < 21; i++) assert(object->slots[i] == 0);
    held[1] = turkey_object_new(4, -1, 504, 63);
    object = held[1];
    assert(object->header.kind == 4 && object->header.count == 63);
    for (int i = 0; i < 63; i++) assert(object->slots[i] == 0);
    held[2] = turkey_object_new(1, 123, 0, 0);
    object = held[2];
    assert(object->header.kind == 1 && object->header.tag == 123 && object->header.count == 0);

    int slot = 3;
    for (int width = 1; width <= 8; width *= 2) {
        if (width == 2) continue;
        /* The layout code that gives this width. */
        int code = width == 1 ? 2 : width == 4 ? 3 : 6;
        for (int filled = 0; filled < 2; filled++) {
            uint64_t bits = filled ? UINT64_C(0xfedcba98deadbeef) : 0;
            held[slot] = turkey_array_new(3, bits, width, code);
            TurkeyArray *array = held[slot++];
            assert(array->header.generation == 0 && array->header.kind == 2);
            assert(array->header.count == code && array->length == 3);
            for (int i = 0; i < 3; i++) {
                uint64_t actual = 0;
                memcpy(&actual, array->elements + i * width, width);
                uint64_t mask = width == 8 ? UINT64_MAX : (UINT64_C(1) << (8 * width)) - 1;
                assert(actual == (bits & mask));
            }
        }
        held[slot] = turkey_array_new(0, 42, width, code);
        assert(((TurkeyArray *)held[slot++])->length == 0);
    }

    held[12] = turkey_cell_new((uintptr_t)held[0], 1);
    TurkeyCell *cell = held[12];
    assert(cell->header.kind == 5 && cell->header.count == 1);
    assert(cell->value == (uintptr_t)held[0]);
    held[13] = turkey_cell_new(UINT64_MAX, 0);
    cell = held[13];
    assert(cell->header.kind == 5 && cell->header.count == 0);
    assert(cell->value == UINT64_MAX);
    held[15] = turkey_closure_shell(UINT64_C(0xabcdef1234567890));
    object = held[15];
    assert(object->header.kind == 3 && object->header.tag == -1);
    assert(object->header.count == 1);
    assert(object->slots[0] == 0);
    assert(object->slots[1] == UINT64_C(0xabcdef1234567890));
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
    uint64_t element = 0;
    memcpy(&element, ((TurkeyArray *)held[18])->elements + 16, 8);
    assert(memcmp(string_bytes((void *)(uintptr_t)element), "a\0b", 3) == 0);
    assert(!turkey_has_panicked);

    assert(turkey_object_new(0, 0, 12, 0) == NULL);
    panic_is("invalid object size");
    assert(turkey_object_new(4, 0, 8, 2) == NULL);
    panic_is("invalid object size");
    assert(turkey_object_new(2, 0, 8, 0) == NULL);
    panic_is("invalid object size");
    assert(turkey_object_new(4, 0, -1, 0) == NULL);
    panic_is("invalid object size");
    /* The header counts the traced words in 16 bits. */
    assert(turkey_object_new(1, 0, 8 * 65536, 65536) == NULL);
    panic_is("invalid object size");
    assert(turkey_array_new(-1, 0, 8, 0) == NULL);
    panic_is("array length cannot be negative");
    assert(turkey_string_new(NULL, -1) == NULL);
    panic_is("array length cannot be negative");
    assert(turkey_array_new(0, 0, 2, 0) == NULL);
    panic_is("invalid array size");
    /* The layout code is all the header keeps, so it has to give the width. */
    assert(turkey_array_new(1, 0, 1, 6) == NULL);
    panic_is("invalid array size");
    assert(turkey_array_new(1, 0, 8, 8) == NULL);
    panic_is("invalid array size");
    assert(turkey_array_new(INT64_C(4611686018427387900), 0, 4, 3) == NULL);
    panic_is("invalid array size");
    assert(turkey_array_new(INT64_C(2305843009213693950), 0, 8, 4) == NULL);
    panic_is("invalid array size");
    /* Accepted sizes still have to leave room for a region's own fields. */
    assert(turkey_array_new(INT64_C(4611686018427387899), 0, 4, 3) == NULL);
    panic_is("allocation is too large");
    assert(turkey_array_new(INT64_C(2305843009213693949), 0, 8, 4) == NULL);
    panic_is("allocation is too large");
    turkey_root_leave(&frame);
    turkey_collect();
    assert(turkey_heap_objects() == 0);
    turkey_gc_report();
    return 0;
}
''')
    binary = directory / "probe"
    subprocess.run([*toolchain.cc(), "-std=c11",
                    "-O1", "-fsanitize=undefined",
                    "-I", str(bootc.PROBE_INCLUDE), str(source),
                    str(stats_object), "-lm", "-pthread", "-o",
                    str(binary)], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize("stress", [False, True])
def test_allocator_exports_before_initialization(allocator_probe, stress):
    env = dict(os.environ)
    env.pop("TURKEY_GC_STRESS", None)
    if stress:
        env["TURKEY_GC_STRESS"] = "1"
    result = subprocess.run(toolchain.command(allocator_probe), env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr
    assert all(line.startswith("[gc") for line in result.stderr.splitlines())
    assert ", allocations 18," in result.stderr
    assert ("[gc] by kind: constr 1, record 1, array 12, closure 1, closure-env 1, "
            "cell 2\n") in result.stderr


def test_allocator_symbols_are_defined_only_in_turkey(allocator_object):
    names = ["turkey_cell_new", "turkey_object_new", "turkey_array_new",
             "turkey_closure_shell", "turkey_string_new",
             "turkey_root_enter", "turkey_root_leave", "turkey_frame_table_register",
             "turkey_entry_stack_set", "turkey_collect", "turkey_gc_report",
             "turkey_gc_set_stress", "turkey_heap_objects", "turkey_roots_head",
             "turkey_giblets_initialize", "turkey_panic", "turkey_panic_string",
             "turkey_exit", "turkey_entry_returned"]
    def defined(path):
        result = subprocess.run(["nm", "-g", str(path)], check=True,
                                capture_output=True, text=True)
        return [line.split()[-1].lstrip("_") for line in result.stdout.splitlines()
                if len(line.split()) >= 3 and line.split()[-2].upper() == "T"]
    generated = defined(allocator_object)
    for name in names:
        assert generated.count(name) == 1

