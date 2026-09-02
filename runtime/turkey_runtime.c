#include "turkey_runtime.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct TurkeyCell { uint64_t value; int32_t pointer_value; } TurkeyCell;
typedef struct TurkeyObject {
    int32_t kind;
    int32_t tag;
    int64_t count;
    uint64_t pointer_bitmap;
    uint64_t slots[];
} TurkeyObject;

static char panic_buffer[256];
static int32_t has_panicked;

typedef struct HeapHeader {
    struct HeapHeader *next;
    size_t size;
    uint32_t kind;
    uint32_t marked;
} HeapHeader;

typedef struct RootFrame {
    struct RootFrame *previous;
    int64_t count;
    void *values[];
} RootFrame;

enum { HEAP_STRING = 1, HEAP_OBJECT = 2, HEAP_CELL = 3 };
static HeapHeader *heap;
static RootFrame *roots;
static int64_t heap_count;
static int64_t allocation_count;
static int gc_stress = -1;

static void mark(void *value);
static HeapHeader *header_of(void *value);

static HeapHeader *find_header(void *value) {
    for (HeapHeader *header = heap; header != NULL; header = header->next)
        if ((void *)(header + 1) == value) return header;
    return NULL;
}

static int valid_heap_pointer(void *value) {
    if (!gc_stress || value == NULL) return value != NULL;
    if (find_header(value) != NULL) return 1;
    turkey_panic("invalid or collected heap pointer");
    return 0;
}

static int valid_object_kind(void *value, int32_t kind) {
    if (!valid_heap_pointer(value)) return 0;
    if (header_of(value)->kind != HEAP_OBJECT || ((TurkeyObject *)value)->kind != kind) {
        turkey_panic("heap object has the wrong runtime kind");
        return 0;
    }
    return 1;
}

void *turkey_root_push(int64_t count) {
    if (count < 0 || (uint64_t)count >
            (SIZE_MAX - sizeof(RootFrame)) / sizeof(void *)) {
        turkey_panic("invalid root frame size");
        return NULL;
    }
    RootFrame *frame = calloc(1, sizeof(RootFrame) + (size_t)count * sizeof(void *));
    if (frame == NULL) { turkey_panic("out of memory"); return NULL; }
    frame->previous = roots;
    frame->count = count;
    roots = frame;
    return frame;
}

void turkey_root_set(void *pointer, int64_t index, void *value) {
    RootFrame *frame = pointer;
    if (frame == NULL || index < 0 || index >= frame->count) {
        turkey_panic("invalid root slot");
        return;
    }
    if (gc_stress && value != NULL && find_header(value) == NULL) {
        char message[128];
        snprintf(message, sizeof(message),
                 "root slot %" PRId64 " is not a heap pointer (%p)", index, value);
        turkey_panic(message);
        return;
    }
    frame->values[index] = value;
}

void turkey_root_pop(void *pointer) {
    RootFrame *frame = pointer;
    if (frame == NULL || roots != frame) { turkey_panic("unbalanced root frame"); return; }
    roots = frame->previous;
    free(frame);
}

static HeapHeader *header_of(void *value) {
    return value == NULL ? NULL : ((HeapHeader *)value) - 1;
}

static void *heap_allocate(size_t size, uint32_t kind) {
    if (gc_stress < 0) gc_stress = getenv("TURKEY_GC_STRESS") != NULL;
    if (gc_stress || (allocation_count > 1024 && allocation_count > heap_count * 2))
        turkey_collect();
    if (size > SIZE_MAX - sizeof(HeapHeader)) {
        turkey_panic("allocation is too large"); return NULL;
    }
    HeapHeader *header = malloc(sizeof(HeapHeader) + size);
    if (header == NULL) { turkey_panic("out of memory"); return NULL; }
    header->next = heap;
    header->size = size;
    header->kind = kind;
    header->marked = 0;
    heap = header;
    heap_count++;
    allocation_count++;
    return header + 1;
}

