#include "turkey_runtime.h"

#include <inttypes.h>
#include <stddef.h>
#include <errno.h>
#include <ctype.h>
#include <time.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

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
/* A header remains immediately before each payload. Small objects occupy
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
    _Alignas(max_align_t) unsigned char data[];
} HeapRegion;
static HeapRegion *regions;
static HeapRegion *available[REGION_CLASSES];
static size_t region_bytes, region_bytes_peak;
static uint32_t mark_epoch;

static HeapRegion *region_of(HeapHeader *header) {
    return (HeapRegion *)((uintptr_t)header & ~((uintptr_t)REGION_BYTES - 1));
}

static HeapHeader *region_allocate(size_t bytes) {
    size_t slot = bytes <= 32 ? 32 : bytes <= 256 ? (bytes + 15) & ~(size_t)15 : 256;
    unsigned cls = slot / 16 - 2;
    while (slot < bytes && slot < REGION_BYTES / 2) { slot *= 2; cls++; }
    int large = bytes > REGION_BYTES / 2;
    HeapRegion *region = large ? NULL : available[cls];
    if (region == NULL) {
        size_t reserved = REGION_BYTES;
        if (large) {
            if (bytes > SIZE_MAX - sizeof(HeapRegion) - (REGION_BYTES - 1)) {
                turkey_panic("allocation is too large"); return NULL;
            }
            reserved = (sizeof(HeapRegion) + bytes + REGION_BYTES - 1)
                & ~((size_t)REGION_BYTES - 1);
            slot = bytes;
        }
        region = aligned_alloc(REGION_BYTES, reserved);
        if (region == NULL) { turkey_panic("out of memory"); return NULL; }
        memset(region, 0, sizeof(HeapRegion));
        region->slot_size = slot;
        region->reserved = reserved;
        region->capacity = large ? 1 : (REGION_BYTES - sizeof(HeapRegion)) / slot;
        region->size_class = large ? REGION_CLASSES : cls;
        region->next = regions;
        regions = region;
        if (!large) available[cls] = region;
        region_bytes += reserved;
        if (region_bytes > region_bytes_peak) region_bytes_peak = region_bytes;
    }
    unsigned word = region->search_word;
    while (region->allocated[word] == UINT64_MAX) word++;
    unsigned bit = (unsigned)__builtin_ctzll(~region->allocated[word]);
    unsigned index = word * 64 + bit;
    region->allocated[word] |= UINT64_C(1) << bit;
    region->search_word = word;
    region->used++;
    if (!large && region->used == region->capacity)
        available[cls] = region->available_next;
    return (HeapHeader *)(region->data + index * region->slot_size);
}
static RootFrame *roots;

/* The innermost root frame, for the crash report in `Turkey.Entry`, which
   walks the chain by `previous` and reads each `function_name`. */
const void *turkey_roots_head(void) { return roots; }
static int64_t heap_count;
static int64_t allocations_since_collection;
static int64_t collection_threshold = 1024;
static int64_t collection_count;
static int gc_stress = -1;
static int gc_initialized;

/* Opt-in accounting, printed by `turkey_gc_report` and, one line per
   collection, by `turkey_collect` itself when TURKEY_GC_STATS is set. The
   point is to answer "what is the collector costing and what is driving it"
   with one run rather than with a profiler and a shrug. */
static int64_t stats_allocations;
static int64_t stats_bytes_allocated;
static int64_t stats_traced;
static int64_t stats_freed_total;
static int64_t stats_live_total;
static int64_t stats_live_peak;
static int64_t stats_collect_clock;
static FILE *stats_log;
/* Scales the collection threshold: the collector runs again after this many
   times the current live set has been allocated. 2 is the default because it
   was measured, not chosen: at 1x the self-compiled compiler spent 79% of its
   wall clock inside turkey_collect, collecting a heap that was
   ~94% garbage every time; the same run at 2x halves the number of full
   mark-and-sweeps for ~1GB of peak memory. TURKEY_GC_THRESHOLD_SCALE
   overrides it for experiments. */
