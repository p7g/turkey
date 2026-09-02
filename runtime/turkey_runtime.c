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
    const char *function_name;
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

void *turkey_root_push(int64_t count, const char *function_name) {
    if (count < 0 || (uint64_t)count >
            (SIZE_MAX - sizeof(RootFrame)) / sizeof(void *)) {
        turkey_panic("invalid root frame size");
        return NULL;
    }
    RootFrame *frame = calloc(1, sizeof(RootFrame) + (size_t)count * sizeof(void *));
    if (frame == NULL) { turkey_panic("out of memory"); return NULL; }
    frame->previous = roots;
    frame->function_name = function_name;
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
                 "%s: root slot %" PRId64 " is not a heap pointer (%p)",
                 frame->function_name, index, value);
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
            if (object->tag >= 6)
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
        } else if (object->kind == 0 || object->kind == 1) {
            for (int64_t index = 0; index < object->count; ++index)
                if (((object->pointer_bitmap >> (3 * index)) & 7) >= 6) {
                    void *child = (void *)(uintptr_t)object->slots[index];
                    if (child != NULL && find_header(child) == NULL) {
                        char message[160];
                        snprintf(message, sizeof(message),
                                 "object tag %d pointer field %" PRId64
                                 " is not a heap pointer (%p)", object->tag, index, child);
                        turkey_panic(message);
                    } else mark(child);
                }
        } else {
            for (int64_t index = 0; index < object->count; ++index)
                if (object->pointer_bitmap & (UINT64_C(1) << index))
                    mark((void *)(uintptr_t)object->slots[index]);
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

static int string_index(TurkeyString *value, int64_t index) {
    if (value != NULL && index >= 0 && index < value->length) return 1;
    char message[128];
    snprintf(message, sizeof(message),
             "string byte index out of bounds: %" PRId64 ", length %" PRId64,
             index, value == NULL ? 0 : value->length);
    turkey_panic(message);
    return 0;
}

static int utf8_width(unsigned char lead) {
    if (lead < 0x80) return 1;
    if (lead >= 0xf0) return 4;
    if (lead >= 0xe0) return 3;
    return 2;
}

static int utf8_boundary(TurkeyString *value, int64_t index) {
    return index == 0 || index == value->length ||
        (index > 0 && index < value->length &&
         (value->bytes[index] & 0xc0) != 0x80);
}

uint8_t turkey_string_byte_at(TurkeyString *value, int64_t index) {
    return string_index(value, index) ? value->bytes[index] : 0;
}

uint32_t turkey_string_decode_at(TurkeyString *value, int64_t index) {
    if (!string_index(value, index)) return 0;
    unsigned char lead = value->bytes[index];
    if ((lead & 0xc0) == 0x80) {
        char message[96];
        snprintf(message, sizeof(message),
                 "byte offset %" PRId64 " is not a character boundary", index);
        turkey_panic(message);
        return 0;
    }
    int width = utf8_width(lead);
    uint32_t scalar = width == 1 ? lead : lead & (0x7f >> width);
    for (int offset = 1; offset < width; ++offset)
        scalar = (scalar << 6) | (value->bytes[index + offset] & 0x3f);
    return scalar;
}

int64_t turkey_string_next_index(TurkeyString *value, int64_t index) {
    if (!string_index(value, index)) return 0;
    if ((value->bytes[index] & 0xc0) == 0x80) {
        char message[96];
        snprintf(message, sizeof(message),
                 "byte offset %" PRId64 " is not a character boundary", index);
        turkey_panic(message);
        return 0;
    }
    return index + utf8_width(value->bytes[index]);
}

TurkeyString *turkey_string_slice(TurkeyString *value, int64_t start, int64_t stop) {
    if (value == NULL || start < 0 || start > stop || stop > value->length) {
        char message[128];
        snprintf(message, sizeof(message),
                 "string slice %" PRId64 "..%" PRId64 " is out of bounds", start, stop);
        turkey_panic(message);
        return NULL;
    }
    if (!utf8_boundary(value, start) || !utf8_boundary(value, stop)) {
        char message[160];
        snprintf(message, sizeof(message),
                 "string slice %" PRId64 "..%" PRId64
                 " does not fall on character boundaries", start, stop);
        turkey_panic(message);
        return NULL;
    }
    return turkey_string_new(value->bytes + start, stop - start);
}

static int64_t byte_find(TurkeyString *haystack, TurkeyString *needle,
                         int64_t start, int reverse) {
    if (haystack == NULL || needle == NULL) return -1;
    if (needle->length == 0)
        return reverse ? haystack->length :
            (start < 0 ? 0 : start > haystack->length ? -1 : start);
    if (needle->length > haystack->length) return -1;
    int64_t last = haystack->length - needle->length;
    if (reverse) {
        for (int64_t index = last; index >= 0; --index)
            if (memcmp(haystack->bytes + index, needle->bytes,
                       (size_t)needle->length) == 0) return index;
    } else {
        if (start < 0) start = 0;
        for (int64_t index = start; index <= last; ++index)
            if (memcmp(haystack->bytes + index, needle->bytes,
                       (size_t)needle->length) == 0) return index;
    }
    return -1;
}

int64_t turkey_string_find(TurkeyString *haystack, TurkeyString *needle,
                           int64_t start) {
    return byte_find(haystack, needle, start, 0);
}

int64_t turkey_string_rfind(TurkeyString *haystack, TurkeyString *needle) {
    return byte_find(haystack, needle, 0, 1);
}

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
    if (count < 0 || count > 63 ||
            ((kind == 0 || kind == 1) && count > 21) || (uint64_t)count >
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

uint64_t turkey_object_get_as(void *pointer, int64_t index, int32_t layout) {
    if (!valid_heap_pointer(pointer)) return 0;
    TurkeyObject *object = pointer;
    uint64_t value = turkey_object_get(pointer, index);
    if (has_panicked || !(object->kind == 0 || object->kind == 1)) return value;
    int32_t stored = (object->pointer_bitmap >> (3 * index)) & 7;
    if (stored == layout || (stored >= 6 && layout >= 6)) return value;
    if (stored == 7 && layout < 6)
        return turkey_unbox((void *)(uintptr_t)value, layout);
    if (stored < 6 && layout == 7)
        return (uint64_t)(uintptr_t)turkey_box(value, stored);
    turkey_panic("object field has the wrong scalar layout");
    return 0;
}

void turkey_object_set_as(void *pointer, int64_t index, uint64_t value,
                          int32_t layout) {
    if (!valid_heap_pointer(pointer)) return;
    TurkeyObject *object = pointer;
    if (!(object->kind == 0 || object->kind == 1)) {
        turkey_object_set(pointer, index, value);
        return;
    }
    int32_t stored = (object->pointer_bitmap >> (3 * index)) & 7;
    if (stored == layout || (stored >= 6 && layout >= 6)) {
        turkey_object_set(pointer, index, value);
    } else if (stored == 7 && layout < 6) {
        void *box = turkey_box(value, layout);
        if (!has_panicked)
            turkey_object_set(pointer, index, (uint64_t)(uintptr_t)box);
    } else if (stored < 6 && layout == 7) {
        uint64_t bits = turkey_unbox((void *)(uintptr_t)value, stored);
        if (!has_panicked) turkey_object_set(pointer, index, bits);
    } else {
        turkey_panic("object field has the wrong scalar layout");
    }
}

void *turkey_box(uint64_t value, int32_t layout) {
    TurkeyObject *box = turkey_object_new(5, layout, 1, 0);
    if (box != NULL) box->slots[0] = value;
    return box;
}

uint64_t turkey_unbox(void *pointer, int32_t layout) {
    if (!valid_object_kind(pointer, 5)) return 0;
    TurkeyObject *box = pointer;
    if (box->tag != layout) {
        turkey_panic("boxed value has the wrong scalar layout");
        return 0;
    }
    return box->slots[0];
}

void *turkey_array_new(int64_t length, uint64_t initial, int32_t element_width,
                       int32_t element_layout) {
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
    array->tag = element_layout;
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

uint64_t turkey_array_get_as(void *pointer, int64_t index, int32_t layout) {
    if (!valid_object_kind(pointer, 2)) return 0;
    TurkeyObject *array = pointer;
    uint64_t value = turkey_array_get(pointer, index);
    if (has_panicked || array->tag == layout ||
            (array->tag >= 6 && layout >= 6)) return value;
    if (array->tag == 7 && layout < 6)
        return turkey_unbox((void *)(uintptr_t)value, layout);
    if (array->tag < 6 && layout == 7)
        return (uint64_t)(uintptr_t)turkey_box(value, array->tag);
    turkey_panic("array element has the wrong scalar layout");
    return 0;
}

void turkey_array_set_as(void *pointer, int64_t index, uint64_t value,
                         int32_t layout) {
    if (!valid_object_kind(pointer, 2)) return;
    TurkeyObject *array = pointer;
    if (array->tag == layout || (array->tag >= 6 && layout >= 6)) {
        turkey_array_set(pointer, index, value);
    } else if (array->tag == 7 && layout < 6) {
        void *box = turkey_box(value, layout);
        if (!has_panicked)
            turkey_array_set(pointer, index, (uint64_t)(uintptr_t)box);
    } else if (array->tag < 6 && layout == 7) {
        uint64_t bits = turkey_unbox((void *)(uintptr_t)value, array->tag);
        if (!has_panicked) turkey_array_set(pointer, index, bits);
    } else {
        turkey_panic("array element has the wrong scalar layout");
    }
}

void *turkey_array_get_boxed(void *pointer, int64_t index) {
    if (!valid_object_kind(pointer, 2)) return NULL;
    TurkeyObject *array = pointer;
    uint64_t value = turkey_array_get(pointer, index);
    if (has_panicked) return NULL;
    if (array->tag >= 6) return (void *)(uintptr_t)value;
    return turkey_box(value, array->tag);
}

void turkey_array_set_boxed(void *pointer, int64_t index, void *value) {
    if (!valid_object_kind(pointer, 2)) return;
    TurkeyObject *array = pointer;
    uint64_t bits = (array->tag >= 6 ? (uint64_t)(uintptr_t)value
                     : turkey_unbox(value, array->tag));
    if (!has_panicked) turkey_array_set(pointer, index, bits);
}

static int array_parts(void *wrapper, TurkeyObject **data, int64_t *length) {
    if (!valid_object_kind(wrapper, 1)) return 0;
    TurkeyObject *outer = wrapper;
    if (outer->count < 1) { turkey_panic("invalid Array value"); return 0; }
    void *storage_pointer = (void *)(uintptr_t)outer->slots[0];
    if (!valid_object_kind(storage_pointer, 1)) return 0;
    TurkeyObject *storage = storage_pointer;
    if (storage->count < 2) { turkey_panic("invalid Array storage"); return 0; }
    void *data_pointer = (void *)(uintptr_t)storage->slots[0];
    if (!valid_object_kind(data_pointer, 2)) return 0;
    *data = data_pointer;
    *length = (int64_t)storage->slots[1];
    if (*length < 0 || *length > (*data)->count) {
        turkey_panic("invalid Array length");
        return 0;
    }
    return 1;
}

static int valid_utf8_bytes(const unsigned char *bytes, int64_t length) {
    for (int64_t index = 0; index < length;) {
        unsigned char lead = bytes[index];
        int width;
        uint32_t scalar;
        if (lead < 0x80) { width = 1; scalar = lead; }
        else if (lead >= 0xc2 && lead <= 0xdf) { width = 2; scalar = lead & 0x1f; }
        else if (lead >= 0xe0 && lead <= 0xef) { width = 3; scalar = lead & 0x0f; }
        else if (lead >= 0xf0 && lead <= 0xf4) { width = 4; scalar = lead & 0x07; }
        else return 0;
        if (index > length - width) return 0;
        for (int offset = 1; offset < width; ++offset) {
            unsigned char byte = bytes[index + offset];
            if ((byte & 0xc0) != 0x80) return 0;
            scalar = (scalar << 6) | (byte & 0x3f);
        }
        if ((width == 2 && scalar < 0x80) ||
                (width == 3 && scalar < 0x800) ||
                (width == 4 && scalar < 0x10000) ||
                (scalar >= 0xd800 && scalar <= 0xdfff) || scalar > 0x10ffff)
            return 0;
        index += width;
    }
    return 1;
}

void *turkey_string_to_byte_storage(TurkeyString *value) {
    if (value == NULL) return NULL;
    TurkeyObject *array = turkey_array_new(value->length, 0, 1, 2);
    if (array != NULL)
        memcpy(array->slots, value->bytes, (size_t)value->length);
    return array;
}

TurkeyString *turkey_string_from_bytes(void *wrapper) {
    TurkeyObject *array;
    int64_t length;
    if (!array_parts(wrapper, &array, &length)) return NULL;
    unsigned char *bytes = (unsigned char *)array->slots;
    if (!valid_utf8_bytes(bytes, length)) {
        turkey_panic("bytes are not valid UTF-8");
        return NULL;
    }
    return turkey_string_new(bytes, length);
}

int32_t turkey_string_is_valid_utf8(void *wrapper) {
    TurkeyObject *array;
    int64_t length;
    if (!array_parts(wrapper, &array, &length)) return 0;
    return valid_utf8_bytes((unsigned char *)array->slots, length);
}

TurkeyString *turkey_string_concat_all(void *wrapper) {
    TurkeyObject *array;
    int64_t count;
    if (!array_parts(wrapper, &array, &count)) return NULL;
    int64_t length = 0;
    for (int64_t index = 0; index < count; ++index) {
        TurkeyString *part = (TurkeyString *)(uintptr_t)array->slots[index];
        if (part == NULL || part->length > INT64_MAX - length) {
            turkey_panic("string is too large");
            return NULL;
        }
        length += part->length;
    }
    TurkeyString *result = heap_allocate(sizeof(TurkeyString) + (size_t)length,
                                         HEAP_STRING);
    if (result == NULL) return NULL;
    result->length = length;
    int64_t offset = 0;
    for (int64_t index = 0; index < count; ++index) {
        TurkeyString *part = (TurkeyString *)(uintptr_t)array->slots[index];
        memcpy(result->bytes + offset, part->bytes, (size_t)part->length);
        offset += part->length;
    }
    return result;
}

void *turkey_closure_new(uint64_t code, int64_t capture_count,
                         uint64_t pointer_bitmap) {
    RootFrame *frame = turkey_root_push(1, "turkey_closure_new");
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