static void mark(void *value) {
    if (value == NULL) return;
    HeapHeader *header = find_header(value);
    if (header == NULL) {
        char message[128];
        snprintf(message, sizeof(message),
                 "GC root or field is not a heap pointer (%p)", value);
        turkey_panic(message);
        return;
    }
    if (header->marked) return;
    header->marked = 1;
    if (header->kind == HEAP_CELL) {
        TurkeyCell *cell = value;
        if (cell->pointer_value) mark((void *)(uintptr_t)cell->value);
    } else if (header->kind == HEAP_OBJECT) {
        TurkeyObject *object = value;
        if (object->kind == 2) {
            if (object->tag)
                for (int64_t index = 0; index < object->count; ++index) {
                    void *child = (void *)(uintptr_t)object->slots[index];
                    if (child != NULL && find_header(child) == NULL) {
                        char message[160];
                        snprintf(message, sizeof(message),
                                 "array pointer field %" PRId64
                                 " is not a heap pointer (%p)", index, child);
                        turkey_panic(message);
                    } else mark(child);
                }
        } else {
            for (int64_t index = 0; index < object->count; ++index)
                if (object->pointer_bitmap & (UINT64_C(1) << index)) {
                    void *child = (void *)(uintptr_t)object->slots[index];
                    if (child != NULL && find_header(child) == NULL) {
                        char message[160];
                        snprintf(message, sizeof(message),
                                 "object tag %d pointer field %" PRId64
                                 " is not a heap pointer (%p)", object->tag, index, child);
                        turkey_panic(message);
                    } else mark(child);
                }
        }
    }
}

void turkey_collect(void) {
    for (RootFrame *frame = roots; frame != NULL; frame = frame->previous)
        for (int64_t index = 0; index < frame->count; ++index) mark(frame->values[index]);
    HeapHeader **link = &heap;
    while (*link != NULL) {
        HeapHeader *header = *link;
        if (!header->marked) {
            *link = header->next;
            free(header);
            heap_count--;
        } else {
            header->marked = 0;
            link = &header->next;
        }
    }
}

int64_t turkey_heap_objects(void) { return heap_count; }
void turkey_gc_set_stress(int32_t enabled) { gc_stress = enabled != 0; }

void turkey_panic(const char *message) {
    if (!has_panicked) {
        snprintf(panic_buffer, sizeof(panic_buffer), "%s", message);
        has_panicked = 1;
    }
}

int32_t turkey_panicked(void) { return has_panicked; }
const char *turkey_panic_message(void) { return panic_buffer; }
void turkey_panic_clear(void) { has_panicked = 0; panic_buffer[0] = '\0'; }

TurkeyString *turkey_string_new(const unsigned char *bytes, int64_t length) {
    if (length < 0 || (uint64_t)length > SIZE_MAX - sizeof(TurkeyString)) {
        turkey_panic("invalid string length");
        return NULL;
    }
    TurkeyString *result = heap_allocate(sizeof(TurkeyString) + (size_t)length,
                                         HEAP_STRING);
    if (result == NULL) return NULL;
    result->length = length;
    memcpy(result->bytes, bytes, (size_t)length);
    return result;
}

TurkeyString *turkey_string_concat(TurkeyString *left, TurkeyString *right) {
    if (left == NULL || right == NULL || left->length > INT64_MAX - right->length) {
        turkey_panic("string is too large");
        return NULL;
    }
    int64_t length = left->length + right->length;
    TurkeyString *result = heap_allocate(sizeof(TurkeyString) + (size_t)length,
                                         HEAP_STRING);
    if (result == NULL) return NULL;
    result->length = length;
    memcpy(result->bytes, left->bytes, (size_t)left->length);
    memcpy(result->bytes + left->length, right->bytes, (size_t)right->length);
    return result;
}

TurkeyString *turkey_int_to_string(int64_t value) {
    char buffer[32];
    int length = snprintf(buffer, sizeof(buffer), "%" PRId64, value);
    return turkey_string_new((const unsigned char *)buffer, length);
}