static double threshold_scale = 2.0;
/* Per-collection freed, since the running total is what the sweep knows. */
static int64_t stats_freed_previous;
/* Allocations by what the object is. Indexed by `turkey_object_new`'s kind:
    0 an untagged constructor node (an ADT application -- the tree the rewrite
    passes rebuild), 1 a tagged record (a `CRecord`: buckets, storages,
    dictionaries), 2 an array, strings among them, 3 a closure, 4 a closure
    environment, 5 a box, 7 a cell. Answers "what are they?" where the site
    counters answer "who made them?". Counted only when TURKEY_GC_STATS is set,
    like
    the rest of this: the constructor sites run after `heap_allocate`, which is
    where the flag is resolved. */
static int64_t stats_by_kind[8];
static void stats_count_kind(int kind) {
    if (stats_log != NULL)
        stats_by_kind[kind >= 0 && kind < 8 ? kind : 0]++;
}

static void mark(void *value);
static HeapHeader *header_of(void *value);

static HeapHeader *find_header(void *value) {
    uintptr_t address = (uintptr_t)value;
    for (HeapRegion *region = regions; region != NULL; region = region->next) {
        uintptr_t first = (uintptr_t)region->data + sizeof(HeapHeader);
        if (address < first) continue;
        size_t offset = address - first;
        if (offset % region->slot_size != 0) continue;
        size_t index = offset / region->slot_size;
        if (index < region->capacity &&
                (region->allocated[index / 64] & (UINT64_C(1) << (index % 64))))
            return (HeapHeader *)(region->data + index * region->slot_size);
    }
    return NULL;
}

static int valid_heap_pointer(void *value) {
    if (!gc_stress || value == NULL) return value != NULL;
    if (find_header(value) != NULL) return 1;
    turkey_panic("invalid or collected heap pointer");
    return 0;
}

static int valid_object_kind(void *value, int32_t kind) {
    if (!valid_heap_pointer(value)) return 0;
    if (header_of(value)->kind != HEAP_OBJECT || ((TurkeyObject *)value)->kind != kind) {
        turkey_panic("heap object has the wrong runtime kind");
        return 0;
    }
    return 1;
}

void turkey_root_enter(void *pointer, void *values, int64_t count,
                       const char *function_name) {
    RootFrame *frame = pointer;
    if (frame == NULL || values == NULL || count < 0) {
        turkey_panic("invalid root frame size");
        return;
    }
    frame->previous = roots;
    frame->function_name = function_name;
    frame->count = count;
    frame->values = values;
    // Nothing is live at registration: the frame is entered on the way into
    // the region that contains the safepoints, and each safepoint names its
    // own live set. A caller with roots that outlive the call stores the mask
    // itself right after this returns.
    frame->live = 0;
    roots = frame;
}

void turkey_root_leave(void *pointer) {
    RootFrame *frame = pointer;
    if (frame == NULL || roots != frame) { turkey_panic("unbalanced root frame"); return; }
    roots = frame->previous;
}

static HeapHeader *header_of(void *value) {
    return value == NULL ? NULL : ((HeapHeader *)value) - 1;
}

static void *heap_allocate(size_t size, uint32_t kind) {
    if (!gc_initialized) {
        gc_initialized = 1;
        if (gc_stress < 0) gc_stress = getenv("TURKEY_GC_STRESS") != NULL;
        const char *scale = getenv("TURKEY_GC_THRESHOLD_SCALE");
        if (scale != NULL) {
            char *end;
            errno = 0;
            double parsed = strtod(scale, &end);
            int has_number = end != scale;
            while (isspace((unsigned char)*end)) end++;
            if (has_number && *end == '\0' && errno != ERANGE &&
                    isfinite(parsed) && parsed >= 1.0)
                threshold_scale = parsed;
        }
        if (getenv("TURKEY_GC_STATS") != NULL) stats_log = stderr;
    }
    if (gc_stress || allocations_since_collection >= collection_threshold)
        turkey_collect();
    if (size > SIZE_MAX - sizeof(HeapHeader)) {
        turkey_panic("allocation is too large"); return NULL;
    }
    HeapHeader *header = region_allocate(sizeof(HeapHeader) + size);
    if (header == NULL) { turkey_panic("out of memory"); return NULL; }
    header->next = NULL;
    header->size = size;
    header->kind = kind;
    header->marked = 0;
    heap_count++;
    allocations_since_collection++;
    if (stats_log != NULL) {
        stats_allocations++;
        stats_bytes_allocated += (int64_t)(size + sizeof(HeapHeader));
        if (kind == HEAP_CELL) stats_count_kind(7);
    }
    return header + 1;
}

