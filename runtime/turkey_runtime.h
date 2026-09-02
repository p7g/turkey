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
double turkey_float_parse(TurkeyString *value);
int32_t turkey_float_can_parse(TurkeyString *value);
double turkey_float_fmod(double left, double right);
double turkey_float_remainder(double left, double right);
double turkey_float_floor(double value);
double turkey_float_ceil(double value);
double turkey_float_round(double value);
double turkey_float_trunc(double value);
TurkeyString *turkey_char_to_string(uint32_t value);
int64_t turkey_string_byte_length(TurkeyString *value);
uint8_t turkey_string_byte_at(TurkeyString *value, int64_t index);
uint32_t turkey_string_decode_at(TurkeyString *value, int64_t index);
int64_t turkey_string_next_index(TurkeyString *value, int64_t index);
TurkeyString *turkey_string_slice(TurkeyString *value, int64_t start, int64_t stop);
int64_t turkey_string_find(TurkeyString *haystack, TurkeyString *needle, int64_t start);
int64_t turkey_string_rfind(TurkeyString *haystack, TurkeyString *needle);
void *turkey_string_to_byte_storage(TurkeyString *value);
TurkeyString *turkey_string_from_bytes(void *array);
int32_t turkey_string_is_valid_utf8(void *array);
TurkeyString *turkey_string_concat_all(void *array);
int32_t turkey_string_eq(TurkeyString *left, TurkeyString *right);
int32_t turkey_string_lt(TurkeyString *left, TurkeyString *right);
uint8_t turkey_print(TurkeyString *value);
uint8_t turkey_write(TurkeyString *value);

void *turkey_cell_new(uint64_t value, int32_t pointer_value);
uint64_t turkey_cell_load(void *cell);
void turkey_cell_store(void *cell, uint64_t value);

void *turkey_object_new(int32_t kind, int32_t tag, int64_t count,
                        uint64_t pointer_bitmap);
int32_t turkey_object_tag(void *object);
uint64_t turkey_object_get(void *object, int64_t index);
void turkey_object_set(void *object, int64_t index, uint64_t value);
uint64_t turkey_object_get_as(void *object, int64_t index, int32_t layout);
void turkey_object_set_as(void *object, int64_t index, uint64_t value,
                          int32_t layout);
void *turkey_box(uint64_t value, int32_t layout);
uint64_t turkey_unbox(void *box, int32_t layout);
void *turkey_array_new(int64_t length, uint64_t initial, int32_t element_width,
                       int32_t element_layout);
int64_t turkey_array_length(void *array);
uint64_t turkey_array_get(void *array, int64_t index);
void turkey_array_set(void *array, int64_t index, uint64_t value);
uint64_t turkey_array_get_as(void *array, int64_t index, int32_t layout);
void turkey_array_set_as(void *array, int64_t index, uint64_t value,
                         int32_t layout);
void *turkey_array_get_boxed(void *array, int64_t index);
void turkey_array_set_boxed(void *array, int64_t index, void *value);
void *turkey_closure_new(uint64_t code, int64_t capture_count,
                         uint64_t pointer_bitmap);
uint64_t turkey_closure_code(void *closure);
void *turkey_closure_environment(void *closure);
void turkey_closure_capture(void *closure, int64_t index, uint64_t value);

void *turkey_root_push(int64_t count, const char *function_name);
void turkey_root_set(void *frame, int64_t index, void *value);
void turkey_root_pop(void *frame);
void turkey_collect(void);
int64_t turkey_heap_objects(void);
void turkey_gc_set_stress(int32_t enabled);

void turkey_panic(const char *message);
int32_t turkey_panicked(void);
const char *turkey_panic_message(void);
void turkey_panic_clear(void);

#endif
