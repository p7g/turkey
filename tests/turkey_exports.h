#ifndef TURKEY_EXPORTS_H
#define TURKEY_EXPORTS_H

/* What a compiled program exports to C, for the C probes the tests link
   against one: each symbol here is a `foreign` definition in `lib/Turkey/`,
   or storage the compiler emits, and none of it is checked against those by
   anything but the probes that call it. Turkey's `Int` is `int64_t`; a
   `Prim.Ptr` is a pointer. */

#include <stdint.h>

/* The first word of the process state every program defines: nonzero while
   a panic or an exit unwinds. */
extern int64_t turkey_has_panicked;

/* The program's early initialization -- every giblet module's globals. C that
   calls a program's Turkey exports without running the program calls it
   before anything else. */
void turkey_giblets_initialize(void);

/* Turkey.Process. */
void turkey_panic(const char *message);
const char *turkey_panic_message(void);
void turkey_panic_clear(void);

/* Turkey.Alloc. A `String` is a byte array, so it is `void *`. */
void *turkey_string_new(const unsigned char *bytes, int64_t length);
void *turkey_cell_new(uint64_t value, int64_t pointer_value);
void *turkey_object_new(int64_t kind, int64_t tag, int64_t count,
                        uint64_t pointer_bitmap);
void *turkey_array_new(int64_t length, uint64_t initial, int64_t element_width,
                       int64_t element_layout);
void *turkey_closure_shell(uint64_t code);

/* Turkey.Heap. `turkey_root_enter`/`leave` push and pop a shadow-stack frame
   the caller owns. */
void turkey_root_enter(void *frame, void *values, int64_t count,
                       const char *function_name);
void turkey_root_leave(void *frame);
void turkey_frame_table_register(const void *table);
void turkey_entry_stack_set(void *frame);
const void *turkey_roots_head(void);
void turkey_collect(void);
/* A minor collection if they are on; `turkey_collect` is always full. */
void turkey_collect_minor(void);
void turkey_gc_report(void);
int64_t turkey_heap_objects(void);
int64_t turkey_collection_count(void);
void turkey_gc_set_stress(int64_t enabled);
void turkey_gc_set_verify(int64_t enabled);
void turkey_gc_set_generational(int64_t enabled);
/* The write barrier's slow path: remembers `parent` if it is old. C that
   stores a pointer into an object allocated before the pointer's object
   calls it after the store. */
void turkey_write_barrier(void *parent);
int64_t turkey_heap_contains(void *value);
void *turkey_heap_regions(void);
void *turkey_heap_available(void);
void *turkey_heap_region_table(void);
int64_t turkey_heap_region_table_capacity(void);
int64_t turkey_heap_region_bytes(void);
int64_t turkey_heap_mark_epoch(void);
void turkey_heap_set_mark_epoch(int64_t epoch);
void turkey_heap_set_objects(int64_t count);
void turkey_heap_set_region_bytes(int64_t bytes);
void *turkey_heap_state(void);

/* Turkey.HeapCheck: 1 if the heap passed, 0 with the diagnostic in
   `turkey_heap_check_message`. */
int64_t turkey_heap_check(int64_t phase, void *frame, void *state);
const char *turkey_heap_check_message(void);
int64_t turkey_heap_check_frames(int64_t phase, uintptr_t current, void *state);

#endif
