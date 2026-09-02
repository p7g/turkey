#include "turkey_runtime.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct TurkeyCell { uint64_t value; } TurkeyCell;

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