/* The grey set, as an explicit stack. Tracing used to recurse, which made the
   C stack depth proportional to the longest chain of heap pointers: a list of
   a hundred thousand elements is an ordinary thing for a program to build and
   was a segfault to collect. Kept across collections so the capacity is paid
   for once. */
static void **mark_stack;
static int64_t mark_count;
static int64_t mark_capacity;

static void mark_grey(void *value, const char *what, int64_t index) {
    if (value == NULL) return;
    /* Exact roots and layout metadata make finding the header O(1) in normal
       execution. GC stress keeps the expensive membership check, and it earns
       the cost: a pointer that is not in the heap means a root was missed or
       a layout bitmap is wrong, and this turns that from a rare corruption
       into a panic on the first collection. */
    HeapHeader *header = gc_stress ? find_header(value) : header_of(value);
    if (header == NULL) {
        char message[160];
        if (what == NULL)
            snprintf(message, sizeof(message),
                     "GC root or field is not a heap pointer (%p)", value);
        else
            snprintf(message, sizeof(message), "%s %" PRId64
                     " is not a heap pointer (%p)", what, index, value);
        turkey_panic(message);
        return;
    }
    if (header->marked == mark_epoch) return;
    header->marked = mark_epoch;
    HeapRegion *region = region_of(header);
    size_t slot = ((unsigned char *)header - region->data) / region->slot_size;
    region->marked_slots[slot / 64] |= UINT64_C(1) << (slot % 64);
    region->live++;
    if (mark_count == mark_capacity) {
        int64_t capacity = mark_capacity < 64 ? 64 : mark_capacity * 2;
        void **grown = realloc(mark_stack, (size_t)capacity * sizeof(void *));
        if (grown == NULL) {
            turkey_panic("out of memory while tracing the heap");
            return;
        }
        mark_stack = grown;
        mark_capacity = capacity;
    }
    mark_stack[mark_count++] = value;
}

static void mark_children(void *value) {
    HeapHeader *header = header_of(value);
    if (header->kind == HEAP_CELL) {
        TurkeyCell *cell = value;
        if (cell->pointer_value)
            mark_grey((void *)(uintptr_t)cell->value, NULL, 0);
        return;
    }
    if (header->kind != HEAP_OBJECT) return;
    TurkeyObject *object = value;
    /* Code 7 is a traced pointer and 6 is a pointer-sized word the collector
       must not follow: a raw address. A test of `>= 6` follows both, and
       collects through an address it has no business reading. */
    if (object->kind == 2) {
        if (object->tag == 7)
            for (int64_t index = 0; index < object->count; ++index)
                mark_grey((void *)(uintptr_t)object->slots[index],
                          "array pointer field", index);
    } else if (object->kind == 0 || object->kind == 1) {
        for (int64_t index = 0; index < object->count; ++index)
            if (((object->pointer_bitmap >> (3 * index)) & 7) == 7)
                mark_grey((void *)(uintptr_t)object->slots[index],
                          "object pointer field", index);
    } else {
        for (int64_t index = 0; index < object->count; ++index)
            if (object->pointer_bitmap & (UINT64_C(1) << index))
                mark_grey((void *)(uintptr_t)object->slots[index],
                          "capture", index);
    }
}

static void mark(void *value) {
    mark_grey(value, NULL, 0);
    while (mark_count > 0) mark_children(mark_stack[--mark_count]);
}


/* -- frame tables ------------------------------------------------------------

   The arm64 backend registers roots OCaml's way: no function pushes or pops
   anything, and each call site that may collect has a table entry naming the
   frame offsets of the roots live across it, keyed by the return address
   for it. The collector walks
   `x29` frame records -- which Apple's ABI requires to be valid at all times --
   and looks each return address up.

   Both root schemes run at once, and that is the point: a C frame and an
   LLVM-path frame have return addresses in no table, so they are skipped, and
   the shadow-stack chain above goes on covering them. A binary that registers
   no table walks no frames at all. */

