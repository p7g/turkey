#include "turkey_runtime.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct TurkeyObject {
    int32_t kind;
    int32_t tag;
    int64_t count;
    uint64_t pointer_bitmap;
    uint64_t slots[];
} TurkeyObject;

/* A `String` is its bytes: a kind-2 array object, `count` bytes long,
   the bytes where the slots begin. `lib/Data/String/Type.gob` declares it and
   both backends erase the newtype, so what C is handed is the array itself. */
static int64_t string_length(void *string) {
    return ((TurkeyObject *)string)->count;
}
static const unsigned char *string_bytes(void *string) {
    return (const unsigned char *)((TurkeyObject *)string)->slots;
}

static char panic_buffer[256];
/* Weak: a program the compiler emits defines the flag itself, as the first
   word of its panic state, and that definition is the one linked. This one
   is for a program emitted by a compiler that does not. */
__attribute__((weak)) int64_t turkey_has_panicked;

/* How many times a field or element read/written at one scalar layout found
   another one stored, and was silently boxed or unboxed to bridge the two.
   This is the differential oracle for making field access static: the stored
   layout is chosen by the *construction site* from its operand layouts, and
   the requested one is computed independently by the consumer, so a nonzero
   count is a producer/consumer disagreement that a plain load would compile
   wrongly. Emitting `getelementptr`+`load` in place of these calls is safe
   exactly when this stays zero. */

/* Where a frame currently is, as a constant the compiler emits once per site.
   Generated code changes its position by storing a pointer to one of these,
   which is a store rather than the four it used to take -- and this happens
   before every operation that can panic, so the four were on the hot path of
   every loop that could overflow or index out of bounds. */
typedef struct PanicSite {
    const char *function;
    const char *file;
    int64_t line;
    int64_t col;
} PanicSite;

typedef struct PanicCallFrame {
    struct PanicCallFrame *previous;
    const PanicSite *site;
} PanicCallFrame;

static PanicCallFrame *panic_calls;
static const PanicSite **panic_trace;
static int64_t panic_trace_count;

static void capture_panic_trace(void) {
    int64_t count = 0;
    for (PanicCallFrame *frame = panic_calls; frame != NULL;
         frame = frame->previous)
        if (frame->site != NULL && frame->site->line != 0) count++;
    panic_trace = malloc((size_t)count * sizeof(const PanicSite *));
    if (panic_trace == NULL) return;
    panic_trace_count = count;
    int64_t index = 0;
    for (PanicCallFrame *frame = panic_calls; frame != NULL;
         frame = frame->previous) {
        if (frame->site == NULL || frame->site->line == 0) continue;
        // The site is a constant in the compiled module, which outlives the
        // frame that pointed at it, so the snapshot can be of pointers.
        panic_trace[index] = frame->site;
        index++;
    }
}

void turkey_panic(const char *message) {
    if (!turkey_has_panicked) {
        snprintf(panic_buffer, sizeof(panic_buffer), "%s", message);
        turkey_has_panicked = 1;
        capture_panic_trace();
    }
}

void turkey_panic_string(void *message) {
    if (turkey_has_panicked) return;
    if (message == NULL) { turkey_panic("error"); return; }
    int64_t size = string_length(message);
    int length = size < (int64_t)sizeof(panic_buffer) - 1
        ? (int)size : (int)sizeof(panic_buffer) - 1;
    memcpy(panic_buffer, string_bytes(message), (size_t)length);
    panic_buffer[length] = '\0';
    turkey_has_panicked = 1;
    capture_panic_trace();
}

/* How the program ended, once `turkey_entry_returned` has taken the flag
   down. See there. */
static int32_t program_panicked;
static PanicCallFrame *entry_saved_calls;

int32_t turkey_panicked(void) {
    return turkey_has_panicked != 0 || program_panicked;
}
const char *turkey_panic_message(void) { return panic_buffer; }

/* The two ends of the program, as the `turkey_entry` every backend emits
   brackets it (TIX-67).

   The entry is Turkey now, and in Turkey the panic flag *is* unwinding: every
   call tests it on return and leaves if it is up. So when the program panics
   -- or exits, which unwinds the same way -- `Turkey.Entry`'s own code would
   unwind straight past the report it exists to print. The flag is parked in
   `program_panicked` instead, where `turkey_panicked` still sees it and no
   call site does.

   The panic frames are bracketed for the same reason in the other direction:
   the program's chain starts empty, so a trace names the program's frames and
   not the entry's, and ends where the entry left it, so a frame the unwound
   program never popped is not the entry's to trip over. */
void turkey_entry_started(void) {
    entry_saved_calls = panic_calls;
    panic_calls = NULL;
}

void turkey_entry_returned(void) {
    program_panicked = turkey_has_panicked != 0;
    turkey_has_panicked = 0;
    panic_calls = entry_saved_calls;
}

void turkey_panic_clear(void) {
    turkey_has_panicked = 0;
    program_panicked = 0;
    panic_buffer[0] = '\0';
    panic_calls = NULL;
    free(panic_trace);
    panic_trace = NULL;
    panic_trace_count = 0;
}

