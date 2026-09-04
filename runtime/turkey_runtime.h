#ifndef TURKEY_RUNTIME_H
#define TURKEY_RUNTIME_H

#include <stdint.h>

extern int32_t turkey_has_panicked;

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

void *turkey_object_new(int32_t kind, int32_t tag, int64_t count,
                        uint64_t pointer_bitmap);
void turkey_object_set(void *object, int64_t index, uint64_t value);
void *turkey_box(uint64_t value, int32_t layout);
uint64_t turkey_unbox(void *box, int32_t layout);
void *turkey_array_new(int64_t length, uint64_t initial, int32_t element_width,
                       int32_t element_layout);
void *turkey_closure_new(uint64_t code, int64_t capture_count,
                         uint64_t pointer_bitmap);
void turkey_closure_capture(void *closure, int64_t index, uint64_t value);

void turkey_root_enter(void *frame, void *values, int64_t count,
                       const char *function_name);
void turkey_root_leave(void *frame);
void turkey_collect(void);
int64_t turkey_heap_objects(void);
int64_t turkey_collection_count(void);
void turkey_gc_set_stress(int32_t enabled);

/* The outside world: arguments, two file doors, the error stream and `exit`.
   `turkey_args_set` is called by the host before the program runs and copies
   what it is given; `turkey_args_storage` builds the `TurkeyString`s on
   demand. `turkey_exit` unwinds through the panic flag, and `turkey_exiting`
   is what tells an exit from a panic at the boundary. */
void turkey_args_set(int64_t count, const unsigned char *const *bytes,
                     const int64_t *lengths);
void *turkey_args_storage(void);
int32_t turkey_file_can_read(TurkeyString *path);
void *turkey_read_file_bytes(TurkeyString *path);
int32_t turkey_write_file_bytes(TurkeyString *path, void *array);
uint8_t turkey_stderr_write(TurkeyString *value);
void turkey_exit(int64_t status);
int32_t turkey_exiting(void);
int64_t turkey_exit_status(void);
void turkey_exit_clear(void);

/* Print the Turkey call stack on a fault in generated code, then exit 139.
   Opt-in: the host installs it when `TURKEY_SEGV_FRAMES` is set, because a
   `SIGSEGV` handler is not this library's to take by default. */
void turkey_install_crash_handler(void);

void turkey_panic(const char *message);
void turkey_panic_string(TurkeyString *message);
int32_t turkey_panicked(void);
const char *turkey_panic_message(void);
void turkey_panic_clear(void);
/* `site` is a `const PanicSite *`: {function, file, line, col}, emitted once
   per source position by the code generator. Spelled `const void *` here
   because the struct is private to the runtime and generated code builds the
   constant itself, matching the layout rather than the name. */
void turkey_frame_enter(void *frame, const void *site);
void turkey_frame_leave(void *frame);
int64_t turkey_frame_count(void);
const char *turkey_frame_function(int64_t index);
const char *turkey_frame_file(int64_t index);
int64_t turkey_frame_line(int64_t index);
int64_t turkey_frame_col(int64_t index);

#endif