typedef struct FrameEntry {
    uintptr_t retaddr;
    int64_t count;
    const int64_t *offsets; /* from x29, signed */
} FrameEntry;

static FrameEntry *frame_entries;
static int64_t frame_entry_count;

static int compare_frame_entries(const void *a, const void *b) {
    uintptr_t x = ((const FrameEntry *)a)->retaddr;
    uintptr_t y = ((const FrameEntry *)b)->retaddr;
    return x < y ? -1 : x > y ? 1 : 0;
}

/* The emitter writes a flat array -- count, then {return address, n, n
   offsets} -- because it cannot sort by an address the linker has not assigned
   yet. Sorting it once here is what makes the lookup a binary search. */
void turkey_frame_table_register(const void *table) {
    const int64_t *words = table;
    if (words == NULL) return;
    int64_t count = words[0];
    if (count <= 0) return;
    FrameEntry *entries = malloc((size_t)count * sizeof *entries);
    if (entries == NULL) {
        turkey_panic("out of memory registering the frame table");
        return;
    }
    const int64_t *p = words + 1;
    for (int64_t index = 0; index < count; ++index) {
        entries[index].retaddr = (uintptr_t)p[0];
        entries[index].count = p[1];
        entries[index].offsets = p + 2;
        p += 2 + p[1];
    }
    qsort(entries, (size_t)count, sizeof *entries, compare_frame_entries);
    free(frame_entries);
    frame_entries = entries;
    frame_entry_count = count;
}

static const FrameEntry *frame_entry_for(uintptr_t retaddr) {
    int64_t low = 0, high = frame_entry_count - 1;
    while (low <= high) {
        int64_t middle = low + (high - low) / 2;
        uintptr_t found = frame_entries[middle].retaddr;
        if (found == retaddr) return &frame_entries[middle];
        if (found < retaddr) low = middle + 1; else high = middle - 1;
    }
    return NULL;
}

/* The frame `Turkey.Entry`'s entry thread runs the program from, which is the
   outermost frame any walk of the mutator stack can reach.

   Asking pthread for the bounds instead is what this used to do, and it is
   both unportable and less precise: `pthread_get_stackaddr_np` is Darwin's
   spelling, glibc's `pthread_getattr_np` returns the *low* address rather
   than the high one, and neither says where the program's frames actually
   begin -- only where the thread's stack was mapped. Deriving the bound from
   `TURKEY_STACK_BYTES` is no better, since `pthread_attr_setstacksize` may
   round up or carve out a guard page. The one frame the entry creates the
   thread for is a bound it knows exactly. */
static char *entry_stack_high;

/* Set by `Turkey.Entry`'s entry thread before the program runs, from its own
   frame (TIX-67): the entry is Turkey now, and this bound is the collector's,
   so the entry reports it rather than owning it. */
void turkey_entry_stack_set(void *frame) { entry_stack_high = frame; }

/* Every frame of the current stack, from this one outwards.

   The stop conditions matter more than the loop: a walk that runs off the end
   of the chain marks whatever the words beyond it happen to hold, which would
   surface as a corruption a long way from here and look exactly like a
   miscompile. So the frame pointer must stay inside this thread's stack, stay
   16-byte aligned, and strictly increase.

   There is no low bound to check. The walk starts at this function's own live
   frame and `frame` only ever increases, so nothing it reaches can be below
   the stack; the high bound and the strict increase are what confine it. */
