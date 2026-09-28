#include "turkey_runtime.h"

#include <inttypes.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct TurkeyCell { uint64_t value; int32_t pointer_value; } TurkeyCell;
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
int32_t turkey_has_panicked;

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

typedef struct HeapHeader {
    struct HeapHeader *next;
    size_t size;
    uint32_t kind;
    uint32_t marked;
} HeapHeader;

typedef struct RootFrame {
    struct RootFrame *previous;
    const char *function_name;
    int64_t count;
    void **values;
    // Which of `values` actually holds a live pointer right now. A root set is
    // a property of a program point, not of a function: `Main#inc` in the
    // brainfuck benchmark has five roots and two safepoints, and both
    // safepoints are the cold out-of-bounds calls, so unioning the live sets
    // over the function made every in-range access pay for paths that never
    // run. The compiler knows the live set at each safepoint exactly, so it
    // stores it here -- one immediate -- before each call that may collect.
    //
    // Bit `i` covers `values[i]`; slots from 64 up are always scanned, which
    // is what keeps the two producers that are not compiled code total. The
    // globals frame has arbitrarily many slots and all of them are live for
    // the whole run, and a function needing more than 64 roots falls back to
    // an all-ones mask with the array zeroed on entry, as it used to be.
    int64_t live;
} RootFrame;

enum { HEAP_OBJECT = 2, HEAP_CELL = 3 };

/* The collector is `lib/Turkey/Heap.gob`. What follows is the heap verifier,
   which checks it by decoding the collector's state with its own copy of the
   layouts -- these structs, which must match that module's offsets -- and by
   its own tracing, so that a collector bug is not checked by the same bug. It
   reaches the state through the module's `turkey_heap_*` accessors.

   A header remains immediately before each payload. Small objects occupy
   fixed-size slots in aligned regions; the address of any header identifies
   its region without a side table. Large objects own a dedicated region. */
enum { REGION_BYTES = 65536, REGION_WORDS = 32, REGION_CLASSES = 22 };
typedef struct HeapRegion {
    struct HeapRegion *next;
    struct HeapRegion *available_next;
    size_t slot_size;
    size_t reserved;
    uint32_t capacity, used, live, search_word, size_class;
    uint64_t allocated[REGION_WORDS];
    uint64_t marked_slots[REGION_WORDS];
    unsigned char data[];
} HeapRegion;
_Static_assert(sizeof(HeapHeader) == 24, "Turkey.Heap's headerBytes");
_Static_assert(offsetof(HeapRegion, marked_slots) == 312, "Turkey.Heap's regionMarked");
_Static_assert(offsetof(HeapRegion, data) == 568, "Turkey.Heap's regionData");

typedef struct FrameEntry {
    uintptr_t retaddr;
    int64_t count;
    const int64_t *offsets; /* from x29, signed */
} FrameEntry;

/* The collector's side of the contract, exported by `Turkey.Heap`. */
HeapRegion *turkey_heap_regions(void);
HeapRegion **turkey_heap_available(void);
HeapRegion **turkey_heap_region_table(void);
int64_t turkey_heap_region_table_capacity(void);
int64_t turkey_heap_region_table_count(void);
int64_t turkey_heap_region_bytes(void);
int64_t turkey_heap_mark_epoch(void);
FrameEntry *turkey_heap_frame_entries(void);
int64_t turkey_heap_frame_entry_count(void);
void *turkey_heap_entry_stack_high(void);

static HeapRegion *region_of(HeapHeader *header) {
    return (HeapRegion *)((uintptr_t)header & ~((uintptr_t)REGION_BYTES - 1));
}

static HeapHeader *header_of(void *value) {
    return value == NULL ? NULL : ((HeapHeader *)value) - 1;
}

/* The region set's hash, recomputed to check that every entry is reachable
   by probing from its home slot. `Turkey.Heap.regionHash` is the other copy. */
