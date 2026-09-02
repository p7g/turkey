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
uint8_t turkey_print(TurkeyString *value);
uint8_t turkey_write(TurkeyString *value);

void *turkey_cell_new(uint64_t value);
uint64_t turkey_cell_load(void *cell);
void turkey_cell_store(void *cell, uint64_t value);

void turkey_panic(const char *message);
int32_t turkey_panicked(void);
const char *turkey_panic_message(void);
void turkey_panic_clear(void);

#endif