static void scan_native_frames(void) {
    if (frame_entry_count == 0) return;
    char *high = entry_stack_high;
    if (high == NULL) {
        /* Reachable only if something ran the program without going through
           `Turkey.Entry`, which sets the bound even when it could not make a
           thread. Scanning from an unknown outer bound is how a walk runs off
           the end; not scanning drops live roots silently, which is worse. So
           say so instead of doing either. */
        turkey_panic("no entry stack bound: the frame walker cannot run");
        return;
    }
    void **frame = __builtin_frame_address(0);
    while ((char *)frame + 16 <= high && ((uintptr_t)frame & 15) == 0) {
        /* The return address in *this* record is an address in the *caller*,
           so the entry it finds describes the caller's frame -- whose `x29` is
           this record's saved one. Applying the offsets to this frame instead
           reads whatever the callee happens to have at those offsets, which is
           how this was wrong the first time. */
        void **next = frame[0];
        if (next <= frame || (char *)next + 16 > high
                || ((uintptr_t)next & 15) != 0) break;
        const FrameEntry *entry = frame_entry_for((uintptr_t)frame[1]);
        if (entry != NULL)
            for (int64_t index = 0; index < entry->count; ++index) {
                mark_grey(*(void **)((char *)next + entry->offsets[index]),
                          "arm64 frame", index);
                while (mark_count > 0) mark_children(mark_stack[--mark_count]);
            }
        frame = next;
    }
}

void turkey_collect(void) {
    struct timespec stats_start, stats_end;
    int64_t stats_live_before = heap_count;
    /* Read before the sweep clears it: this is the allocation pressure the
       collection is responding to. */
    int64_t stats_allocs_since = allocations_since_collection;
    int stats_wanted = stats_log != NULL;
    if (stats_wanted) clock_gettime(CLOCK_MONOTONIC, &stats_start);
    /* Epoch marks need no per-object clearing in an ordinary sweep. Handle
       wrap explicitly, including stress runs that collect at every allocation. */
    if (++mark_epoch == 0) {
        for (HeapRegion *region = regions; region != NULL; region = region->next) {
            for (unsigned word = 0; word < REGION_WORDS; word++) {
                uint64_t bits = region->allocated[word];
                while (bits) {
                    unsigned slot = word * 64 + (unsigned)__builtin_ctzll(bits);
                    HeapHeader *header = (HeapHeader *)(region->data + slot * region->slot_size);
                    header->marked = 0;
                    bits &= bits - 1;
                }
            }
        }
        mark_epoch = 1;
    }
    for (RootFrame *frame = roots; frame != NULL; frame = frame->previous)
        for (int64_t index = 0; index < frame->count; ++index)
            if (index >= 64 || (frame->live >> index) & 1) {
                /* Named, because "a root is not a heap pointer" is true of one
                   slot of one frame and a compiler emitting roots for the
                   first time needs to know which. `function_name` is already
                   carried for the crash handler; this is the same string. */
                mark_grey(frame->values[index], frame->function_name, index);
                while (mark_count > 0) mark_children(mark_stack[--mark_count]);
            }
    /* Beside the chain, not instead of it: the arm64 backend's frames are
       here and everything else's are above. */
    scan_native_frames();
    /* Empty regions cost one free, regardless of their allocation count.
       Survivors rebuild availability by copying a fixed-size bitmap and
       using epoch marks. No walk over individual object headers. */
    memset(available, 0, sizeof(available));
    HeapRegion **link = &regions;
    while (*link != NULL) {
        HeapRegion *region = *link;
        int64_t dead = region->used - region->live;
        heap_count -= dead;
        stats_freed_total += dead;
        if (region->live == 0) {
            *link = region->next;
            region_bytes -= region->reserved;
            free(region);
            continue;
        }
        memcpy(region->allocated, region->marked_slots, sizeof(region->allocated));
        memset(region->marked_slots, 0, sizeof(region->marked_slots));
        region->used = region->live;
        region->live = 0;
        region->search_word = 0;
        if (region->used < region->capacity && region->size_class < REGION_CLASSES) {
            unsigned cls = region->size_class;
            region->available_next = available[cls];
            available[cls] = region;
        }
        link = &region->next;
    }
    allocations_since_collection = 0;
    double next_threshold = (double)(heap_count > 1024 ? heap_count : 1024)
        * threshold_scale;
    /* INT64_MAX rounds up when converted to double; do not cast that bound. */
    collection_threshold = next_threshold >= (double)INT64_MAX
        ? INT64_MAX : (int64_t)next_threshold;
    collection_count++;
    if (stats_wanted) {
        clock_gettime(CLOCK_MONOTONIC, &stats_end);
        int64_t nanos = (stats_end.tv_sec - stats_start.tv_sec) * 1000000000ll
            + (stats_end.tv_nsec - stats_start.tv_nsec);
        int64_t freed_now = stats_freed_total - stats_freed_previous;
        int64_t survived = stats_live_before - freed_now;
        stats_freed_previous = stats_freed_total;
        stats_collect_clock += nanos;
        stats_traced += survived > 0 ? survived : 0;
        stats_live_total += heap_count;
        if (heap_count > stats_live_peak) stats_live_peak = heap_count;
        fprintf(stats_log,
                "[gc %" PRId64 "] allocs-since %" PRId64 ", live-before %" PRId64
                ", survived %" PRId64 ", freed %" PRId64
                ", next threshold %" PRId64 ", %.3f ms\n",
                collection_count, stats_allocs_since, stats_live_before,
                survived, freed_now,
                collection_threshold,
                (double)nanos / 1e6);
        fflush(stats_log);
    }
}

