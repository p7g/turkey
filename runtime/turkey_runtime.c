#include "turkey_runtime.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct TurkeyCell { uint64_t value; } TurkeyCell;
typedef struct TurkeyObject {
    int32_t kind;
    int32_t tag;
    int64_t count;
    uint64_t pointer_bitmap;
    uint64_t slots[];
} TurkeyObject;

static char panic_buffer[256];
static int32_t has_panicked;

void turkey_panic(const char *message) {
    if (!has_panicked) {
        snprintf(panic_buffer, sizeof(panic_buffer), "%s", message);
        has_panicked = 1;
    }
}

int32_t turkey_panicked(void) { return has_panicked; }
const char *turkey_panic_message(void) { return panic_buffer; }
void turkey_panic_clear(void) { has_panicked = 0; panic_buffer[0] = '\0'; }

static void *checked_malloc(size_t size) {
    void *result = malloc(size);
    if (result == NULL) turkey_panic("out of memory");
    return result;
}

TurkeyString *turkey_string_new(const unsigned char *bytes, int64_t length) {
    if (length < 0 || (uint64_t)length > SIZE_MAX - sizeof(TurkeyString)) {
        turkey_panic("invalid string length");
        return NULL;
    }
    TurkeyString *result = checked_malloc(sizeof(TurkeyString) + (size_t)length);
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
    TurkeyString *result = checked_malloc(sizeof(TurkeyString) + (size_t)length);
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

void *turkey_cell_new(uint64_t value) {
    TurkeyCell *cell = checked_malloc(sizeof(TurkeyCell));
    if (cell != NULL) cell->value = value;
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
    TurkeyObject *object = checked_malloc(
        sizeof(TurkeyObject) + (size_t)count * sizeof(uint64_t));
    if (object == NULL) return NULL;
    object->kind = kind;
    object->tag = tag;
    object->count = count;
    object->pointer_bitmap = pointer_bitmap;
    memset(object->slots, 0, (size_t)count * sizeof(uint64_t));
    return object;
}

int32_t turkey_object_tag(void *pointer) {
    return ((TurkeyObject *)pointer)->tag;
}

uint64_t turkey_object_get(void *pointer, int64_t index) {
    TurkeyObject *object = pointer;
    if (index < 0 || index >= object->count) {
        turkey_panic("invalid object field");
        return 0;
    }
    return object->slots[index];
}

void turkey_object_set(void *pointer, int64_t index, uint64_t value) {
    TurkeyObject *object = pointer;
    if (index < 0 || index >= object->count) {
        turkey_panic("invalid object field");
        return;
    }
    object->slots[index] = value;
}

void *turkey_array_new(int64_t length, uint64_t initial, int32_t pointer_elements) {
    TurkeyObject *array = turkey_object_new(2, pointer_elements, length,
                                             pointer_elements ? UINT64_MAX : 0);
    if (array == NULL) return NULL;
    for (int64_t index = 0; index < length; ++index) array->slots[index] = initial;
    return array;
}

int64_t turkey_array_length(void *pointer) {
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
    TurkeyObject *array = pointer;
    return array_index(array, index, "read at") ? array->slots[index] : 0;
}

void turkey_array_set(void *pointer, int64_t index, uint64_t value) {
    TurkeyObject *array = pointer;
    if (array_index(array, index, "write at")) array->slots[index] = value;
}