TurkeyString *turkey_float_to_string(double value) {
    char buffer[64];
    int length;
    if (isnan(value)) length = snprintf(buffer, sizeof(buffer), "NaN");
    else if (isinf(value)) length = snprintf(buffer, sizeof(buffer),
                                             signbit(value) ? "-Infinity" : "Infinity");
    else {
        length = snprintf(buffer, sizeof(buffer), "%.17g", value);
        if (strchr(buffer, '.') == NULL && strchr(buffer, 'e') == NULL) {
            buffer[length++] = '.'; buffer[length++] = '0'; buffer[length] = '\0';
        }
    }
    return turkey_string_new((const unsigned char *)buffer, length);
}

TurkeyString *turkey_char_to_string(uint32_t value) {
    unsigned char out[4];
    int64_t n;
    if (value <= 0x7f) { out[0] = value; n = 1; }
    else if (value <= 0x7ff) {
        out[0] = 0xc0 | (value >> 6); out[1] = 0x80 | (value & 0x3f); n = 2;
    } else if (value <= 0xffff && !(value >= 0xd800 && value <= 0xdfff)) {
        out[0] = 0xe0 | (value >> 12); out[1] = 0x80 | ((value >> 6) & 0x3f);
        out[2] = 0x80 | (value & 0x3f); n = 3;
    } else if (value <= 0x10ffff) {
        out[0] = 0xf0 | (value >> 18); out[1] = 0x80 | ((value >> 12) & 0x3f);
        out[2] = 0x80 | ((value >> 6) & 0x3f); out[3] = 0x80 | (value & 0x3f); n = 4;
    } else {
        turkey_panic("invalid Unicode scalar value"); return NULL;
    }
    return turkey_string_new(out, n);
}

int64_t turkey_string_byte_length(TurkeyString *value) { return value->length; }

int32_t turkey_string_eq(TurkeyString *left, TurkeyString *right) {
    return left->length == right->length &&
        memcmp(left->bytes, right->bytes, (size_t)left->length) == 0;
}

int32_t turkey_string_lt(TurkeyString *left, TurkeyString *right) {
    size_t common = (size_t)(left->length < right->length ? left->length : right->length);
    int order = memcmp(left->bytes, right->bytes, common);
    return order < 0 || (order == 0 && left->length < right->length);
}

uint8_t turkey_write(TurkeyString *value) {
    if (value == NULL) return 0;
    fwrite(value->bytes, 1, (size_t)value->length, stdout);
    fflush(stdout);
    return 0;
}

uint8_t turkey_print(TurkeyString *value) {
    turkey_write(value); fputc('\n', stdout); fflush(stdout); return 0;
}

void *turkey_cell_new(uint64_t value, int32_t pointer_value) {
    TurkeyCell *cell = heap_allocate(sizeof(TurkeyCell), HEAP_CELL);
    if (cell != NULL) { cell->value = value; cell->pointer_value = pointer_value; }
    return cell;
}

uint64_t turkey_cell_load(void *pointer) { return ((TurkeyCell *)pointer)->value; }
void turkey_cell_store(void *pointer, uint64_t value) {
    ((TurkeyCell *)pointer)->value = value;
}

void *turkey_object_new(int32_t kind, int32_t tag, int64_t count,
                        uint64_t pointer_bitmap) {
    if (count < 0 || count > 63 || (uint64_t)count >
            (SIZE_MAX - sizeof(TurkeyObject)) / sizeof(uint64_t)) {
        turkey_panic("invalid object size");
        return NULL;
    }
    TurkeyObject *object = heap_allocate(
        sizeof(TurkeyObject) + (size_t)count * sizeof(uint64_t), HEAP_OBJECT);
    if (object == NULL) return NULL;
    object->kind = kind;
    object->tag = tag;
    object->count = count;
    object->pointer_bitmap = pointer_bitmap;
    memset(object->slots, 0, (size_t)count * sizeof(uint64_t));
    return object;
}

int32_t turkey_object_tag(void *pointer) {
    if (!valid_heap_pointer(pointer)) return -1;
    return ((TurkeyObject *)pointer)->tag;
}

uint64_t turkey_object_get(void *pointer, int64_t index) {
    if (!valid_heap_pointer(pointer)) return 0;
    TurkeyObject *object = pointer;
    if (index < 0 || index >= object->count) {
        turkey_panic("invalid object field");
        return 0;
    }
    return object->slots[index];
}