void turkey_gc_report(void) {
    if (stats_log == NULL) return;
    fprintf(stderr, "[gc] region bytes %zu, peak %zu\n", region_bytes, region_bytes_peak);
    fprintf(stderr,
            "[gc] collections %" PRId64 ", allocations %" PRId64
            ", bytes %" PRId64 " (%.1f MB)\n",
            collection_count, stats_allocations, stats_bytes_allocated,
            (double)stats_bytes_allocated / (1024.0 * 1024.0));
    fprintf(stderr,
            "[gc] by kind: constr %" PRId64
            ", record %" PRId64 ", array %" PRId64 ", closure %" PRId64
            ", closure-env %" PRId64 ", box %" PRId64 ", cell %" PRId64 "\n",
            stats_by_kind[0], stats_by_kind[1],
            stats_by_kind[2], stats_by_kind[3], stats_by_kind[4],
            stats_by_kind[5], stats_by_kind[7]);
    fprintf(stderr,
            "[gc] final live %" PRId64 ", peak live %" PRId64
            ", objects traced %" PRId64 ", freed %" PRId64
            ", collect time %.3f s\n",
            heap_count, stats_live_peak, stats_traced, stats_freed_total,
            (double)stats_collect_clock / 1e9);
}

int64_t turkey_heap_objects(void) { return heap_count; }
int64_t turkey_collection_count(void) { return collection_count; }
void turkey_gc_set_stress(int32_t enabled) { gc_stress = enabled != 0; }

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

/* ------------------------------------------- the collector's allocation interface
 *
 * What generated code calls to make a heap object, and so what the collector
 * hands out: each of these fills a header over `heap_allocate`, which is the
 * safepoint and the region allocator. They stay in C with the collector and
 * move when it does -- a managed object cannot come from `malloc`, because it
 * needs a header and an allocation bit in a region this file owns.
 *
 * Two shapes are pinned here rather than stated anywhere a layout change could
 * see them: a closure is `[code, environment]`, and a `String` is a byte array
 * -- `lib/Data/String/Type.gob` erases to one, so `turkey_string_new` below is
 * what interns a literal.
 */

void *turkey_cell_new(uint64_t value, int32_t pointer_value) {
    TurkeyCell *cell = heap_allocate(sizeof(TurkeyCell), HEAP_CELL);
    if (cell != NULL) { cell->value = value; cell->pointer_value = pointer_value; }
    return cell;
}

void *turkey_object_new(int32_t kind, int32_t tag, int64_t count,
                        uint64_t pointer_bitmap) {
    if (count < 0 || count > 63 ||
            ((kind == 0 || kind == 1) && count > 21) || (uint64_t)count >
            (SIZE_MAX - sizeof(TurkeyObject)) / sizeof(uint64_t)) {
        turkey_panic("invalid object size");
        return NULL;
    }
    TurkeyObject *object = heap_allocate(
        sizeof(TurkeyObject) + (size_t)count * sizeof(uint64_t), HEAP_OBJECT);
    if (object == NULL) return NULL;
    stats_count_kind(kind);
    object->kind = kind;
    object->tag = tag;
    object->count = count;
    object->pointer_bitmap = pointer_bitmap;
    memset(object->slots, 0, (size_t)count * sizeof(uint64_t));
    return object;
}