void turkey_frame_enter(void *pointer, const void *site) {
    PanicCallFrame *frame = pointer;
    if (frame == NULL) { turkey_panic("invalid panic frame"); return; }
    frame->previous = panic_calls;
    frame->site = site;
    panic_calls = frame;
}

void turkey_frame_leave(void *pointer) {
    PanicCallFrame *frame = pointer;
    if (frame == NULL) return;
    if (panic_calls != frame) { turkey_panic("unbalanced panic frame"); return; }
    panic_calls = frame->previous;
}

/* The innermost live panic frame, for the crash report in `Turkey.Entry`: the
   chain as it stands when the fault hit, not the snapshot a panic takes. */
const void *turkey_panic_calls_head(void) { return panic_calls; }

int64_t turkey_frame_count(void) {
    return panic_trace_count;
}

static const PanicSite *panic_frame_at(int64_t index) {
    return index < 0 || index >= panic_trace_count ? NULL : panic_trace[index];
}

const char *turkey_frame_function(int64_t index) {
    const PanicSite *frame = panic_frame_at(index);
    return frame == NULL ? NULL : frame->function;
}
const char *turkey_frame_file(int64_t index) {
    const PanicSite *frame = panic_frame_at(index);
    return frame == NULL ? NULL : frame->file;
}
int64_t turkey_frame_line(int64_t index) {
    const PanicSite *frame = panic_frame_at(index);
    return frame == NULL ? 0 : frame->line;
}
int64_t turkey_frame_col(int64_t index) {
    const PanicSite *frame = panic_frame_at(index);
    return frame == NULL ? 0 : frame->col;
}

/* The panic flag alone, which `turkey_panicked` is not: the collector stops
   before sweeping a heap it did not finish tracing. */
int32_t turkey_panic_pending(void) { return turkey_has_panicked != 0; }

/* --------------------------------------------------- what the host hands over
 *
 * The arguments going in and the exit status coming out. Everything else the
 * outside world was -- the streams and the two file doors -- is Turkey now,
 * over `open`, `read`, `write` and `close` (`lib/System/IO.gob`); the
 * arguments are built into strings in Turkey too (`System.Env.args`).
 *
 * `Turkey.Entry` sets the arguments before running the program and reads the
 * exit flag afterwards. `Unsafe.Runtime` exposes this state to Turkey code.
 */

static unsigned char **argument_bytes;
static int64_t *argument_lengths;
static int64_t argument_count;

void turkey_args_set(int64_t count, const unsigned char *const *bytes,
                     const int64_t *lengths) {
    /* Copied out of the host's memory and held outside the Turkey heap. A
       `String` per argument would have to stay reachable for the life
       of the program from a root the collector scans, and there is no such
       root; plain bytes need none, and `System.Env.args` builds the strings
       on demand. */
    for (int64_t index = 0; index < argument_count; ++index)
        free(argument_bytes[index]);
    free(argument_bytes);
    free(argument_lengths);
    argument_bytes = NULL;
    argument_lengths = NULL;
    argument_count = 0;
    if (count <= 0) return;
    argument_bytes = calloc((size_t)count, sizeof(unsigned char *));
    argument_lengths = calloc((size_t)count, sizeof(int64_t));
    if (argument_bytes == NULL || argument_lengths == NULL) {
        free(argument_bytes);
        free(argument_lengths);
        argument_bytes = NULL;
        argument_lengths = NULL;
        turkey_panic("out of memory recording arguments");
        return;
    }
    for (int64_t index = 0; index < count; ++index) {
        int64_t length = lengths[index];
        unsigned char *copy = malloc((size_t)length + 1);
        if (copy == NULL) {
            argument_count = index;
            turkey_panic("out of memory recording arguments");
            return;
        }
        memcpy(copy, bytes[index], (size_t)length);
        copy[length] = '\0';
        argument_bytes[index] = copy;
        argument_lengths[index] = length;
    }
    argument_count = count;
}

/* One argument at a time, for `Unsafe.Runtime`: a count, and each one's bytes
   and length. Raw bytes rather than strings, so that nothing here allocates
   and the collector never has to know these exist. An index out of range is
   undefined, as any raw read is; `System.Env.args` asks only below the count. */
int64_t turkey_arg_count(void) { return argument_count; }

const unsigned char *turkey_arg_bytes(int64_t index) {
    return argument_bytes[index];
}

int64_t turkey_arg_length(int64_t index) { return argument_lengths[index]; }

static int32_t exit_requested;
static int64_t exit_status;

void turkey_exit(int64_t status) {
    /* Unwound the way a panic is, and for the same reason: generated code
       already tests one flag after every call that can fail, so a second
       mechanism would be a second thing to get right at every one of those
       sites. `turkey_exiting` tells the two apart at the boundary -- an exit
       carries a status and no message. */
    exit_requested = 1;
    exit_status = status;
    fflush(stdout);
    fflush(stderr);
    turkey_has_panicked = 1;
}

int32_t turkey_exiting(void) { return exit_requested; }

int64_t turkey_exit_status(void) { return exit_status; }

void turkey_exit_clear(void) { exit_requested = 0; exit_status = 0; }
