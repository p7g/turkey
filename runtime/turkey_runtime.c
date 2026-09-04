#include "turkey_runtime.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <signal.h>
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

enum { HEAP_STRING = 1, HEAP_OBJECT = 2, HEAP_CELL = 3 };
static HeapHeader *heap;
static RootFrame *roots;
static int64_t heap_count;
static int64_t allocations_since_collection;
static int64_t collection_threshold = 1024;
static int64_t collection_count;
static int gc_stress = -1;

static void mark(void *value);
static HeapHeader *header_of(void *value);

static HeapHeader *find_header(void *value) {
    for (HeapHeader *header = heap; header != NULL; header = header->next)
        if ((void *)(header + 1) == value) return header;
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
    if (gc_stress < 0) gc_stress = getenv("TURKEY_GC_STRESS") != NULL;
    if (gc_stress || allocations_since_collection >= collection_threshold)
        turkey_collect();
    if (size > SIZE_MAX - sizeof(HeapHeader)) {
        turkey_panic("allocation is too large"); return NULL;
    }
    HeapHeader *header = malloc(sizeof(HeapHeader) + size);
    if (header == NULL) { turkey_panic("out of memory"); return NULL; }
    header->next = heap;
    header->size = size;
    header->kind = kind;
    header->marked = 0;
    heap = header;
    heap_count++;
    allocations_since_collection++;
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
    if (header->marked) return;
    header->marked = 1;
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
    if (object->kind == 2) {
        if (object->tag >= 6)
            for (int64_t index = 0; index < object->count; ++index)
                mark_grey((void *)(uintptr_t)object->slots[index],
                          "array pointer field", index);
    } else if (object->kind == 0 || object->kind == 1) {
        for (int64_t index = 0; index < object->count; ++index)
            if (((object->pointer_bitmap >> (3 * index)) & 7) >= 6)
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

void turkey_collect(void) {
    for (RootFrame *frame = roots; frame != NULL; frame = frame->previous)
        for (int64_t index = 0; index < frame->count; ++index)
            if (index >= 64 || (frame->live >> index) & 1)
                mark(frame->values[index]);
    HeapHeader **link = &heap;
    while (*link != NULL) {
        HeapHeader *header = *link;
        if (!header->marked) {
            *link = header->next;
            free(header);
            heap_count--;
        } else {
            header->marked = 0;
            link = &header->next;
        }
    }
    allocations_since_collection = 0;
    collection_threshold = heap_count > 1024 ? heap_count : 1024;
    collection_count++;
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

void turkey_panic_string(TurkeyString *message) {
    if (turkey_has_panicked) return;
    if (message == NULL) { turkey_panic("error"); return; }
    int length = message->length < (int64_t)sizeof(panic_buffer) - 1
        ? (int)message->length : (int)sizeof(panic_buffer) - 1;
    memcpy(panic_buffer, message->bytes, (size_t)length);
    panic_buffer[length] = '\0';
    turkey_has_panicked = 1;
    capture_panic_trace();
}

int32_t turkey_panicked(void) { return turkey_has_panicked; }
const char *turkey_panic_message(void) { return panic_buffer; }
void turkey_panic_clear(void) {
    turkey_has_panicked = 0;
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

TurkeyString *turkey_string_new(const unsigned char *bytes, int64_t length) {
    if (length < 0 || (uint64_t)length > SIZE_MAX - sizeof(TurkeyString)) {
        turkey_panic("invalid string length");
        return NULL;
    }
    TurkeyString *result = heap_allocate(sizeof(TurkeyString) + (size_t)length,
                                         HEAP_STRING);
    if (result == NULL) return NULL;
    result->length = length;
    memcpy(result->bytes, bytes, (size_t)length);
    return result;
}

TurkeyString *turkey_string_concat(TurkeyString *left, TurkeyString *right) {
    if (left == NULL || right == NULL || left->length > INT64_MAX - right->length) {
        turkey_panic("string is too large");
        return NULL;
    }
    int64_t length = left->length + right->length;
    TurkeyString *result = heap_allocate(sizeof(TurkeyString) + (size_t)length,
                                         HEAP_STRING);
    if (result == NULL) return NULL;
    result->length = length;
    memcpy(result->bytes, left->bytes, (size_t)left->length);
    memcpy(result->bytes + left->length, right->bytes, (size_t)right->length);
    return result;
}

TurkeyString *turkey_int_to_string(int64_t value) {
    char buffer[32];
    int length = snprintf(buffer, sizeof(buffer), "%" PRId64, value);
    return turkey_string_new((const unsigned char *)buffer, length);
}

TurkeyString *turkey_float_to_string(double value) {
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

static int parse_float(TurkeyString *value, double *result) {
    if (value == NULL) return 0;
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

double turkey_float_parse(TurkeyString *value) {
    double result = 0.0;
    if (!parse_float(value, &result)) turkey_panic("string is not a Float");
    return result;
}

int32_t turkey_float_can_parse(TurkeyString *value) {
    double ignored;
    return parse_float(value, &ignored);
}

double turkey_float_fmod(double left, double right) { return fmod(left, right); }
double turkey_float_remainder(double left, double right) { return remainder(left, right); }
double turkey_float_floor(double value) { return floor(value); }
double turkey_float_ceil(double value) { return ceil(value); }
double turkey_float_round(double value) { return round(value); }
double turkey_float_trunc(double value) { return trunc(value); }

TurkeyString *turkey_char_to_string(uint32_t value) {
    unsigned char out[4];
    int64_t n;
    if (value <= 0x7f) { out[0] = value; n = 1; }
    else if (value <= 0x7ff) {
        out[0] = 0xc0 | (value >> 6); out[1] = 0x80 | (value & 0x3f); n = 2;
    } else if (value <= 0xffff && !(value >= 0xd800 && value <= 0xdfff)) {
        out[0] = 0xe0 | (value >> 12); out[1] = 0x80 | ((value >> 6) & 0x3f);
        out[2] = 0x80 | (value & 0x3f); n = 3;
    } else if (value <= 0x10ffff) {
        out[0] = 0xf0 | (value >> 18); out[1] = 0x80 | ((value >> 12) & 0x3f);
        out[2] = 0x80 | ((value >> 6) & 0x3f); out[3] = 0x80 | (value & 0x3f); n = 4;
    } else {
        turkey_panic("invalid Unicode scalar value"); return NULL;
    }
    return turkey_string_new(out, n);
}

int64_t turkey_string_byte_length(TurkeyString *value) { return value->length; }

static int string_index(TurkeyString *value, int64_t index) {
    if (value != NULL && index >= 0 && index < value->length) return 1;
    char message[128];
    snprintf(message, sizeof(message),
             "string byte index out of bounds: %" PRId64 ", length %" PRId64,
             index, value == NULL ? 0 : value->length);
    turkey_panic(message);
    return 0;
}

static int utf8_width(unsigned char lead) {
    if (lead < 0x80) return 1;
    if (lead >= 0xf0) return 4;
    if (lead >= 0xe0) return 3;
    return 2;
}

static int utf8_boundary(TurkeyString *value, int64_t index) {
    return index == 0 || index == value->length ||
        (index > 0 && index < value->length &&
         (value->bytes[index] & 0xc0) != 0x80);
}

uint8_t turkey_string_byte_at(TurkeyString *value, int64_t index) {
    return string_index(value, index) ? value->bytes[index] : 0;
}

uint32_t turkey_string_decode_at(TurkeyString *value, int64_t index) {
    if (!string_index(value, index)) return 0;
    unsigned char lead = value->bytes[index];
    if ((lead & 0xc0) == 0x80) {
        char message[96];
        snprintf(message, sizeof(message),
                 "byte offset %" PRId64 " is not a character boundary", index);
        turkey_panic(message);
        return 0;
    }
    int width = utf8_width(lead);
    uint32_t scalar = width == 1 ? lead : lead & (0x7f >> width);
    for (int offset = 1; offset < width; ++offset)
        scalar = (scalar << 6) | (value->bytes[index + offset] & 0x3f);
    return scalar;
}

int64_t turkey_string_next_index(TurkeyString *value, int64_t index) {
    if (!string_index(value, index)) return 0;
    if ((value->bytes[index] & 0xc0) == 0x80) {
        char message[96];
        snprintf(message, sizeof(message),
                 "byte offset %" PRId64 " is not a character boundary", index);
        turkey_panic(message);
        return 0;
    }
    return index + utf8_width(value->bytes[index]);
}

TurkeyString *turkey_string_slice(TurkeyString *value, int64_t start, int64_t stop) {
    if (value == NULL || start < 0 || start > stop || stop > value->length) {
        char message[128];
        snprintf(message, sizeof(message),
                 "string slice %" PRId64 "..%" PRId64 " is out of bounds", start, stop);
        turkey_panic(message);
        return NULL;
    }
    if (!utf8_boundary(value, start) || !utf8_boundary(value, stop)) {
        char message[160];
        snprintf(message, sizeof(message),
                 "string slice %" PRId64 "..%" PRId64
                 " does not fall on character boundaries", start, stop);
        turkey_panic(message);
        return NULL;
    }
    return turkey_string_new(value->bytes + start, stop - start);
}

static int64_t byte_find(TurkeyString *haystack, TurkeyString *needle,
                         int64_t start, int reverse) {
    if (haystack == NULL || needle == NULL) return -1;
    if (needle->length == 0)
        return reverse ? haystack->length :
            (start < 0 ? 0 : start > haystack->length ? -1 : start);
    if (needle->length > haystack->length) return -1;
    int64_t last = haystack->length - needle->length;
    if (reverse) {
        for (int64_t index = last; index >= 0; --index)
            if (memcmp(haystack->bytes + index, needle->bytes,
                       (size_t)needle->length) == 0) return index;
    } else {
        if (start < 0) start = 0;
        for (int64_t index = start; index <= last; ++index)
            if (memcmp(haystack->bytes + index, needle->bytes,
                       (size_t)needle->length) == 0) return index;
    }
    return -1;
}

int64_t turkey_string_find(TurkeyString *haystack, TurkeyString *needle,
                           int64_t start) {
    return byte_find(haystack, needle, start, 0);
}

int64_t turkey_string_rfind(TurkeyString *haystack, TurkeyString *needle) {
    return byte_find(haystack, needle, 0, 1);
}

int32_t turkey_string_eq(TurkeyString *left, TurkeyString *right) {
    return left->length == right->length &&
        memcmp(left->bytes, right->bytes, (size_t)left->length) == 0;
}

int32_t turkey_string_lt(TurkeyString *left, TurkeyString *right) {
    size_t common = (size_t)(left->length < right->length ? left->length : right->length);
    int order = memcmp(left->bytes, right->bytes, common);
    return order < 0 || (order == 0 && left->length < right->length);
}

uint8_t turkey_write(TurkeyString *value) {
    if (value == NULL) return 0;
    fwrite(value->bytes, 1, (size_t)value->length, stdout);
    fflush(stdout);
    return 0;
}

uint8_t turkey_print(TurkeyString *value) {
    turkey_write(value); fputc('\n', stdout); fflush(stdout); return 0;
}

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
    object->kind = kind;
    object->tag = tag;
    object->count = count;
    object->pointer_bitmap = pointer_bitmap;
    memset(object->slots, 0, (size_t)count * sizeof(uint64_t));
    return object;
}

static int object_index(TurkeyObject *object, int64_t index) {
    if (index < 0 || index >= object->count) {
        turkey_panic("invalid object field");
        return 0;
    }
    return 1;
}

void turkey_object_set(void *pointer, int64_t index, uint64_t value) {
    if (!valid_heap_pointer(pointer)) return;
    TurkeyObject *object = pointer;
    if (object_index(object, index)) object->slots[index] = value;
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

/* `Data.Array#Array` is a newtype (`DeclTable.newtypes`), so the value handed
   over here is the `ArrayStorage` record itself: the constructor around it is
   not built by `wrap_array` and is not there to be read back. */
static int array_parts(void *wrapper, TurkeyObject **data, int64_t *length) {
    if (!valid_object_kind(wrapper, 1)) return 0;
    TurkeyObject *storage = wrapper;
    if (storage->count < 2) { turkey_panic("invalid Array storage"); return 0; }
    void *data_pointer = (void *)(uintptr_t)storage->slots[0];
    if (!valid_object_kind(data_pointer, 2)) return 0;
    *data = data_pointer;
    *length = (int64_t)storage->slots[1];
    if (*length < 0 || *length > (*data)->count) {
        turkey_panic("invalid Array length");
        return 0;
    }
    return 1;
}

static int valid_utf8_bytes(const unsigned char *bytes, int64_t length) {
    for (int64_t index = 0; index < length;) {
        unsigned char lead = bytes[index];
        int width;
        uint32_t scalar;
        if (lead < 0x80) { width = 1; scalar = lead; }
        else if (lead >= 0xc2 && lead <= 0xdf) { width = 2; scalar = lead & 0x1f; }
        else if (lead >= 0xe0 && lead <= 0xef) { width = 3; scalar = lead & 0x0f; }
        else if (lead >= 0xf0 && lead <= 0xf4) { width = 4; scalar = lead & 0x07; }
        else return 0;
        if (index > length - width) return 0;
        for (int offset = 1; offset < width; ++offset) {
            unsigned char byte = bytes[index + offset];
            if ((byte & 0xc0) != 0x80) return 0;
            scalar = (scalar << 6) | (byte & 0x3f);
        }
        if ((width == 2 && scalar < 0x80) ||
                (width == 3 && scalar < 0x800) ||
                (width == 4 && scalar < 0x10000) ||
                (scalar >= 0xd800 && scalar <= 0xdfff) || scalar > 0x10ffff)
            return 0;
        index += width;
    }
    return 1;
}

void *turkey_string_to_byte_storage(TurkeyString *value) {
    if (value == NULL) return NULL;
    TurkeyObject *array = turkey_array_new(value->length, 0, 1, 2);
    if (array != NULL)
        memcpy(array->slots, value->bytes, (size_t)value->length);
    return array;
}

TurkeyString *turkey_string_from_bytes(void *wrapper) {
    TurkeyObject *array;
    int64_t length;
    if (!array_parts(wrapper, &array, &length)) return NULL;
    unsigned char *bytes = (unsigned char *)array->slots;
    if (!valid_utf8_bytes(bytes, length)) {
        turkey_panic("bytes are not valid UTF-8");
        return NULL;
    }
    return turkey_string_new(bytes, length);
}

int32_t turkey_string_is_valid_utf8(void *wrapper) {
    TurkeyObject *array;
    int64_t length;
    if (!array_parts(wrapper, &array, &length)) return 0;
    return valid_utf8_bytes((unsigned char *)array->slots, length);
}

TurkeyString *turkey_string_concat_all(void *wrapper) {
    TurkeyObject *array;
    int64_t count;
    if (!array_parts(wrapper, &array, &count)) return NULL;
    int64_t length = 0;
    for (int64_t index = 0; index < count; ++index) {
        TurkeyString *part = (TurkeyString *)(uintptr_t)array->slots[index];
        if (part == NULL || part->length > INT64_MAX - length) {
            turkey_panic("string is too large");
            return NULL;
        }
        length += part->length;
    }
    TurkeyString *result = heap_allocate(sizeof(TurkeyString) + (size_t)length,
                                         HEAP_STRING);
    if (result == NULL) return NULL;
    result->length = length;
    int64_t offset = 0;
    for (int64_t index = 0; index < count; ++index) {
        TurkeyString *part = (TurkeyString *)(uintptr_t)array->slots[index];
        memcpy(result->bytes + offset, part->bytes, (size_t)part->length);
        offset += part->length;
    }
    return result;
}

void *turkey_closure_new(uint64_t code, int64_t capture_count,
                         uint64_t pointer_bitmap) {
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

void turkey_closure_capture(void *pointer, int64_t index, uint64_t value) {
    if (!valid_object_kind(pointer, 3)) return;
    TurkeyObject *closure = pointer;
    turkey_object_set((void *)(uintptr_t)closure->slots[1], index, value);
}

/* ------------------------------------------------------------ the outside world
 *
 * The floor `turkey/builtins.py` describes: arguments, two file doors, the
 * error stream and `exit`. Every one of them is written twice -- once there
 * for the Python host and once here -- so the cost of a primitive is paid
 * twice and the set is deliberately small.
 *
 * Bytes, not text, on both file doors: a file is not guaranteed to be
 * well-formed UTF-8 and a `String` is, so the validating constructor stays in
 * the library where `Some` and `None` are in scope. Reading is total only
 * after `turkey_file_can_read` says so, the same predicate-plus-total split
 * `turkey_float_can_parse`/`turkey_float_parse` already uses.
 */

static unsigned char **argument_bytes;
static int64_t *argument_lengths;
static int64_t argument_count;

void turkey_args_set(int64_t count, const unsigned char *const *bytes,
                     const int64_t *lengths) {
    /* Copied out of the host's memory and held outside the Turkey heap. A
       `TurkeyString` per argument would have to stay reachable for the life
       of the program from a root the collector scans, and there is no such
       root; plain bytes need none, and `turkey_args_storage` builds the
       strings on demand. */
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

void *turkey_args_storage(void) {
    RootFrame frame;
    void *roots[1] = {NULL};
    turkey_root_enter(&frame, roots, 1, "turkey_args_storage");
    /* Rooted before the first string is built: every `turkey_string_new` can
       collect, and the array is the only thing holding the strings made
       before it. */
    TurkeyObject *storage = turkey_array_new(argument_count, 0, 8, 6);
    if (storage == NULL) { turkey_root_leave(&frame); return NULL; }
    roots[0] = storage;
    frame.live = 1;
    for (int64_t index = 0; index < argument_count; ++index) {
        TurkeyString *value = turkey_string_new(argument_bytes[index],
                                                argument_lengths[index]);
        if (value == NULL) { turkey_root_leave(&frame); return NULL; }
        storage->slots[index] = (uint64_t)(uintptr_t)value;
    }
    turkey_root_leave(&frame);
    return storage;
}

/* A `TurkeyString` is length-delimited and a path is a C string, so every
   door here needs a NUL-terminated copy. An embedded NUL is rejected rather
   than truncated at: a path that names one file to Turkey and another to the
   operating system is the shape of a directory-traversal bug. */
static char *path_of(TurkeyString *value) {
    if (value == NULL) return NULL;
    if (memchr(value->bytes, '\0', (size_t)value->length) != NULL) {
        turkey_panic("a path cannot contain a NUL byte");
        return NULL;
    }
    char *path = malloc((size_t)value->length + 1);
    if (path == NULL) { turkey_panic("out of memory"); return NULL; }
    memcpy(path, value->bytes, (size_t)value->length);
    path[value->length] = '\0';
    return path;
}

int32_t turkey_file_can_read(TurkeyString *value) {
    char *path = path_of(value);
    if (path == NULL) return 0;
    FILE *handle = fopen(path, "rb");
    free(path);
    if (handle == NULL) return 0;
    fclose(handle);
    return 1;
}

void *turkey_read_file_bytes(TurkeyString *value) {
    char *path = path_of(value);
    if (path == NULL) return NULL;
    FILE *handle = fopen(path, "rb");
    if (handle == NULL) {
        snprintf(panic_buffer, sizeof panic_buffer, "cannot read %s", path);
        free(path);
        turkey_panic(panic_buffer);
        return NULL;
    }
    /* Grown rather than sized by `fseek` first: a pipe or a device has no
       length to ask for, and a regular file can change between the two
       calls. */
    size_t capacity = 4096, length = 0;
    unsigned char *bytes = malloc(capacity);
    if (bytes == NULL) {
        fclose(handle); free(path); turkey_panic("out of memory"); return NULL;
    }
    for (;;) {
        if (length == capacity) {
            size_t grown = capacity * 2;
            unsigned char *bigger = realloc(bytes, grown);
            if (bigger == NULL) {
                free(bytes); fclose(handle); free(path);
                turkey_panic("out of memory");
                return NULL;
            }
            bytes = bigger;
            capacity = grown;
        }
        size_t read = fread(bytes + length, 1, capacity - length, handle);
        length += read;
        if (read == 0) break;
    }
    int failed = ferror(handle);
    fclose(handle);
    if (failed) {
        snprintf(panic_buffer, sizeof panic_buffer, "cannot read %s", path);
        free(bytes); free(path);
        turkey_panic(panic_buffer);
        return NULL;
    }
    free(path);
    TurkeyObject *storage = turkey_array_new((int64_t)length, 0, 1, 2);
    if (storage != NULL && length > 0) memcpy(storage->slots, bytes, length);
    free(bytes);
    return storage;
}

int32_t turkey_write_file_bytes(TurkeyString *value, void *wrapper) {
    /* Answers whether it worked rather than panicking. A failed write is an
       ordinary thing to want to report -- a full disk, a read-only directory
       -- and unlike a failed read there is no predicate that could be asked
       first without lying about the race. */
    TurkeyObject *array;
    int64_t length;
    if (!array_parts(wrapper, &array, &length)) return 0;
    char *path = path_of(value);
    if (path == NULL) return 0;
    FILE *handle = fopen(path, "wb");
    free(path);
    if (handle == NULL) return 0;
    size_t written = length == 0 ? 0
        : fwrite(array->slots, 1, (size_t)length, handle);
    int failed = written != (size_t)length || ferror(handle);
    if (fclose(handle) != 0) failed = 1;
    return failed ? 0 : 1;
}

uint8_t turkey_stderr_write(TurkeyString *value) {
    if (value == NULL) return 0;
    fwrite(value->bytes, 1, (size_t)value->length, stderr);
    fflush(stderr);
    return 0;
}

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

/* -------------------------------------------------------- crash diagnostics
 *
 * A fault in generated code otherwise says nothing at all. The JIT registers
 * no symbols, so the operating system's crash report is a list of unnamed
 * addresses, and a debugger cannot control a hardened interpreter well enough
 * to be attached to one. Meanwhile two shadow stacks that already exist know
 * the answer: `panic_calls` carries the source position of every call that
 * can fail, and the collector's root frames carry the function names. Walking
 * them turns "exited -11, no output" into the Turkey call stack.
 *
 * Opt-in through `TURKEY_SEGV_FRAMES`, the way `TURKEY_GC_STRESS` is, and for
 * the same reason: taking `SIGSEGV` over for a process that is mostly not
 * this runtime is a debugging choice rather than a default.
 */

static void crash_report(int signal_number) {
    /* Async-signal-safe: `write` alone, out of a stack buffer. Nothing here
       allocates, takes a lock, or returns -- the process is already lost, and
       the only job left is to say where from. */
    char line[512];
    const char *header = signal_number == SIGBUS
        ? "\n*** SIGBUS in generated code\n"
        : "\n*** SIGSEGV in generated code\n";
    write(2, header, strlen(header));
    const char *sites = "  innermost call sites:\n";
    write(2, sites, strlen(sites));
    int64_t shown = 0;
    for (PanicCallFrame *frame = panic_calls;
         frame != NULL && shown < 20; frame = frame->previous) {
        const PanicSite *site = frame->site;
        if (site == NULL || site->line == 0) continue;
        int n = snprintf(line, sizeof line, "    %s (%s:%" PRId64 ":%" PRId64 ")\n",
                         site->function ? site->function : "?",
                         site->file ? site->file : "?", site->line, site->col);
        if (n > 0) write(2, line, (size_t)n);
        shown++;
    }
    const char *frames = "  enclosing functions, innermost first:\n";
    write(2, frames, strlen(frames));
    shown = 0;
    for (RootFrame *frame = roots; frame != NULL && shown < 40;
         frame = frame->previous, ++shown) {
        int n = snprintf(line, sizeof line, "    %s\n",
                         frame->function_name ? frame->function_name : "?");
        if (n > 0) write(2, line, (size_t)n);
    }
    _exit(139);
}

void turkey_install_crash_handler(void) {
    signal(SIGSEGV, crash_report);
    signal(SIGBUS, crash_report);
}

/* ------------------------------------------------------------- a real binary
 *
 * The JIT reaches the entry through `ctypes` and reads the panic and exit
 * flags back in Python. A compiled program has no Python, so the same three
 * steps -- hand over the arguments, run, report -- are here instead, and the
 * `main` the code generator emits is a call to this with the entry thunk.
 *
 * In C rather than in generated IR because none of it depends on the program:
 * only the entry's *name* does, and that is the argument.
 */
/* The entry runs on a thread of its own, for its stack.
 *
 * A compiler is a tree walk, and a Turkey frame is not small: `Opt#expr` roots
 * every live pointer it holds in one array, so its frame is kilobytes, and a
 * deeply nested program overruns the 8MB the main thread gets on macOS. The
 * fault is a write to the guard page, which is a `SIGSEGV` like any other and
 * says nothing -- the handler cannot even run, because running it needs the
 * stack that just ran out.
 *
 * A thread takes its stack size as an attribute and the main thread's cannot
 * be changed once the process is running, so the entry goes on a thread made
 * for it. `main` does nothing but wait, so this costs one thread and no
 * concurrency: the collector still sees exactly one mutator.
 */
/* The same 512MB `driver.STACK_BYTES` gives the interpreter, and for the
   same reason: the two hosts should run out of stack in the same place. */
#define TURKEY_STACK_BYTES ((size_t)512 * 1024 * 1024)

static void *entry_thread(void *argument) {
    ((void (*)(void))argument)();
    return NULL;
}

static void run_entry(void (*entry)(void)) {
    pthread_attr_t attributes;
    pthread_t thread;
    if (pthread_attr_init(&attributes) != 0) {
        entry();
        return;
    }
    if (pthread_attr_setstacksize(&attributes, TURKEY_STACK_BYTES) != 0 ||
            pthread_create(&thread, &attributes, entry_thread,
                           (void *)entry) != 0) {
        /* No thread to be had: the small stack beats not running at all. */
        pthread_attr_destroy(&attributes);
        entry();
        return;
    }
    pthread_attr_destroy(&attributes);
    pthread_join(thread, NULL);
}

int turkey_main(int argc, char **argv, void (*entry)(void)) {
    /* Same opt-in as the JIT's: a compiled program is the one that most needs
       the shadow stacks read back, since there is no Python left to read the
       flags. */
    if (getenv("TURKEY_SEGV_FRAMES") != NULL) turkey_install_crash_handler();

    /* `argv + 1`: the program's own arguments, with its name dropped, which is
       what `driver.run` hands the JIT so that the two hosts agree on element
       zero. */
    int count = argc > 0 ? argc - 1 : 0;
    if (count > 0) {
        const unsigned char **bytes =
            malloc((size_t)count * sizeof(unsigned char *));
        int64_t *lengths = malloc((size_t)count * sizeof(int64_t));
        if (bytes == NULL || lengths == NULL) {
            free((void *)bytes);
            free(lengths);
            fputs("turkey: out of memory reading arguments\n", stderr);
            return 1;
        }
        for (int index = 0; index < count; ++index) {
            bytes[index] = (const unsigned char *)argv[index + 1];
            lengths[index] = (int64_t)strlen(argv[index + 1]);
        }
        turkey_args_set(count, bytes, lengths);
        free((void *)bytes);
        free(lengths);
    } else {
        turkey_args_set(0, NULL, NULL);
    }

    run_entry(entry);

    if (turkey_exiting()) {
        int64_t status = turkey_exit_status();
        return (int)(status & 0xff);
    }
    if (turkey_panicked()) {
        const char *message = turkey_panic_message();
        fprintf(stderr, "panic: %s\n", message == NULL ? "" : message);
        int64_t depth = turkey_frame_count();
        for (int64_t index = 0; index < depth; ++index) {
            const char *function = turkey_frame_function(index);
            const char *file = turkey_frame_file(index);
            fprintf(stderr, "  at %s (%s:%" PRId64 ":%" PRId64 ")\n",
                    function == NULL ? "?" : function,
                    file == NULL ? "?" : file,
                    turkey_frame_line(index), turkey_frame_col(index));
        }
        return 1;
    }
    turkey_collect();
    return 0;
}