void *turkey_box(uint64_t value, int32_t layout) {
    TurkeyObject *box = turkey_object_new(5, layout, 1, 0);
    if (box != NULL) box->slots[0] = value;
    return box;
}

uint64_t turkey_unbox(void *pointer, int32_t layout) {
    if (!valid_object_kind(pointer, 5)) return 0;
    TurkeyObject *box = pointer;
    if (box->tag != layout) {
        turkey_panic("boxed value has the wrong scalar layout");
        return 0;
    }
    return box->slots[0];
}

void *turkey_array_new(int64_t length, uint64_t initial, int32_t element_width,
                       int32_t element_layout) {
    if (length < 0) {
        turkey_panic("array length cannot be negative");
        return NULL;
    }
    if (!(element_width == 1 || element_width == 4 || element_width == 8) ||
            (uint64_t)length > (SIZE_MAX - sizeof(TurkeyObject)) /
            (uint32_t)element_width) {
        turkey_panic("invalid array size");
        return NULL;
    }
    TurkeyObject *array = heap_allocate(
        sizeof(TurkeyObject) + (size_t)length * (uint32_t)element_width,
        HEAP_OBJECT);
    if (array == NULL) return NULL;
    stats_count_kind(2);
    array->kind = 2;
    array->tag = element_layout;
    array->count = length;
    array->pointer_bitmap = (uint32_t)element_width;
    /* One `memcpy` call per element was the previous shape of this, which for
       a million-element array is a million calls to copy up to eight bytes.
       A zero fill is a `memset` whatever the width, and a repeating fill is a
       typed store. */
    unsigned char *bytes = (unsigned char *)array->slots;
    if (initial == 0) {
        memset(bytes, 0, (size_t)length * (uint32_t)element_width);
    } else if (element_width == 1) {
        memset(bytes, (int)(initial & 0xff), (size_t)length);
    } else if (element_width == 4) {
        uint32_t *words = (uint32_t *)bytes;
        uint32_t value = (uint32_t)initial;
        for (int64_t index = 0; index < length; ++index) words[index] = value;
    } else {
        uint64_t *words = (uint64_t *)bytes;
        for (int64_t index = 0; index < length; ++index) words[index] = initial;
    }
    return array;
}

void *turkey_closure_new(uint64_t code, int64_t capture_count,
                         uint64_t pointer_bitmap) {
    /* A capture-free closure needs no environment object: nothing can read
       one, because a body with no captures never touches its environment
       parameter, and the marker skips a null second slot already. This is
       what the emitter's static closures rely on -- and what a capture-free
       closure built any other way still benefits from, one object instead of
       a pair. */
    if (capture_count == 0) {
        TurkeyObject *closure = turkey_object_new(3, -1, 2, 2);
        if (closure == NULL) return NULL;
        closure->slots[0] = code;
        closure->slots[1] = 0;
        return closure;
    }
    RootFrame frame;
    void *roots[1] = {NULL};
    turkey_root_enter(&frame, roots, 1, "turkey_closure_new");
    TurkeyObject *environment = turkey_object_new(
        4, -1, capture_count, pointer_bitmap);
    if (environment == NULL) { turkey_root_leave(&frame); return NULL; }
    roots[0] = environment;
    frame.live = 1;
    TurkeyObject *closure = turkey_object_new(3, -1, 2, 2);
    if (closure == NULL) { turkey_root_leave(&frame); return NULL; }
    closure->slots[0] = code;
    closure->slots[1] = (uint64_t)(uintptr_t)environment;
    turkey_root_leave(&frame);
    return closure;
}


/* A literal, as the entry interns it: a byte array holding a copy. */
void *turkey_string_new(const unsigned char *bytes, int64_t length) {
    TurkeyObject *array = turkey_array_new(length, 0, 1, 2);
    if (array != NULL && length > 0)
        memcpy(array->slots, bytes, (size_t)length);
    return array;
}