static size_t region_hash(const HeapRegion *region) {
    uint64_t h = ((uintptr_t)region / REGION_BYTES) * UINT64_C(0x9E3779B97F4A7C15);
    return (size_t)(h ^ (h >> 32))
        & ((size_t)turkey_heap_region_table_capacity() - 1);
}

/* Independent verification uses root inclusion and closure of the marked set:
   if roots are marked and every edge out of a marked object is marked, every
   reachable object is marked. It never mutates marks or calls the collector's
   traversal/lookup helpers. Compiler-emitted layouts and roots are still
   trusted descriptions: a root omitted by the compiler cannot be reconstructed.
   The region list and stack chain must be readable; this diagnoses runtime
   invariant violations, not arbitrary corruption of native address space. */
typedef struct HeapCheck {
    HeapRegion **regions;
    size_t count;
    int phase; /* 0 before marking, 1 before sweeping, 2 after sweeping */
    /* The collection's Turkey frame record, where the native walk starts, as
       the collector's does; null when no Turkey frame is live. */
    void **frame;
    /* The collector's state, read once through its accessors. */
    uint32_t epoch;
    const FrameEntry *entries;
    int64_t entry_count;
    uintptr_t high;
} HeapCheck;

static int heap_check_fail(const char *reason, const void *owner, int64_t index) {
    char message[240];
    snprintf(message, sizeof(message), "heap verifier: %s (at %p, slot %" PRId64 ")",
             reason, owner, index);
    turkey_panic(message);
    return 0;
}

static int heap_check_order(const void *a, const void *b) {
    uintptr_t x = (uintptr_t)*(HeapRegion *const *)a;
    uintptr_t y = (uintptr_t)*(HeapRegion *const *)b;
    return (x > y) - (x < y);
}

static HeapRegion *heap_check_region(const HeapCheck *check, uintptr_t address) {
    size_t low = 0, high = check->count;
    while (low < high) {
        size_t mid = low + (high - low) / 2;
        if ((uintptr_t)check->regions[mid] <= address) low = mid + 1;
        else high = mid;
    }
    return low == 0 ? NULL : check->regions[low - 1];
}

static int heap_check_pointer(const HeapCheck *check, void *value, int marked,
                              const void *owner, int64_t index) {
    if (value == NULL) return 1;
    uintptr_t address = (uintptr_t)value;
    HeapRegion *r = heap_check_region(check, address);
    if (r == NULL || address < (uintptr_t)r->data + sizeof(HeapHeader))
        return heap_check_fail("invalid traced pointer", owner, index);
    size_t offset = address - (uintptr_t)r->data - sizeof(HeapHeader);
    size_t slot = offset / r->slot_size;
    if (offset % r->slot_size || slot >= r->capacity ||
        !(r->allocated[slot / 64] & (UINT64_C(1) << (slot % 64))))
        return heap_check_fail("invalid traced pointer", owner, index);
    HeapHeader *h = (HeapHeader *)(r->data + slot * r->slot_size);
    if (marked && h->marked != check->epoch)
        return heap_check_fail("reachable object is unmarked", owner, index);
    return 1;
}

static int heap_check_object(const HeapCheck *check, HeapHeader *h) {
    void *value = h + 1;
    int marked = check->phase != 0 && h->marked == check->epoch;
    if (h->kind == HEAP_CELL) {
        if (h->size != sizeof(TurkeyCell))
            return heap_check_fail("invalid cell size", value, -1);
        TurkeyCell *cell = value;
        /* Any nonzero flag denotes a pointer, as in the allocator ABI. */
        return !cell->pointer_value || heap_check_pointer(check,
            (void *)(uintptr_t)cell->value, marked, value, 0);
    }
    if (h->kind != HEAP_OBJECT || h->size < sizeof(TurkeyObject))
        return heap_check_fail("invalid heap header", value, -1);
    TurkeyObject *o = value;
    if (o->kind < 0 || o->kind > 5 || o->count < 0)
        return heap_check_fail("invalid object header", value, -1);
    size_t width = 8;
    if (o->kind == 2) {
        width = o->pointer_bitmap;
        if ((width != 1 && width != 4 && width != 8) ||
            o->tag < 0 || o->tag > 7 || (o->tag == 7 && width != 8))
            return heap_check_fail("invalid array layout", value, -1);
    } else if (o->count > ((o->kind <= 1) ? 21 : 63)) {
        return heap_check_fail("invalid object count", value, -1);
    }
    if ((uint64_t)o->count > (h->size - sizeof(*o)) / width ||
        sizeof(*o) + (size_t)o->count * width != h->size)
        return heap_check_fail("payload size disagrees with header", value, -1);
    if (o->kind == 2 && o->tag != 7) return 1;
    for (int64_t i = 0; i < o->count; i++) {
        int traced = o->kind == 2 ? o->tag == 7 : o->kind <= 1
            ? ((o->pointer_bitmap >> (i * 3)) & 7) == 7
            : ((o->pointer_bitmap >> i) & 1) != 0;
        if (traced && !heap_check_pointer(check, (void *)(uintptr_t)o->slots[i],
                                          marked, value, i)) return 0;
    }
    return 1;
}

