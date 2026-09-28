#ifndef TURKEY_RUNTIME_H
#define TURKEY_RUNTIME_H

#include <stdint.h>

extern int32_t turkey_has_panicked;

/* A `String` is a byte array on the heap, so this is `void *`:
   `turkey_string_new` is how the entry interns a literal. */
void *turkey_string_new(const unsigned char *bytes, int64_t length);

/* C-callable exports supplied by Turkey.Alloc in the generated program.
   Scalar arguments use Turkey's 64-bit Int; stored header fields stay 32-bit. */
void *turkey_cell_new(uint64_t value, int64_t pointer_value);
void *turkey_object_new(int64_t kind, int64_t tag, int64_t count,
                        uint64_t pointer_bitmap);
void *turkey_box(uint64_t value, int64_t layout);
uint64_t turkey_unbox(void *box, int64_t layout);
void *turkey_array_new(int64_t length, uint64_t initial, int64_t element_width,
                       int64_t element_layout);
void *turkey_closure_shell(uint64_t code);

/* The program's early initialization -- every giblet module's globals -- under
   a C name. `turkey_entry` runs it first; C that calls a program's Turkey
   exports without running the program calls it before anything else. */
void turkey_giblets_initialize(void);

/* The collector, exported by Turkey.Heap in the generated program.
   `turkey_root_enter`/`leave` push and pop a shadow-stack frame the caller
   owns. The arm64 backend's roots are instead one table for the module, keyed
   by return address, registered by its entry sequence before anything
   allocates; the table is `const void *` because the entry is generated code
   building the constant itself, matching the layout rather than the name. */
void turkey_root_enter(void *frame, void *values, int64_t count,
                       const char *function_name);
void turkey_root_leave(void *frame);
void turkey_frame_table_register(const void *table);
void turkey_entry_stack_set(void *frame);
const void *turkey_roots_head(void);
void turkey_collect(void);
void turkey_gc_report(void);
int64_t turkey_heap_objects(void);
int64_t turkey_collection_count(void);
void turkey_gc_set_stress(int32_t enabled);
void turkey_gc_set_verify(int32_t enabled);
/* Whether `value` is a live heap object, and three settings a test uses to
   corrupt the heap on purpose. The verifier's accessors are declared beside
   it, in the C. */
int64_t turkey_heap_contains(void *value);
void turkey_heap_set_mark_epoch(int64_t epoch);
void turkey_heap_set_objects(int64_t count);
void turkey_heap_set_region_bytes(int64_t bytes);

/* The heap verifier, which stays C so that it is not the collector checking
   itself: the collector calls it at each phase when TURKEY_GC_VERIFY is set,
   and it exits the process on a failure. */
void turkey_heap_verify(int64_t phase, void *frame);
int32_t turkey_panic_pending(void);

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
   panic machinery's state. The entry itself -- `turkey_main`, the big-stack
   thread and the crash handler -- is Turkey, and each program defines those
   symbols. The generated `turkey_entry` calls the first two around the
   program; see the C. */
void turkey_entry_started(void);
void turkey_entry_returned(void);
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