void turkey_object_set(void *pointer, int64_t index, uint64_t value) {
    if (!valid_heap_pointer(pointer)) return;
    TurkeyObject *object = pointer;
    if (index < 0 || index >= object->count) {
        turkey_panic("invalid object field");
        return;
    }
    object->slots[index] = value;
}

void *turkey_array_new(int64_t length, uint64_t initial, int32_t element_width,
                       int32_t pointer_elements) {
    if (length < 0) {
        turkey_panic("array length cannot be negative");
        return NULL;
    }
    if (!(element_width == 1 || element_width == 4 || element_width == 8) ||
            (uint64_t)length > (SIZE_MAX - sizeof(TurkeyObject)) /
            (uint32_t)element_width) {
        turkey_panic("invalid array size");
        return NULL;
    }
    TurkeyObject *array = heap_allocate(
        sizeof(TurkeyObject) + (size_t)length * (uint32_t)element_width,
        HEAP_OBJECT);
    if (array == NULL) return NULL;
    array->kind = 2;
    array->tag = pointer_elements != 0;
    array->count = length;
    array->pointer_bitmap = (uint32_t)element_width;
    for (int64_t index = 0; index < length; ++index) {
        unsigned char *slot = (unsigned char *)array->slots + index * element_width;
        memcpy(slot, &initial, (size_t)element_width);
    }
    return array;
}

int64_t turkey_array_length(void *pointer) {
    if (!valid_heap_pointer(pointer)) return 0;
    return ((TurkeyObject *)pointer)->count;
}

static int array_index(TurkeyObject *array, int64_t index, const char *operation) {
    if (index < 0 || index >= array->count) {
        char message[128];
        snprintf(message, sizeof(message),
                 "array index out of bounds: %s index %" PRId64 ", length %" PRId64,
                 operation, index, array->count);
        turkey_panic(message);
        return 0;
    }
    return 1;
}

uint64_t turkey_array_get(void *pointer, int64_t index) {
    if (!valid_heap_pointer(pointer)) return 0;
    TurkeyObject *array = pointer;
    uint64_t value = 0;
    if (array_index(array, index, "read at")) {
        int32_t width = (int32_t)array->pointer_bitmap;
        memcpy(&value, (unsigned char *)array->slots + index * width, (size_t)width);
    }
    return value;
}

void turkey_array_set(void *pointer, int64_t index, uint64_t value) {
    if (!valid_heap_pointer(pointer)) return;
    TurkeyObject *array = pointer;
    if (array_index(array, index, "write at")) {
        int32_t width = (int32_t)array->pointer_bitmap;
        memcpy((unsigned char *)array->slots + index * width, &value, (size_t)width);
    }
}

void *turkey_closure_new(uint64_t code, int64_t capture_count,
                         uint64_t pointer_bitmap) {
    RootFrame *frame = turkey_root_push(1);
    TurkeyObject *environment = turkey_object_new(
        4, -1, capture_count, pointer_bitmap);
    if (environment == NULL) { turkey_root_pop(frame); return NULL; }
    turkey_root_set(frame, 0, environment);
    TurkeyObject *closure = turkey_object_new(3, -1, 2, 2);
    if (closure == NULL) { turkey_root_pop(frame); return NULL; }
    closure->slots[0] = code;
    closure->slots[1] = (uint64_t)(uintptr_t)environment;
    turkey_root_pop(frame);
    return closure;
}

uint64_t turkey_closure_code(void *pointer) {
    if (!valid_object_kind(pointer, 3)) return 0;
    return ((TurkeyObject *)pointer)->slots[0];
}

void *turkey_closure_environment(void *pointer) {
    if (!valid_object_kind(pointer, 3)) return NULL;
    return (void *)(uintptr_t)((TurkeyObject *)pointer)->slots[1];
}

void turkey_closure_capture(void *pointer, int64_t index, uint64_t value) {
    if (!valid_object_kind(pointer, 3)) return;
    TurkeyObject *closure = pointer;
    turkey_object_set((void *)(uintptr_t)closure->slots[1], index, value);
}
