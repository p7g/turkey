#ifndef TURKEY_RUNTIME_H
#define TURKEY_RUNTIME_H

#include <stdint.h>

extern int32_t turkey_has_panicked;

/* A `String` is a byte array on the heap (TIX-66), so these are `void *`:
   `turkey_string_new` is how the entry interns a literal, and the float
   three stay C until TIX-75. */
void *turkey_string_new(const unsigned char *bytes, int64_t length);
void *turkey_float_to_string(double value);
double turkey_float_parse(void *value);
int32_t turkey_float_can_parse(void *value);

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

/* What the entry and the crash report in `lib/Turkey/Entry.gob` read of the
   collector's and the panic machinery's state (TIX-67). The entry itself --
   `turkey_main`, the big-stack thread and the crash handler -- is Turkey, and
   each program defines those symbols; these stay C because the state they
   reach is the collector's until it moves too. */
void turkey_entry_stack_set(void *frame);
/* Called by the generated `turkey_entry` around the program; see the C. */
void turkey_entry_started(void);
void turkey_entry_returned(void);
const void *turkey_roots_head(void);
const void *turkey_panic_calls_head(void);

void turkey_panic(const char *message);
void turkey_panic_string(void *message);
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