/* ------------------------------------------------------------------ float text
 *
 * `snprintf` and `strtod` cannot be declared as foreign functions, since the
 * language has no variadics, so these three stay C until shortest round-trip
 * formatting and correctly rounded parsing are written in Turkey. They read and
 * build strings as byte arrays like everything else here.
 */

void *turkey_float_to_string(double value) {
    char buffer[64];
    int length;
    if (isnan(value)) length = snprintf(buffer, sizeof(buffer), "NaN");
    else if (isinf(value)) length = snprintf(buffer, sizeof(buffer),
                                             signbit(value) ? "-Infinity" : "Infinity");
    else if (value == 0.0) {
        length = snprintf(buffer, sizeof(buffer), signbit(value) ? "-0.0" : "0.0");
    } else {
        union { double number; uint64_t bits; } original = { .number = value }, parsed;
        char trial[64];
        int precision;
        for (precision = 1; precision < 17; ++precision) {
            snprintf(trial, sizeof(trial), "%.*g", precision, value);
            parsed.number = strtod(trial, NULL);
            if (parsed.bits == original.bits) break;
        }
        int exponent = (int)floor(log10(fabs(value)));
        if (exponent >= -4 && exponent < 16) {
            int decimals = precision - exponent - 1;
            if (decimals < 0) decimals = 0;
            length = snprintf(buffer, sizeof(buffer), "%.*f", decimals, value);
        } else {
            length = snprintf(buffer, sizeof(buffer), "%.*e", precision - 1, value);
            length = (int)strlen(buffer);
        }
        char *marker = strchr(buffer, 'e');
        if (strchr(buffer, '.') == NULL || (marker != NULL && strchr(buffer, '.') > marker)) {
            size_t position = marker == NULL ? (size_t)length : (size_t)(marker - buffer);
            memmove(buffer + position + 2, buffer + position,
                    (size_t)length - position + 1);
            buffer[position] = '.';
            buffer[position + 1] = '0';
            length += 2;
        }
    }
    return turkey_string_new((const unsigned char *)buffer, length);
}

static int parse_float(void *string, double *result) {
    if (string == NULL) return 0;
    struct { int64_t length; const unsigned char *bytes; } view = {
        string_length(string), string_bytes(string) }, *value = &view;
    if (value->length == 3 && memcmp(value->bytes, "NaN", 3) == 0) {
        *result = NAN; return 1;
    }
    if (value->length == 8 && memcmp(value->bytes, "Infinity", 8) == 0) {
        *result = INFINITY; return 1;
    }
    if (value->length == 9 && memcmp(value->bytes, "-Infinity", 9) == 0) {
        *result = -INFINITY; return 1;
    }
    int64_t index = 0;
    if (index < value->length &&
            (value->bytes[index] == '+' || value->bytes[index] == '-')) index++;
    int64_t whole = index;
    while (index < value->length && value->bytes[index] >= '0' &&
           value->bytes[index] <= '9') index++;
    if (index == whole || index >= value->length || value->bytes[index++] != '.') return 0;
    int64_t fraction = index;
    while (index < value->length && value->bytes[index] >= '0' &&
           value->bytes[index] <= '9') index++;
    if (index == fraction) return 0;
    if (index < value->length &&
            (value->bytes[index] == 'e' || value->bytes[index] == 'E')) {
        index++;
        if (index < value->length &&
                (value->bytes[index] == '+' || value->bytes[index] == '-')) index++;
        int64_t exponent = index;
        while (index < value->length && value->bytes[index] >= '0' &&
               value->bytes[index] <= '9') index++;
        if (index == exponent) return 0;
    }
    if (index != value->length || (uint64_t)value->length >= SIZE_MAX) return 0;
    char *text = malloc((size_t)value->length + 1);
    if (text == NULL) { turkey_panic("out of memory"); return 0; }
    memcpy(text, value->bytes, (size_t)value->length);
    text[value->length] = '\0';
    *result = strtod(text, NULL);
    free(text);
    return 1;
}

double turkey_float_parse(void *value) {
    double result = 0.0;
    if (!parse_float(value, &result)) turkey_panic("string is not a Float");
    return result;
}

int32_t turkey_float_can_parse(void *value) {
    double ignored;
    return parse_float(value, &ignored);
}



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