static int heap_check_native(const HeapCheck *check, uintptr_t current, uintptr_t high) {
    const FrameEntry *frame_entries = check->entries;
    int64_t frame_entry_count = check->entry_count;
    if (frame_entry_count == 0) return 1;
    if (frame_entry_count < 0 || frame_entries == NULL)
        return heap_check_fail("invalid native frame table", frame_entries, -1);
    for (int64_t i = 0; i < frame_entry_count; i++) {
        if (frame_entries[i].count < 0 ||
            (frame_entries[i].count && frame_entries[i].offsets == NULL) ||
            (i && frame_entries[i-1].retaddr >= frame_entries[i].retaddr))
            return heap_check_fail("invalid native frame table", frame_entries, i);
    }
    /* From the same record, and within the same bounds, as the collector's walk:
       only Turkey's frame records are walked, and C frames between here and
       the allocator need not keep one. Records are 8-byte aligned, not 16. */
    if (current == 0) return 1;
    if (high == 0) return heap_check_fail("missing native stack bound", NULL, -1);
    while (current <= high && high - current >= 16 && !(current & 7)) {
        const uintptr_t *record = (const uintptr_t *)current;
        uintptr_t caller = record[0];
        if (caller <= current || caller > high || high - caller < 16 || (caller & 7)) break;
        size_t low = 0, end = (size_t)frame_entry_count;
        while (low < end) {
            size_t mid = low + (end - low) / 2;
            if (frame_entries[mid].retaddr < record[1]) low = mid + 1;
            else end = mid;
        }
        if (low < (size_t)frame_entry_count && frame_entries[low].retaddr == record[1]) {
            const FrameEntry *entry = &frame_entries[low];
            for (int64_t i = 0; i < entry->count; i++) {
                int64_t offset = entry->offsets[i];
                /* Unsigned addition avoids overflow on malformed offsets;
                   the range check rejects wraparound before dereferencing. */
                uintptr_t slot = caller + (uint64_t)offset;
                if ((slot & 7) || slot < current + 16 || slot > high - 8)
                    return heap_check_fail("invalid native root offset", entry, i);
                if (!heap_check_pointer(check, *(void **)slot, check->phase != 0,
                                        (void *)caller, i)) return 0;
            }
        }
        current = caller;
    }
    return 1;
}

static int heap_check_roots(const HeapCheck *check) {
    RootFrame *roots = (RootFrame *)turkey_roots_head();
    RootFrame *slow = roots, *fast = roots;
    while (fast != NULL && fast->previous != NULL) {
        slow = slow->previous;
        fast = fast->previous->previous;
        if (slow == fast) return heap_check_fail("cyclic root chain", slow, -1);
    }
    for (RootFrame *f = roots; f != NULL; f = f->previous) {
        if (f->count < 0 || f->values == NULL)
            return heap_check_fail("invalid root frame", f, -1);
        for (int64_t i = 0; i < f->count; i++)
            if ((i >= 64 || (((uint64_t)f->live >> i) & 1)) &&
                !heap_check_pointer(check, f->values[i], check->phase != 0, f, i))
                return 0;
    }
    return heap_check_native(check, (uintptr_t)check->frame, check->high);
}

