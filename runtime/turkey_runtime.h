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

void *turkey_cell_new(uint64_t value, int32_t pointer_value);

void *turkey_object_new(int32_t kind, int32_t tag, int64_t count,
                        uint64_t pointer_bitmap);
void *turkey_box(uint64_t value, int32_t layout);
uint64_t turkey_unbox(void *box, int32_t layout);
void *turkey_array_new(int64_t length, uint64_t initial, int32_t element_width,
                       int32_t element_layout);
void *turkey_closure_new(uint64_t code, int64_t capture_count,
                         uint64_t pointer_bitmap);

/* Raw memory: `malloc` and `free`, and deliberately nothing more (TIX-61).
   Not heap objects -- these have no header, are never collected, and the
   collector must not be handed one. Temporary: TIX-62's FFI declares `malloc`
   and `free` directly and both of these go. */

void turkey_root_enter(void *frame, void *values, int64_t count,
                       const char *function_name);
void turkey_root_leave(void *frame);
/* The arm64 backend's roots: one table for the module, keyed by return
   address, registered by its entry sequence before anything allocates. Spelled
   `const void *` because the entry is generated code building the constant
   itself, matching the layout rather than the name -- the same reason
   `turkey_frame_enter` takes one. A program that never calls this (every
   LLVM-path binary) is unaffected. */
void turkey_frame_table_register(const void *table);
void turkey_collect(void);
int64_t turkey_heap_objects(void);
int64_t turkey_collection_count(void);
void turkey_gc_report(void);
void turkey_gc_set_stress(int32_t enabled);

/* What the host hands over: arguments in, exit status out.
   `turkey_args_set` is called by the host before the program runs and copies
   what it is given; the program reads the copies back one at a time through
   `turkey_arg_*`, which `Unsafe.Runtime` declares. `turkey_exit` unwinds
   through the panic flag, and `turkey_exiting` is what tells an exit from a
   panic at the boundary. */
void turkey_args_set(int64_t count, const unsigned char *const *bytes,
                     const int64_t *lengths);
int64_t turkey_arg_count(void);
const unsigned char *turkey_arg_bytes(int64_t index);
int64_t turkey_arg_length(int64_t index);
void turkey_exit(int64_t status);
int32_t turkey_exiting(void);
int64_t turkey_exit_status(void);
void turkey_exit_clear(void);

/* Print the Turkey call stack on a fault in generated code, then exit 139.
   Opt-in: the host installs it when `TURKEY_SEGV_FRAMES` is set, because a
   `SIGSEGV` handler is not this library's to take by default. */
void turkey_install_crash_handler(void);

/* The `main` of a compiled program: hands the arguments over, runs `entry`,
   and turns the panic and exit flags into an exit status. The code generator
   emits a `main` that is a call to this. */
int turkey_main(int argc, char **argv, void (*entry)(void));

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
