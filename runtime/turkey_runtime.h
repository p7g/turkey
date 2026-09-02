#ifndef TURKEY_RUNTIME_H
#define TURKEY_RUNTIME_H

#include <stdint.h>

typedef struct TurkeyString {
    int64_t length;
    unsigned char bytes[];
} TurkeyString;

TurkeyString *turkey_string_new(const unsigned char *bytes, int64_t length);
TurkeyString *turkey_string_concat(TurkeyString *left, TurkeyString *right);
TurkeyString *turkey_int_to_string(int64_t value);
TurkeyString *turkey_float_to_string(double value);
TurkeyString *turkey_char_to_string(uint32_t value);
int64_t turkey_string_byte_length(TurkeyString *value);
int32_t turkey_string_eq(TurkeyString *left, TurkeyString *right);
int32_t turkey_string_lt(TurkeyString *left, TurkeyString *right);
uint8_t turkey_print(TurkeyString *value);
uint8_t turkey_write(TurkeyString *value);

void *turkey_cell_new(uint64_t value);
uint64_t turkey_cell_load(void *cell);
void turkey_cell_store(void *cell, uint64_t value);

void *turkey_object_new(int32_t kind, int32_t tag, int64_t count,
                        uint64_t pointer_bitmap);
int32_t turkey_object_tag(void *object);
uint64_t turkey_object_get(void *object, int64_t index);
void turkey_object_set(void *object, int64_t index, uint64_t value);
void *turkey_array_new(int64_t length, uint64_t initial, int32_t pointer_elements);
int64_t turkey_array_length(void *array);
uint64_t turkey_array_get(void *array, int64_t index);
void turkey_array_set(void *array, int64_t index, uint64_t value);

void turkey_panic(const char *message);
int32_t turkey_panicked(void);
const char *turkey_panic_message(void);
void turkey_panic_clear(void);

#endif