static int heap_check_contents(HeapCheck *check) {
    HeapRegion **region_table = turkey_heap_region_table();
    size_t region_table_capacity = (size_t)turkey_heap_region_table_capacity();
    size_t region_table_count = (size_t)turkey_heap_region_table_count();
    int64_t heap_count = turkey_heap_objects();
    HeapRegion **available = turkey_heap_available();
    size_t bytes = 0;
    uint64_t objects = 0;
    /* Validate every region before using it for membership queries. */
    for (size_t n = 0; n < check->count; n++) {
        HeapRegion *r = check->regions[n];
        if (((uintptr_t)r % REGION_BYTES) || r->reserved < REGION_BYTES ||
            r->reserved % REGION_BYTES || r->slot_size < sizeof(HeapHeader) ||
            !r->capacity || r->capacity > REGION_WORDS * 64 ||
            r->capacity > (r->reserved - sizeof(*r)) / r->slot_size ||
            r->used > r->capacity || r->size_class > REGION_CLASSES ||
            r->search_word >= REGION_WORDS)
            return heap_check_fail("invalid region geometry", r, -1);
        size_t class_slot = r->size_class < 15 ? 32 + 16 * r->size_class
            : (size_t)256 << (r->size_class - 14);
        if (r->size_class < REGION_CLASSES
                ? (r->reserved != REGION_BYTES || r->slot_size != class_slot ||
                   r->capacity != (REGION_BYTES - sizeof(*r)) / class_slot)
                : r->capacity != 1)
            return heap_check_fail("invalid region size class", r, -1);
        for (unsigned word = 0; word < r->search_word; word++)
            if (r->allocated[word] != UINT64_MAX)
                return heap_check_fail("allocation search skips free slots", r, word);
        if (n && (uintptr_t)r - (uintptr_t)check->regions[n-1] < check->regions[n-1]->reserved)
            return heap_check_fail("overlapping regions", r, -1);
        if (bytes > SIZE_MAX - r->reserved)
            return heap_check_fail("region byte count overflow", r, -1);
        bytes += r->reserved;
        uint32_t used = 0, live = 0;
        for (unsigned i = 0; i < REGION_WORDS * 64; i++) {
            uint64_t bit = UINT64_C(1) << (i % 64);
            int allocated = (r->allocated[i / 64] & bit) != 0;
            int marked = (r->marked_slots[i / 64] & bit) != 0;
            if ((i >= r->capacity && allocated) || (marked && !allocated))
                return heap_check_fail("marked/free slot or bitmap outside region", r, i);
            if (check->phase != 1 && marked)
                return heap_check_fail("uncleared region marks", r, i);
            used += allocated;
            live += marked;
            if (!allocated) continue;
            HeapHeader *h = (HeapHeader *)(r->data + i * r->slot_size);
            if (h->size > r->slot_size - sizeof(*h))
                return heap_check_fail("object exceeds allocation slot", h, i);
            if (check->phase == 1 && marked != (h->marked == check->epoch))
                return heap_check_fail("header and region marks disagree", h, i);
            if (check->phase == 2 && h->marked != check->epoch)
                return heap_check_fail("unmarked sweep survivor", h, i);
        }
        if (used != r->used || live != r->live)
            return heap_check_fail("region counts disagree", r, -1);
        objects += used;
    }
    if (heap_count < 0 || objects != (uint64_t)heap_count || bytes != (size_t)turkey_heap_region_bytes())
        return heap_check_fail("heap totals disagree", NULL, -1);
    /* The region set that the collector's pointer test consults holds exactly
       the listed regions, each reachable by probing from its home slot: every
       slot between the home and the entry is occupied. */
    if (region_table_count != check->count ||
            (region_table_capacity & (region_table_capacity - 1)) ||
            (check->count && region_table_count * 2 > region_table_capacity))
        return heap_check_fail("region table disagrees with region list", region_table, -1);
    size_t entries = 0;
    for (size_t at = 0; at < region_table_capacity; at++) {
        HeapRegion *r = region_table[at];
        if (r == NULL) continue;
        entries++;
        if (heap_check_region(check, (uintptr_t)r) != r)
            return heap_check_fail("region table holds an unlisted region", r, (int64_t)at);
        for (size_t probe = region_hash(r); probe != at;
                probe = (probe + 1) & (region_table_capacity - 1))
            if (region_table[probe] == NULL)
                return heap_check_fail("region table entry unreachable by probing",
                                       r, (int64_t)at);
    }
    if (entries != check->count)
        return heap_check_fail("region table disagrees with region list", region_table, -1);
    /* Availability is a list of regions with holes, not a list of objects.
       A full region's stale available_next is deliberately ignored. */
    size_t expected[REGION_CLASSES] = {0}, actual[REGION_CLASSES] = {0};
    for (size_t n = 0; n < check->count; n++) {
        HeapRegion *r = check->regions[n];
        if (r->size_class < REGION_CLASSES && r->used < r->capacity)
            expected[r->size_class]++;
        for (unsigned i = 0; i < r->capacity; i++)
            if ((r->allocated[i / 64] >> (i % 64)) & 1)
                if (!heap_check_object(check, (HeapHeader *)(r->data + i * r->slot_size))) return 0;
    }
    for (unsigned cls = 0; cls < REGION_CLASSES; cls++) {
        for (HeapRegion *r = available[cls]; r != NULL; r = r->available_next) {
            if (++actual[cls] > check->count ||
                heap_check_region(check, (uintptr_t)r) != r ||
                r->size_class != cls || r->used >= r->capacity)
                return heap_check_fail("invalid available-region list", r, cls);
        }
        if (actual[cls] != expected[cls])
            return heap_check_fail("missing available region", NULL, cls);
    }
    return heap_check_roots(check);
}

static int heap_verify(int phase, void **frame) {
    HeapCheck check = {NULL, 0, phase, frame,
                       (uint32_t)turkey_heap_mark_epoch(),
                       turkey_heap_frame_entries(), turkey_heap_frame_entry_count(),
                       (uintptr_t)turkey_heap_entry_stack_high()};
    HeapRegion *regions = turkey_heap_regions();
    HeapRegion *slow = regions, *fast = regions;
    while (fast != NULL && fast->next != NULL) {
        slow = slow->next;
        fast = fast->next->next;
        if (slow == fast) return heap_check_fail("cyclic region chain", slow, -1);
    }
    for (HeapRegion *r = regions; r != NULL; r = r->next) check.count++;
    if (check.count) {
        if (check.count > SIZE_MAX / sizeof(*check.regions))
            return heap_check_fail("region index too large", NULL, -1);
        check.regions = malloc(check.count * sizeof(*check.regions));
        if (check.regions == NULL) return heap_check_fail("out of memory", NULL, -1);
        size_t i = 0;
        for (HeapRegion *r = regions; r != NULL; r = r->next) check.regions[i++] = r;
        qsort(check.regions, check.count, sizeof(*check.regions), heap_check_order);
    }
    int result = heap_check_contents(&check);
    free(check.regions);
    return result;
}

/* Called by the collector before marking (phase 0), before sweeping (1) and
   after sweeping (2), when TURKEY_GC_VERIFY is set.

   Heap corruption invalidates the mutator's assumptions. In particular, an
   entry initializer may still dereference an allocation result before it can
   propagate a panic, so verification failures must stop here. */
void turkey_heap_verify(int64_t phase, void *frame) {
    if (!heap_verify((int)phase, frame)) {
        fprintf(stderr, "%s\n", panic_buffer);
        exit(EXIT_FAILURE);
    }
}

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
    return turkey_has_panicked || program_panicked;
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
    program_panicked = turkey_has_panicked;
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
int32_t turkey_panic_pending(void) { return turkey_has_panicked; }

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
