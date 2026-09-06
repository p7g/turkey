module Turkey.Llvm (
    emitModule,
)

-- The low IR as LLVM IR text (`NATIVE-BACKEND.md`, M27 phase 2).
--
-- Text rather than a library, which is the difference from `turkey/llvmgen.py`
-- and not a limitation: that one builds `llvmlite` objects and JITs them in
-- process, because it is the *interpreter's* backend and has a Python runtime
-- to hand the result back to. This one is a compiler -- it writes a `.ll` file
-- that `cc` turns into an object and links against `runtime/turkey_runtime.c`
-- -- so the only interface it needs is the one LLVM documents.
--
-- Three things make the text form less work here than it would be in Python.
--
-- **Opaque pointers.** LLVM 15 onwards has one `ptr` type, so the bitcasts
-- `llvmgen.py` emits between object, array, cell and string pointers are gone
-- -- most of them existed only to satisfy a type system this level does not
-- need. What remains is `getelementptr` for the offsets, and those the object
-- layout decides.
--
-- **Quoted symbols.** A Turkey top-level name carries `#`, `@`, `%`, `.` and
-- commas, and LLVM allows any of them inside `@"..."`. So there is no mangling
-- scheme to invent, agree on and later regret: the symbol in the object file
-- is the name in Core, which is what a backtrace and a disassembly should say.
--
-- **Block parameters become phis, and only here.** The IR above this has
-- parameters on blocks, which is the modern choice and the one
-- `NATIVE-BACKEND.md` argues for; LLVM has phis. The conversion is mechanical
-- *because* of an invariant the lowering maintains -- only a `Jump` carries
-- arguments, and a `Branch`'s targets take none -- so a block's phis are read
-- off its predecessors' jumps and nothing has to be split. That invariant is
-- worth stating out loud, because the day a branch carries arguments this
-- becomes the critical-edge problem instead.

import Data.Map as Map
import Data.Set as Set
import Turkey.LowIr as L
import Turkey.LowIr (Op(..), Low(..), BinOp(..), CmpOp(..), Conv(..),
                     Callee(..))
import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..), Term(..),
                   Func(..), Block(..))
import Turkey.SsaLower as SsaLower
import Turkey.SsaLower (Module(..))

-- -- types ---------------------------------------------------------------------------

-- The LLVM type a representation is held in.
--
-- `unit` is `i8` rather than LLVM's `void`: a `void` value cannot be named,
-- passed or phi'd, and this IR gives every instruction a result. One byte that
-- nothing reads is cheaper than a second rule about which results exist.
fun typeOf(r : Rep) -> String = match r.class_ {
    Unit -> "i8"
    I1 -> "i1"
    I8 -> "i8"
    I32 -> "i32"
    I64 -> "i64"
    F64 -> "double"
    Ptr -> "ptr"
}

-- What the runtime's `turkey_box`/`turkey_unbox` and the object metadata call
-- this representation. Must agree with `Turkey.SsaLower.repCode`, which writes
-- the same numbers into an object's header.
fun layoutCode(r : Rep) -> Int = match r.class_ {
    Unit -> 0
    I1 -> 1
    I8 -> 2
    I32 -> 3
    I64 -> 4
    F64 -> 5
    Ptr -> if r.traced { 7 } else { 6 }
}

-- Bytes one array element occupies. The same rule as
-- `turkey/backend_lower._layout_width`, and it has to stay the same: that one
-- decides the width `turkey_array_new` is called with and this one decides the
-- stride the element is then read at.
fun elementWidth(c : RepClass) -> Int = match c {
    I8 -> 1
    I32 -> 4
    _ -> 8
}

fun elementType(c : RepClass) -> String = match elementWidth(c) {
    1 -> "i8"
    4 -> "i32"
    _ -> "i64"
}

-- -- names ---------------------------------------------------------------------------

-- A symbol, quoted. Turkey names contain characters LLVM will not accept bare,
-- and quoting takes all of them -- so the object file's symbol is the compiler's
-- own name for the binding rather than a mangling of it.
fun symbol(name : String) -> String = "@\"" + escaped(name) + "\""

-- LLVM's quoted form takes any byte as `\XX`, and only the quote and the
-- backslash have to be spelled that way. A Turkey name contains neither today;
-- escaping them anyway costs one pass and removes the need to know that.
fun escaped(name : String) -> String =
    String.replace(String.replace(name, "\\", "\\5C"), "\"", "\\22")

fun reg(v : Value) -> String = "%v" + Int.toString(Ssa.valueId(v))

fun label(b : BlockId) -> String = "b" + Int.toString(Ssa.blockId(b))

-- -- the module ----------------------------------------------------------------------

-- What one emission run accumulates besides the text.
type Emit = Emit {
    out      : String.Builder,
    -- String literal -> the index of the pair of globals holding it. Interned
    -- per module rather than per evaluation, which is what lets `ConstString`
    -- be a load and not a safepoint.
    literals : Map String Int,
    -- A counter for the temporaries the emitter itself needs, which are not
    -- IR values and so cannot use their ids.
    next     : Int,
    -- Primitives with no emission rule. Collected rather than raised, so one
    -- run says everything that is missing instead of the first thing.
    missing  : Set.Set String,
    -- The label being emitted into. A guard moves it.
    at       : String,
    -- Panic message -> its global. `turkey_panic` takes a C string, so these
    -- are byte arrays rather than `TurkeyString`s.
    messages : Map String Int,
    -- Value id -> its slot in the current function's root array. Only the
    -- traced values that are live across a safepoint are in it.
    slots    : Map Int Int,
    -- Global name -> its slot in the *permanent* root array. Only the
    -- pointer-shaped globals are in it; the rest are ordinary storage.
    globals  : Map String Int,
}

fun fresh(e : Emit) -> String {
    let n = e.next
    e.next = n + 1
    "%t" + Int.toString(n)
}

fun line(e : Emit, text : String) -> Unit {
    String.push(e.out, text)
    String.push(e.out, "\n")
}

-- The module, and the primitives it could not emit.
fun emitModule(m : Module, entry : String) -> (String, Array String) {
    let e = Emit { out = String.builder(), literals = Map.new(), next = 0,
                   missing = Set.new(), at = "b0", messages = Map.new(),
                   slots = Map.new(), globals = Map.new() }
    line(e, "; Generated by the Turkey bootstrap compiler.")
    line(e, "")
    declareRuntime(e)
    line(e, "")
    -- Every string literal in the program, found before anything is emitted
    -- so that a `ConstString` can be a load of an already-decided global.
    collectLiterals(m, e.literals)
    line(e, "")
    -- A pointer global lives *in* the collector's permanent root array, so
    -- storing to it and rooting it are the same store. That is what makes
    -- every dictionary survive: they are globals, the module initializer
    -- computes them once, and nothing else refers to them from a stack frame
    -- for the rest of the run.
    --
    -- A scalar global holds nothing to trace and stays ordinary storage,
    -- which also keeps the array dense -- its length is the number of things
    -- the collector must scan, not the number of globals.
    var roots = 0
    for (name, r) in m.globals {
        if r.traced {
            Map.put(e.globals, name, roots)
            roots = roots + 1
        } else {
            line(e, symbol("global." + name) + " = internal global "
                    + typeOf(r) + " " + zeroOf(r))
        }
    }
    -- The interned string literals, in the same array and for the same
    -- reason: each cache holds a `TurkeyString` the runtime allocated, and a
    -- collector that cannot see it frees every literal in the program the
    -- first time it runs. They are named `%literal.N` rather than given their
    -- own array so that one frame covers everything that lives for the whole
    -- run.
    for (text, index) in Map.entries(e.literals) {
        Map.put(e.globals, "%literal." + Int.toString(index), roots)
        roots = roots + 1
    }
    for (text, index) in Map.entries(e.literals) {
        emitLiteral(e, text, index)
    }
    line(e, "@.turkey.global.roots = internal global ["
            + Int.toString(roots) + " x ptr] zeroinitializer")
    line(e, "@.turkey.global.frame = internal global [5 x i64] zeroinitializer")
    line(e, "")
    for f in m.functions { emitFunc(e, f) }
    emitEntry(e, m, entry)
    -- Last, because a panic message is discovered by emitting the code that
    -- mentions it -- and the entry sequence mentions one too, naming the
    -- frame that holds the globals. LLVM does not care about declaration
    -- order within a module, and having this *before* the entry meant the
    -- entry's own message was requested after the table had been written.
    line(e, "")
    for (text, index) in Map.entries(e.messages) {
        line(e, messageName(index) + " = private unnamed_addr constant "
                + cString(text))
    }
    (String.build(e.out), Array.sort(Set.elements(e.missing)))
}

-- The global holding a panic message, interned per module.
fun messageGlobal(e : Emit, text : String) -> String {
    if !Map.contains(e.messages, text) {
        Map.put(e.messages, text, len(e.messages))
    }
    messageName(Map.getOr(e.messages, text, 0))
}

fun messageName(index : Int) -> String =
    "@.turkey.message." + Int.toString(index)

-- A NUL-terminated byte array, which is what a C `const char *` points at.
fun cString(text : String) -> String {
    let bytes = String.toBytes(text)
    let b = String.builder()
    String.push(b, "[" + Int.toString(len(bytes) + 1) + " x i8] c\"")
    for byte in bytes { String.push(b, byteEscape(Byte.toInt(byte))) }
    String.push(b, "\\00\"")
    String.build(b)
}

-- A zero of the given type, as an instruction, so a value that could not be
-- emitted still has the type its `Rep` promised. The module is refused anyway;
-- this only keeps the failure to one message rather than a cascade of type
-- errors from `cc`.
fun zeroInstruction(ty : String, r : Rep) -> String = match r.class_ {
    F64 -> "fadd double 0.0, 0.0"
    Ptr -> "inttoptr i64 0 to ptr"
    _ -> "add " + ty + " 0, 0"
}

fun zeroOf(r : Rep) -> String = match r.class_ {
    F64 -> "0.0"
    Ptr -> "null"
    _ -> "0"
}

-- -- the runtime ---------------------------------------------------------------------

-- Everything in `runtime/turkey_runtime.h` this emitter calls.
--
-- Declared rather than defined: `cc` links the object this becomes against the
-- runtime's own translation unit, so these are the same symbols the JIT looks
-- up in its own process. One header, two backends, and a disagreement about a
-- signature is a link error rather than a wrong call.
fun declareRuntime(e : Emit) -> Unit {
    for d in [
        "declare ptr @turkey_string_new(ptr, i64)",
        "declare ptr @turkey_string_concat(ptr, ptr)",
        "declare ptr @turkey_int_to_string(i64)",
        "declare ptr @turkey_float_to_string(double)",
        "declare double @turkey_float_parse(ptr)",
        "declare i32 @turkey_float_can_parse(ptr)",
        "declare double @turkey_float_fmod(double, double)",
        "declare double @turkey_float_remainder(double, double)",
        "declare double @turkey_float_floor(double)",
        "declare double @turkey_float_ceil(double)",
        "declare double @turkey_float_round(double)",
        "declare double @turkey_float_trunc(double)",
        "declare ptr @turkey_char_to_string(i32)",
        "declare i64 @turkey_string_byte_length(ptr)",
        "declare i8 @turkey_string_byte_at(ptr, i64)",
        "declare i32 @turkey_string_decode_at(ptr, i64)",
        "declare i64 @turkey_string_next_index(ptr, i64)",
        "declare ptr @turkey_string_slice(ptr, i64, i64)",
        "declare i64 @turkey_string_find(ptr, ptr, i64)",
        "declare i64 @turkey_string_rfind(ptr, ptr)",
        "declare ptr @turkey_string_to_byte_storage(ptr)",
        "declare ptr @turkey_string_from_bytes(ptr)",
        "declare i32 @turkey_string_is_valid_utf8(ptr)",
        "declare ptr @turkey_string_concat_all(ptr)",
        "declare i32 @turkey_string_eq(ptr, ptr)",
        "declare i32 @turkey_string_lt(ptr, ptr)",
        "declare ptr @turkey_args_storage()",
        "declare i32 @turkey_file_can_read(ptr)",
        "declare ptr @turkey_read_file_bytes(ptr)",
        "declare i32 @turkey_write_file_bytes(ptr, ptr)",
        "declare i8 @turkey_stderr_write(ptr)",
        "declare void @turkey_exit(i64)",
        "declare i8 @turkey_print(ptr)",
        "declare i8 @turkey_write(ptr)",
        "declare ptr @turkey_cell_new(i64, i32)",
        "declare ptr @turkey_object_new(i32, i32, i64, i64)",
        "declare void @turkey_object_set(ptr, i64, i64)",
        "declare ptr @turkey_box(i64, i32)",
        "declare i64 @turkey_unbox(ptr, i32)",
        "declare ptr @turkey_array_new(i64, i64, i32, i32)",
        "declare ptr @turkey_closure_new(i64, i64, i64)",
        "declare void @turkey_closure_capture(ptr, i64, i64)",
        "declare void @turkey_panic(ptr)",
        "declare void @turkey_panic_string(ptr)",
        "declare i32 @turkey_panicked()",
        "declare i32 @turkey_main(i32, ptr, ptr)",
        "declare void @turkey_root_enter(ptr, ptr, i64, ptr)",
        "declare void @turkey_root_leave(ptr)",
        "@turkey_has_panicked = external global i32",
        "declare {i64, i1} @llvm.sadd.with.overflow.i64(i64, i64)",
        "declare {i64, i1} @llvm.ssub.with.overflow.i64(i64, i64)",
        "declare {i64, i1} @llvm.smul.with.overflow.i64(i64, i64)",
        "declare void @llvm.memset.p0.i64(ptr, i8, i64, i1)",
    ] { line(e, d) }
}

-- -- string literals -----------------------------------------------------------------

fun collectLiterals(m : Module, out : Map String Int) -> Unit {
    for f in m.functions {
        for block in f.blocks {
            for i in block.insts {
                match i.op {
                    ConstString(text) -> if !Map.contains(out, text) {
                        Map.put(out, text, len(out))
                    }
                    _ -> {}
                }
            }
        }
    }
}

-- A literal is two globals: the bytes, and a cache for the `TurkeyString` the
-- runtime makes out of them.
--
-- The cache is why `ConstString` is a load rather than an allocation, and why
-- the instruction is not a safepoint: the string is built once, in the entry
-- sequence, and every evaluation after that reads the same pointer. It is also
-- what makes two occurrences of one literal one object, which the language
-- does not promise but nothing can observe the absence of, there being no
-- object identity (FINDINGS 10).
fun emitLiteral(e : Emit, text : String, index : Int) -> Unit {
    let bytes = String.toBytes(text)
    let n = len(bytes)
    let b = String.builder()
    String.push(b, "[" + Int.toString(n + 1) + " x i8] c\"")
    for byte in bytes { String.push(b, byteEscape(Byte.toInt(byte))) }
    String.push(b, "\\00\"")
    line(e, literalBytes(index) + " = private unnamed_addr constant "
            + String.build(b))
}

fun literalBytes(index : Int) -> String =
    "@.turkey.literal.bytes." + Int.toString(index)

fun literalValue(e : Emit, index : Int) -> String =
    globalAddress(e, "%literal." + Int.toString(index))

-- One byte of an LLVM `c"..."` constant.
--
-- Printable ASCII goes through as itself, which costs one comparison and
-- makes the emitted module *readable*: a panic message and a string literal
-- are the two things anyone reading generated IR wants to recognise, and
-- escaping every byte turned both into hex. Everything else is escaped,
-- including the quote and the backslash that LLVM requires and the NUL that
-- terminates the array.
fun byteEscape(n : Int) -> String =
    if n >= 32 && n < 127 && n != 34 && n != 92 {
        String.fromCodePoints([codePoint(n)])
    } else {
        "\\" + hexDigit(n / 16) + hexDigit(n % 16)
    }

fun codePoint(n : Int) -> Char = match Char.fromInt(n) {
    Some(c) -> c
    None -> '?'
}

-- Spelled out rather than computed from a code point: `Char.fromInt` answers
-- an `Option`, and unwrapping one per nibble to reach a table this size is
-- more code than the table.
fun hexDigit(n : Int) -> String = match n {
    10 -> "A"
    11 -> "B"
    12 -> "C"
    13 -> "D"
    14 -> "E"
    15 -> "F"
    _ -> Int.toString(n)
}

-- -- functions -----------------------------------------------------------------------

-- One function, in two passes over its blocks.
--
-- Two, because a guard *splits* a block: an overflow check, a division by
-- zero, and the panic flag after a call each end the block they are in and
-- continue in a new one. So the label a block's terminator sits in is not the
-- label it started with -- and a successor's phi has to name the label control
-- actually left from. The bodies are therefore emitted first, recording where
-- each block ended, and the phis are written afterwards, when every
-- predecessor's exit is known.
--
-- The alternative -- counting guards ahead of time to predict the exit label
-- -- would put one rule in two places that must agree, which is the shape of
-- mistake this compiler keeps finding in itself.
fun emitFunc(e : Emit, f : Func Low) -> Unit {
    let params = []
    for p in f.params {
        Array.push(params, typeOf(Ssa.repOf(f, p)) + " " + reg(p))
    }
    let incoming = incomingOf(f)
    let live = Ssa.liveness(f)
    e.slots = rootSlots(f, live)
    let bodies = []
    let exits = []
    let outer = e.out
    for var b = 0; b < len(f.blocks); b = b + 1 {
        e.out = String.builder()
        e.at = label(BlockId(b))
        let after = Ssa.liveAfter(f, BlockId(b), live[b])
        let insts = Ssa.blockAt(f, BlockId(b)).insts
        for var n = 0; n < len(insts); n = n + 1 {
            -- The roots are published *before* the call, naming what must
            -- survive it, which is what is live after it.
            if isSafepoint(insts[n].op) { publish(e, f, after[n], insts[n]) }
            emitInst(e, f, insts[n])
        }
        Array.push(exits, e.at)
        emitTerm(e, f, Ssa.blockAt(f, BlockId(b)).term)
        Array.push(bodies, String.build(e.out))
    }
    e.out = outer
    line(e, "define internal " + typeOf(f.result) + " " + symbol(f.name)
            + "(" + String.join(params, ", ") + ") {")
    for var b = 0; b < len(f.blocks); b = b + 1 {
        line(e, label(BlockId(b)) + ":")
        -- The frame is allocated in the entry block, before anything, because
        -- an `alloca` anywhere else is a dynamic stack allocation.
        if b == Ssa.blockId(f.entry) { enterFrame(e, f) }
        emitPhis(e, f, BlockId(b), incoming, exits)
        String.push(e.out, bodies[b])
    }
    line(e, "}")
    line(e, "")
}

-- -- garbage collection roots -----------------------------------------------------------

-- Which traced values need a slot, and which slot each gets.
--
-- Only the ones live *across* a safepoint. A pointer that is dead by the time
-- a call happens does not have to survive it, and a pointer never live across
-- one is only ever in a register the collector does not need to see -- so the
-- set is usually a small fraction of a function's pointers, and it is the
-- difference between a root array the size of the live set and one the size of
-- the function (FINDINGS 55).
fun rootSlots(f : Func Low, live : Array (Set.Set Int)) -> Map Int Int {
    let needed = Set.new()
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let after = Ssa.liveAfter(f, BlockId(b), live[b])
        let insts = Ssa.blockAt(f, BlockId(b)).insts
        for var n = 0; n < len(insts); n = n + 1 {
            if isSafepoint(insts[n].op) {
                for id in Set.elements(across(f, after[n], insts[n])) {
                    Set.add(needed, id)
                }
            }
        }
    }
    let out = Map.new()
    -- Sorted, so the slot a value gets does not depend on a set's iteration
    -- order and two runs produce the same module.
    let ids = Array.sort(Set.elements(needed))
    for var i = 0; i < len(ids); i = i + 1 { Map.put(out, ids[i], i) }
    out
}

-- The traced values live *across* a safepoint, which is not the same as those
-- live after it.
--
-- An argument whose last use is this very call is dead afterwards and is still
-- needed *during* it: the callee holds it, and the collector may run while it
-- does. Rooting only what survives the call freed `Int.toString`'s result
-- while `turkey_string_concat` was reading it, and "circle of 5" came out
-- "circle of c".
--
-- The result goes the other way and must *not* be rooted: it does not exist
-- until the collection is over, so storing it would be a use before its
-- definition -- not merely useless but not SSA, which clang answers with a
-- segfault rather than a diagnosis.
--
-- Both halves are stated in `turkey/llvmgen._safepoint_live`'s comment, which
-- is where this should have been read from rather than reasoned out.
--
-- One function, because the slot assignment and the publish must agree about
-- exactly this set: a value in one and not the other is either a root with no
-- slot or a slot never written.
fun across(f : Func Low, live : Set.Set Int, i : Low) -> Set.Set Int {
    let out = Set.new()
    for id in Set.elements(live) {
        if id != Ssa.valueId(i.result) && traced(f, id) { Set.add(out, id) }
    }
    for v in Ssa.uses(i) {
        let id = Ssa.valueId(v)
        if id != Ssa.valueId(i.result) && traced(f, id) { Set.add(out, id) }
    }
    out
}

fun traced(f : Func Low, id : Int) -> Bool =
    if id < 0 || id >= len(f.reps) { False } else { f.reps[id].traced }

-- An operation the collector may run inside.
--
-- Every call, and every allocation. Being wrong in the *inclusive* direction
-- costs a store and a mask write; being wrong the other way frees a live
-- object under the program's feet, so a call that cannot itself allocate is
-- listed anyway -- it may call one that can.
fun isSafepoint(op : Op) -> Bool = match op {
    Call(_, _) -> True
    ObjectNew(_, _, _, _) -> True
    ArrayNew(_, _, _) -> True
    CellNew(_) -> True
    ClosureNew(_, _, _) -> True
    ClosureCapture(_, _, _) -> True
    Box(_) -> True
    Unbox(_) -> True
    ConstString(_) -> False
    _ -> False
}

-- The root frame, in the entry block.
--
-- `RootFrame` is `{ ptr previous, ptr name, i64 count, ptr values, i64 live }`
-- and the runtime fills every field but `live`, which each safepoint sets for
-- itself. Nothing is live at registration, so a collection between here and
-- the first safepoint scans nothing -- which is correct, and is why the array
-- does not have to be zeroed.
fun enterFrame(e : Emit, f : Func Low) -> Unit {
    if len(e.slots) == 0 { return }
    line(e, "  %frame = alloca [5 x i64]")
    line(e, "  %roots = alloca [" + Int.toString(len(e.slots)) + " x ptr]")
    -- Past 64 slots the live mask runs out of bits, and the runtime scans
    -- every one of those slots unconditionally -- so they have to start as
    -- null rather than as whatever the stack held. `Main#main` in
    -- `question.tl` has more than 64 and read a `0x1` out of slot 64, which is
    -- what named this.
    --
    -- Only past 64: below it the mask says which slots are meaningful, so
    -- zeroing them all would be a memset per call for nothing.
    if len(e.slots) > 64 {
        line(e, "  call void @llvm.memset.p0.i64(ptr %roots, i8 0, i64 "
                + Int.toString(len(e.slots) * 8) + ", i1 false)")
    }
    line(e, "  call void @turkey_root_enter(ptr %frame, ptr %roots, i64 "
            + Int.toString(len(e.slots)) + ", ptr "
            + messageGlobal(e, f.name) + ")")
}

-- Publish the live root set before a safepoint.
--
-- The mask is an immediate: a root set is a property of a *program point*, not
-- of a function, so unioning the live sets over the whole function would make
-- every path pay for the ones it never takes. The runtime's `live` field is
-- exactly this, and the comment on it in `turkey_runtime.c` is the measurement
-- that put it there.
fun publish(e : Emit, f : Func Low, live : Set.Set Int, i : Low) -> Unit {
    if len(e.slots) == 0 { return }
    var mask = 0
    let stored = []
    for id in Array.sort(Set.elements(across(f, live, i))) {
        match Map.get(e.slots, id) {
            Some(slot) -> {
                Array.push(stored, (slot, id))
                -- `Int.or`, not `+`: bit 63 is the sign bit of a signed
                -- 64-bit integer, and adding it to a mask that already has
                -- bits set overflows -- which now panics.
                if slot < 64 { mask = Int.or(mask, Int.shl(1, slot)) }
            }
            None -> {}
        }
    }
    for (slot, id) in stored {
        let address = fresh(e)
        line(e, "  " + address + " = getelementptr inbounds ptr, ptr %roots,"
                + " i64 " + Int.toString(slot))
        line(e, "  store ptr %v" + Int.toString(id) + ", ptr " + address)
    }
    let maskAddress = fresh(e)
    line(e, "  " + maskAddress + " = getelementptr inbounds i8, ptr %frame,"
            + " i64 32")
    line(e, "  store i64 " + Int.toString(mask) + ", ptr " + maskAddress)
}

-- Every path out of a function pops its frame. A `ret` that forgot would leave
-- the collector walking a frame whose stack storage has been reused.
fun leaveFrame(e : Emit) -> Unit {
    if len(e.slots) == 0 { return }
    line(e, "  call void @turkey_root_leave(ptr %frame)")
}

-- A guard: if `condition` holds, panic with `message` and leave.
--
-- The failure arm returns rather than branching to a shared exit, which is
-- what `turkey/llvmgen.py` does and for a reason worth keeping: the arm that
-- carries on must not merge with anything, or LLVM has to assume the failing
-- path could come back and redo every load across the merge. An empty
-- `message` means the panic already happened -- the flag is being propagated
-- out of a callee -- and nothing new is raised.
fun guard(e : Emit, f : Func Low, condition : String,
          message : String) -> Unit {
    let failed = blockName(e)
    let carry = blockName(e)
    line(e, "  br i1 " + condition + ", label %" + failed
            + ", label %" + carry)
    line(e, failed + ":")
    if message != "" {
        line(e, "  call void @turkey_panic(ptr " + messageGlobal(e, message)
                + ")")
    }
    leaveFrame(e)
    line(e, "  ret " + typeOf(f.result) + " " + zeroOf(f.result))
    line(e, carry + ":")
    e.at = carry
}

-- After a call: if the callee panicked, leave.
--
-- `turkey_panic` sets a flag and returns -- there is no unwinding -- so a
-- caller that did not look would carry on with a value the callee never
-- produced. Every call needs this, which is why the flag is read as a plain
-- global load rather than through `turkey_panicked()`.
fun propagate(e : Emit, f : Func Low) -> Unit {
    let flag = fresh(e)
    let panicked = fresh(e)
    line(e, "  " + flag + " = load i32, ptr @turkey_has_panicked")
    line(e, "  " + panicked + " = icmp ne i32 " + flag + ", 0")
    guard(e, f, panicked, "")
}

fun blockName(e : Emit) -> String {
    let n = e.next
    e.next = n + 1
    "g" + Int.toString(n)
}

-- Block -> its parameters' incoming values, one array per predecessor.
--
-- Only a `Jump` carries arguments. A `Branch`'s targets take none, which the
-- lowering guarantees and the verifier could be taught to insist on -- and
-- which is the whole reason no edge has to be split here.
fun incomingOf(f : Func Low) -> Map Int (Array (Int, Array Value)) {
    let out = Map.new()
    for var b = 0; b < len(f.blocks); b = b + 1 {
        match f.blocks[b].term {
            Jump(target, args) -> {
                let key = Ssa.blockId(target)
                let found = Map.getOr(out, key, [])
                Array.push(found, (b, args))
                Map.put(out, key, found)
            }
            Branch(_, yes, no) -> {
                for target in [yes, no] {
                    let key = Ssa.blockId(target)
                    let found = Map.getOr(out, key, [])
                    Array.push(found, (b, []))
                    Map.put(out, key, found)
                }
            }
            _ -> {}
        }
    }
    out
}

fun emitPhis(e : Emit, f : Func Low, at : BlockId,
             incoming : Map Int (Array (Int, Array Value)),
             exits : Array String) -> Unit {
    let block = Ssa.blockAt(f, at)
    let sources = Map.getOr(incoming, Ssa.blockId(at), [])
    for var p = 0; p < len(block.params); p = p + 1 {
        let arms = []
        for (from, args) in sources {
            if p < len(args) {
                -- Where control *left* the predecessor, which is not where it
                -- entered if a guard split the block.
                Array.push(arms, "[ " + reg(args[p]) + ", %" + exits[from]
                                 + " ]")
            }
        }
        let r = Ssa.repOf(f, block.params[p])
        -- A parameter with no incoming edge belongs to an unreachable block.
        -- `Ssa.verify` deliberately says nothing about those, so the emitter
        -- has to give LLVM something well formed: undef is exactly the claim
        -- that nothing reaches here.
        if len(arms) == 0 {
            line(e, "  " + reg(block.params[p]) + " = phi " + typeOf(r)
                    + " [ " + undefOf(r) + ", %" + label(at) + " ]")
        } else {
            line(e, "  " + reg(block.params[p]) + " = phi " + typeOf(r) + " "
                    + String.join(arms, ", "))
        }
    }
}

fun undefOf(r : Rep) -> String = "undef"

fun emitTerm(e : Emit, f : Func Low, t : Term) -> Unit = match t {
    Ret(v) -> {
        leaveFrame(e)
        line(e, "  ret " + typeOf(Ssa.repOf(f, v)) + " " + reg(v))
    }
    Jump(target, _) -> line(e, "  br label %" + label(target))
    Branch(c, yes, no) ->
        line(e, "  br i1 " + reg(c) + ", label %" + label(yes)
                + ", label %" + label(no))
    Panic(v) -> {
        line(e, "  call void @turkey_panic_string(ptr " + reg(v) + ")")
        leaveFrame(e)
        line(e, "  ret " + typeOf(f.result) + " " + zeroOf(f.result))
    }
    -- Not `unreachable`: this is reached only where the lowering proved a
    -- value never arrives, and an LLVM `unreachable` licenses the optimizer to
    -- assume the path is impossible -- which is true, and which turns any
    -- mistake in that proof into miscompiled surrounding code rather than a
    -- crash. A return of zero is the same claim, made unexploitably.
    Unreachable -> {
        leaveFrame(e)
        line(e, "  ret " + typeOf(f.result) + " " + zeroOf(f.result))
    }
}

-- -- instructions ---------------------------------------------------------------------

-- The object header, which every heap access offsets from:
--
--     { i32 kind, i32 tag, i64 count, i64 pointer_bitmap, [0 x i64] slots }
--
-- Stated here as byte offsets rather than as a named struct type, because with
-- opaque pointers a `getelementptr` on `i8` is the whole of what an offset is
-- and a struct type would only be a second place to keep the layout. The
-- runtime's `TurkeyObject` is the first, and the two must agree.
let slotsOffset = 24
let countOffset = 8
let tagOffset = 4

fun emitInst(e : Emit, f : Func Low, i : Low) -> Unit {
    let r = Ssa.repOf(f, i.result)
    let ty = typeOf(r)
    let dst = reg(i.result)
    match i.op {
        ConstInt(n) -> line(e, "  " + dst + " = add " + ty + " 0, "
                               + Int.toString(n))
        -- Through the bit pattern rather than through a decimal spelling:
        -- LLVM accepts a hexadecimal double exactly, and a round trip through
        -- shortest-representation printing is a place for a value to change.
        ConstFloat(x) -> line(e, "  " + dst + " = fadd double 0.0, "
                                 + doubleLiteral(x))
        ConstString(text) -> line(e, "  " + dst + " = load ptr, ptr "
                                     + literalValue(e, literalIndex(e, text)))
        ConstUnit -> line(e, "  " + dst + " = add i8 0, 0")
        Bin(o, a, b) -> emitBin(e, f, dst, ty, r, o, a, b)
        Cmp(o, a, b) -> {
            let operand = Ssa.repOf(f, a)
            line(e, "  " + dst + " = " + cmpOpName(o, operand) + " "
                    + typeOf(operand) + " " + reg(a) + ", " + reg(b))
        }
        Neg(a) -> if r.class_ == F64 {
            line(e, "  " + dst + " = fneg double " + reg(a))
        } else if r.class_ == I64 {
            checked(e, f, dst, "ssub", "0", reg(a), "integer overflow in unary -")
        } else {
            line(e, "  " + dst + " = sub " + ty + " 0, " + reg(a))
        }
        Not(a) -> line(e, "  " + dst + " = xor i1 " + reg(a) + ", true")
        Convert(c, a) -> emitConvert(e, f, dst, ty, c, a, r)
        Box(a) -> {
            let held = Ssa.repOf(f, a)
            let raw = widened(e, f, a, held)
            line(e, "  " + dst + " = call ptr @turkey_box(i64 " + raw + ", i32 "
                    + Int.toString(layoutCode(held)) + ")")
        }
        Unbox(a) -> {
            let raw = fresh(e)
            line(e, "  " + raw + " = call i64 @turkey_unbox(ptr " + reg(a)
                    + ", i32 " + Int.toString(layoutCode(r)) + ")")
            narrow(e, dst, ty, raw, r)
        }
        ObjectNew(kind, tag, count, bitmap) ->
            line(e, "  " + dst + " = call ptr @turkey_object_new(i32 "
                    + Int.toString(kind) + ", i32 " + Int.toString(tag)
                    + ", i64 " + Int.toString(count) + ", i64 "
                    + Int.toString(bitmap) + ")")
        ObjectGet(target, index) -> {
            let address = slotAddress(e, reg(target), Int.toString(index))
            let raw = fresh(e)
            line(e, "  " + raw + " = load i64, ptr " + address)
            narrow(e, dst, ty, raw, r)
        }
        ObjectSet(target, index, v) -> {
            let held = Ssa.repOf(f, v)
            let raw = widened(e, f, v, held)
            line(e, "  call void @turkey_object_set(ptr " + reg(target)
                    + ", i64 " + Int.toString(index) + ", i64 " + raw + ")")
            line(e, "  " + dst + " = add i8 0, 0")
        }
        -- The tag is `i32` at offset 4, which is what a pattern test compares.
        ObjectTag(target) -> {
            let address = fresh(e)
            line(e, "  " + address + " = getelementptr inbounds i8, ptr "
                    + reg(target) + ", i64 " + Int.toString(tagOffset))
            line(e, "  " + dst + " = load i32, ptr " + address)
        }
        ArrayNew(length, initial, element) -> {
            let held = Ssa.repOf(f, initial)
            let raw = widened(e, f, initial, held)
            line(e, "  " + dst + " = call ptr @turkey_array_new(i64 "
                    + reg(length) + ", i64 " + raw + ", i32 "
                    + Int.toString(elementWidth(element)) + ", i32 "
                    + Int.toString(layoutCode(Ssa.untraced(element))) + ")")
        }
        ArrayGet(target, index) -> {
            let element = elementType(r.class_)
            let address = elementAddress(e, reg(target), reg(index), r.class_)
            if elementWidth(r.class_) == 8 {
                let raw = fresh(e)
                line(e, "  " + raw + " = load i64, ptr " + address)
                narrow(e, dst, ty, raw, r)
            } else {
                line(e, "  " + dst + " = load " + element + ", ptr " + address)
            }
        }
        ArraySet(target, index, v) -> {
            let held = Ssa.repOf(f, v)
            let address = elementAddress(e, reg(target), reg(index),
                                         held.class_)
            if elementWidth(held.class_) == 8 {
                let raw = widened(e, f, v, held)
                line(e, "  store i64 " + raw + ", ptr " + address)
            } else {
                line(e, "  store " + elementType(held.class_) + " " + reg(v)
                        + ", ptr " + address)
            }
            line(e, "  " + dst + " = add i8 0, 0")
        }
        -- An array is kind 2: its `count` is the length.
        ArrayLength(target) -> {
            let address = fresh(e)
            line(e, "  " + address + " = getelementptr inbounds i8, ptr "
                    + reg(target) + ", i64 " + Int.toString(countOffset))
            line(e, "  " + dst + " = load i64, ptr " + address)
        }
        -- A cell is `{ i64 value, i32 pointer_value }`, and the flag says
        -- whether the collector follows the word.
        CellNew(v) -> {
            let held = Ssa.repOf(f, v)
            let raw = widened(e, f, v, held)
            line(e, "  " + dst + " = call ptr @turkey_cell_new(i64 " + raw
                    + ", i32 " + (if held.traced { "1" } else { "0" }) + ")")
        }
        CellLoad(target) -> {
            let raw = fresh(e)
            line(e, "  " + raw + " = load i64, ptr " + reg(target))
            narrow(e, dst, ty, raw, r)
        }
        CellStore(target, v) -> {
            let held = Ssa.repOf(f, v)
            let raw = widened(e, f, v, held)
            line(e, "  store i64 " + raw + ", ptr " + reg(target))
            line(e, "  " + dst + " = add i8 0, 0")
        }
        -- The code operand is the function's *address* as an integer, which is
        -- what the runtime stores and what a closure call loads back.
        ClosureNew(code, count, bitmap) -> {
            let address = fresh(e)
            line(e, "  " + address + " = ptrtoint ptr " + symbol(code)
                    + " to i64")
            line(e, "  " + dst + " = call ptr @turkey_closure_new(i64 "
                    + address + ", i64 " + Int.toString(count) + ", i64 "
                    + Int.toString(bitmap) + ")")
        }
        ClosureCapture(closure, index, v) -> {
            let held = Ssa.repOf(f, v)
            let raw = widened(e, f, v, held)
            line(e, "  call void @turkey_closure_capture(ptr " + reg(closure)
                    + ", i64 " + Int.toString(index) + ", i64 " + raw + ")")
            line(e, "  " + dst + " = add i8 0, 0")
        }
        Call(callee, args) -> {
            emitCall(e, f, dst, ty, r, callee, args)
            -- Every call, without exception. A callee that cannot panic
            -- itself may call one that can, and being wrong in the inclusive
            -- direction costs a load and a never-taken branch.
            propagate(e, f)
        }
        GlobalLoad(name) ->
            line(e, "  " + dst + " = load " + ty + ", ptr "
                    + globalAddress(e, name))
        GlobalStore(name, v) -> {
            line(e, "  store " + typeOf(Ssa.repOf(f, v)) + " " + reg(v)
                    + ", ptr " + globalAddress(e, name))
            line(e, "  " + dst + " = add i8 0, 0")
        }
        -- Nothing emits these yet: the lowering gives a `var` a cell, and a
        -- genuine stack slot only appears when the register allocator wants
        -- one. Saying so beats emitting something plausible.
        SlotLoad(_) -> line(e, "  " + dst + " = add i8 0, 0 ; unimplemented slot")
        SlotStore(_, _) -> line(e, "  " + dst + " = add i8 0, 0 ; unimplemented slot")
    }
}

-- The LLVM opcode for an arithmetic operation at this representation.
--
-- Integer and floating point are different instructions in LLVM and one
-- constructor here, which is right: `Add` is what the *language* means, and
-- which machine instruction implements it is a fact about the operands. The
-- same choice `Cmp` makes.
--
-- Signed division and remainder, because Turkey's `Int` is signed. Shifts are
-- logical right (`lshr`) to match `Prim.intShr`, and `ShrU` is spelled the same
-- way -- the distinction the constructor draws is not one this target makes,
-- and saying so beats emitting an arithmetic shift nothing asked for.
fun binOpName(o : BinOp, r : Rep) -> String {
    let float = r.class_ == F64
    match o {
        Add -> if float { "fadd" } else { "add" }
        Sub -> if float { "fsub" } else { "sub" }
        Mul -> if float { "fmul" } else { "mul" }
        Div -> if float { "fdiv" } else { "sdiv" }
        Rem -> if float { "frem" } else { "srem" }
        And -> "and"
        Or -> "or"
        Xor -> "xor"
        Shl -> "shl"
        Shr -> "lshr"
        ShrU -> "lshr"
        AddWrap -> if float { "fadd" } else { "add" }
        SubWrap -> if float { "fsub" } else { "sub" }
        MulWrap -> if float { "fmul" } else { "mul" }
    }
}

-- `ordered` for floats: a comparison with a NaN is false rather than a trap,
-- which is what every language this one resembles means by `<`.
-- Signedness is decided by the *width*, and `i64` is the only signed one --
-- because `Int` is the only signed type Turkey has. `Bool` is `i1`, `Byte` is
-- `i8` and `Char` is `i32`, and all three are ordered as magnitudes.
--
-- Getting this wrong is not subtle in its effect and is very subtle to read: a
-- signed one-bit integer holds 0 and -1, so `False < True` compiles to
-- `icmp slt i1 0, -1` and answers *false*. That is what it did, and the
-- corpus's `operators.tl` is what said so. `turkey/llvmgen.py` reaches the
-- same rule from the other end, by listing which primitives are unsigned.
fun cmpOpName(o : CmpOp, operand : Rep) -> String {
    let float = operand.class_ == F64
    let signed = operand.class_ == I64
    match o {
        Eq -> if float { "fcmp oeq" } else { "icmp eq" }
        Ne -> if float { "fcmp one" } else { "icmp ne" }
        Lt -> if float { "fcmp olt" }
              else if signed { "icmp slt" } else { "icmp ult" }
        Le -> if float { "fcmp ole" }
              else if signed { "icmp sle" } else { "icmp ule" }
        Gt -> if float { "fcmp ogt" }
              else if signed { "icmp sgt" } else { "icmp ugt" }
        Ge -> if float { "fcmp oge" }
              else if signed { "icmp sge" } else { "icmp uge" }
    }
}

-- Where a global's value is kept: a slot of the permanent root array if the
-- collector must scan it, its own storage otherwise.
--
-- A constant expression rather than an emitted `getelementptr`, so that this
-- is usable from anywhere without needing a block to put an instruction in.
fun globalAddress(e : Emit, name : String) -> String =
    match Map.get(e.globals, name) {
        Some(slot) -> "getelementptr inbounds ([0 x ptr], ptr "
                      + "@.turkey.global.roots, i64 0, i64 "
                      + Int.toString(slot) + ")"
        None -> symbol("global." + name)
    }

fun literalIndex(e : Emit, text : String) -> Int = Map.getOr(e.literals, text, 0)

-- A double as its exact bit pattern.
--
-- LLVM reads `0x` followed by sixteen hex digits as the IEEE 754 encoding, so
-- this is the one spelling that cannot lose anything. A decimal round trip
-- through shortest-representation printing and back is where a constant
-- silently becomes a different constant, and the compiler that prints it is
-- not the one that reads it.
fun doubleLiteral(x : Float) -> String {
    let bits = Float.bits(x)
    let b = String.builder()
    String.push(b, "0x")
    for var shift = 60; shift >= 0; shift = shift - 4 {
        String.push(b, hexDigit(Int.and(Int.shr(bits, shift), 15)))
    }
    String.build(b)
}

-- The address of object slot `index`.
fun slotAddress(e : Emit, object : String, index : String) -> String {
    let base = fresh(e)
    line(e, "  " + base + " = getelementptr inbounds i8, ptr " + object
            + ", i64 " + Int.toString(slotsOffset))
    let address = fresh(e)
    line(e, "  " + address + " = getelementptr inbounds i64, ptr " + base
            + ", i64 " + index)
    address
}

-- The address of array element `index`, at the element's own width.
--
-- An array is kind 2, so its `count` is the length and the header's bitmap
-- word is reused as the element width. The width is a compile-time constant
-- here, so the stride is in the `getelementptr`'s type rather than read back
-- out of the header.
fun elementAddress(e : Emit, array : String, index : String,
                   c : RepClass) -> String {
    let base = fresh(e)
    line(e, "  " + base + " = getelementptr inbounds i8, ptr " + array
            + ", i64 " + Int.toString(slotsOffset))
    let address = fresh(e)
    line(e, "  " + address + " = getelementptr inbounds " + elementType(c)
            + ", ptr " + base + ", i64 " + index)
    address
}

-- A value as the raw 64-bit word the runtime's untyped interfaces take.
fun widened(e : Emit, f : Func Low, v : Value, held : Rep) -> String {
    let name = reg(v)
    match held.class_ {
        I64 -> name
        Ptr -> {
            let out = fresh(e)
            line(e, "  " + out + " = ptrtoint ptr " + name + " to i64")
            out
        }
        F64 -> {
            let out = fresh(e)
            line(e, "  " + out + " = bitcast double " + name + " to i64")
            out
        }
        Unit -> "0"
        _ -> {
            let out = fresh(e)
            line(e, "  " + out + " = zext " + typeOf(held) + " " + name
                    + " to i64")
            out
        }
    }
}

-- The inverse: a raw word back to the representation that was asked for.
fun narrow(e : Emit, dst : String, ty : String, raw : String,
           r : Rep) -> Unit = match r.class_ {
    I64 -> line(e, "  " + dst + " = add i64 " + raw + ", 0")
    Ptr -> line(e, "  " + dst + " = inttoptr i64 " + raw + " to ptr")
    F64 -> line(e, "  " + dst + " = bitcast i64 " + raw + " to double")
    Unit -> line(e, "  " + dst + " = add i8 0, 0")
    _ -> line(e, "  " + dst + " = trunc i64 " + raw + " to " + ty)
}

-- Arithmetic, with the checks Turkey's semantics require.
--
-- `Int` addition, subtraction and multiplication *panic* on overflow rather
-- than wrapping -- that is what `Prim.intAddWrapping` exists to opt out of --
-- and division panics on a zero divisor and on the one quotient that does not
-- fit. None of that is expressible as a single LLVM instruction, so each is a
-- checked intrinsic and a guard.
--
-- Only at `i64`. `Byte` and `Char` arithmetic is done by widening to `Int`
-- first, so a narrow `Bin` here is already known to fit.
fun emitBin(e : Emit, f : Func Low, dst : String, ty : String, r : Rep,
            o : BinOp, a : Value, b : Value) -> Unit {
    let plain = fun() -> Unit =
        line(e, "  " + dst + " = " + binOpName(o, r) + " " + ty + " "
                + reg(a) + ", " + reg(b))
    if r.class_ != I64 { return plain() }
    match o {
        Add -> checked(e, f, dst, "sadd", reg(a), reg(b),
                       "integer overflow in +")
        Sub -> checked(e, f, dst, "ssub", reg(a), reg(b),
                       "integer overflow in -")
        Mul -> checked(e, f, dst, "smul", reg(a), reg(b),
                       "integer overflow in *")
        Div -> {
            divisorGuard(e, f, b, "division by zero")
            -- The one quotient that does not fit: the most negative integer
            -- divided by -1.
            let low = fresh(e)
            let negative = fresh(e)
            let both = fresh(e)
            line(e, "  " + low + " = icmp eq i64 " + reg(a)
                    + ", -9223372036854775808")
            line(e, "  " + negative + " = icmp eq i64 " + reg(b) + ", -1")
            line(e, "  " + both + " = and i1 " + low + ", " + negative)
            guard(e, f, both, "integer overflow in /")
            line(e, "  " + dst + " = sdiv i64 " + reg(a) + ", " + reg(b))
        }
        Rem -> {
            divisorGuard(e, f, b, "remainder by zero")
            -- `srem` with those same operands is undefined in LLVM even
            -- though the mathematical answer is zero, so the case is taken
            -- out rather than left to the target's lowering.
            let low = fresh(e)
            let negative = fresh(e)
            let special = fresh(e)
            line(e, "  " + low + " = icmp eq i64 " + reg(a)
                    + ", -9223372036854775808")
            line(e, "  " + negative + " = icmp eq i64 " + reg(b) + ", -1")
            line(e, "  " + special + " = and i1 " + low + ", " + negative)
            let ordinary = blockName(e)
            let done = blockName(e)
            let computed = fresh(e)
            line(e, "  br i1 " + special + ", label %" + done + ", label %"
                    + ordinary)
            let from = e.at
            line(e, ordinary + ":")
            line(e, "  " + computed + " = srem i64 " + reg(a) + ", " + reg(b))
            line(e, "  br label %" + done)
            line(e, done + ":")
            line(e, "  " + dst + " = phi i64 [ 0, %" + from + " ], [ "
                    + computed + ", %" + ordinary + " ]")
            e.at = done
        }
        -- The wrapping forms, and every non-trapping operation: one
        -- instruction, no guard.
        _ -> plain()
    }
}

-- One of LLVM's checked arithmetic intrinsics, and the guard on its overflow
-- bit.
fun checked(e : Emit, f : Func Low, dst : String, operation : String,
            a : String, b : String, message : String) -> Unit {
    let pair = fresh(e)
    let failed = fresh(e)
    line(e, "  " + pair + " = call {i64, i1} @llvm." + operation
            + ".with.overflow.i64(i64 " + a + ", i64 " + b + ")")
    line(e, "  " + dst + " = extractvalue {i64, i1} " + pair + ", 0")
    line(e, "  " + failed + " = extractvalue {i64, i1} " + pair + ", 1")
    guard(e, f, failed, message)
}

fun divisorGuard(e : Emit, f : Func Low, b : Value, message : String) -> Unit {
    let zero = fresh(e)
    line(e, "  " + zero + " = icmp eq i64 " + reg(b) + ", 0")
    guard(e, f, zero, message)
}

fun emitConvert(e : Emit, f : Func Low, dst : String, ty : String, c : Conv,
                a : Value, r : Rep) -> Unit {
    let from = Ssa.repOf(f, a)
    match c {
        IntToFloat -> line(e, "  " + dst + " = sitofp " + typeOf(from) + " "
                              + reg(a) + " to double")
        FloatToInt -> line(e, "  " + dst + " = fptosi double " + reg(a)
                              + " to " + ty)
        Widen -> line(e, "  " + dst + " = zext " + typeOf(from) + " " + reg(a)
                         + " to " + ty)
        Narrow -> line(e, "  " + dst + " = trunc " + typeOf(from) + " " + reg(a)
                          + " to " + ty)
        -- The same machine value with a different obligation on the collector,
        -- and with opaque pointers not even a cast. Kept as an instruction so
        -- the value has its own name and its own `Rep`.
        Retag -> line(e, "  " + dst + " = getelementptr i8, ptr " + reg(a)
                         + ", i64 0")
    }
}

fun emitCall(e : Emit, f : Func Low, dst : String, ty : String, r : Rep,
             callee : Callee, args : Array Value) -> Unit {
    let supplied = []
    for a in args {
        Array.push(supplied, typeOf(Ssa.repOf(f, a)) + " " + reg(a))
    }
    match callee {
        Direct(name) -> line(e, "  " + dst + " = call " + ty + " "
                                + symbol(name) + "("
                                + String.join(supplied, ", ") + ")")
        Runtime(name) -> emitRuntimeCall(e, f, dst, ty, r, name, args)
        -- A closure is kind 3 with exactly two slots, code then environment,
        -- so both are constant-offset loads. The environment is passed first
        -- and every argument is already boxed, which is the uniform convention
        -- `Turkey.SsaLower.closureCall` establishes.
        Indirect(closure) -> {
            let codeAddress = slotAddress(e, reg(closure), "0")
            let code = fresh(e)
            line(e, "  " + code + " = load i64, ptr " + codeAddress)
            let target = fresh(e)
            line(e, "  " + target + " = inttoptr i64 " + code + " to ptr")
            let envAddress = slotAddress(e, reg(closure), "1")
            let raw = fresh(e)
            line(e, "  " + raw + " = load i64, ptr " + envAddress)
            let environment = fresh(e)
            line(e, "  " + environment + " = inttoptr i64 " + raw + " to ptr")
            let passed = ["ptr " + environment]
            for s in supplied { Array.push(passed, s) }
            let types = ["ptr"]
            for a in args { Array.push(types, typeOf(Ssa.repOf(f, a))) }
            line(e, "  " + dst + " = call " + ty + " " + target + "("
                    + String.join(passed, ", ") + ")")
        }
    }
}

-- A primitive with no machine operation, reached as an opaque symbol.
--
-- `NATIVE-BACKEND.md`'s line: an opcode is a constructor where a pass must
-- match on it, and a symbol is a string where none does. Nothing above this
-- asks *which* runtime function, so the instruction set does not grow a
-- constructor per primitive -- and the price is paid exactly here, in one
-- table, which is the cheapest place for it.
fun emitRuntimeCall(e : Emit, f : Func Low, dst : String, ty : String, r : Rep,
                    name : String, args : Array Value) -> Unit {
    let supplied = []
    for a in args {
        Array.push(supplied, typeOf(Ssa.repOf(f, a)) + " " + reg(a))
    }
    let call = fun(symbolName : String, resultType : String) -> String {
        let out = if resultType == "void" { "" } else { fresh(e) }
        let prefix = if resultType == "void" { "  " } else { "  " + out + " = " }
        line(e, prefix + "call " + resultType + " @" + symbolName + "("
                + String.join(supplied, ", ") + ")")
        out
    }
    match runtimeOf(name) {
        Some((symbolName, resultType)) -> {
            if resultType == "void" {
                let _ = call(symbolName, resultType)
                line(e, "  " + dst + " = add i8 0, 0")
            } else if resultType == ty {
                line(e, "  " + dst + " = call " + ty + " @" + symbolName + "("
                        + String.join(supplied, ", ") + ")")
            } else {
                -- The runtime answers `i32` where the language wants `i1`:
                -- `turkey_string_eq` and friends are C predicates. One `icmp`
                -- rather than a second set of entry points.
                let raw = call(symbolName, resultType)
                if ty == "i1" {
                    line(e, "  " + dst + " = icmp ne " + resultType + " " + raw
                            + ", 0")
                } else {
                    line(e, "  " + dst + " = add i8 0, 0 ; unexpected result")
                }
            }
        }
        -- Inline, because they are a few instructions and a runtime entry
        -- point per predicate would be a call where a compare will do.
        None -> match name {
            "Prim.charIsScalar" -> {
                let large = fresh(e)
                let low = fresh(e)
                let high = fresh(e)
                let surrogate = fresh(e)
                let bad = fresh(e)
                line(e, "  " + large + " = icmp ugt i64 " + reg(args[0])
                        + ", 1114111")
                line(e, "  " + low + " = icmp uge i64 " + reg(args[0])
                        + ", 55296")
                line(e, "  " + high + " = icmp ule i64 " + reg(args[0])
                        + ", 57343")
                line(e, "  " + surrogate + " = and i1 " + low + ", " + high)
                line(e, "  " + bad + " = or i1 " + large + ", " + surrogate)
                line(e, "  " + dst + " = xor i1 " + bad + ", true")
            }
            "Prim.floatBits" ->
                line(e, "  " + dst + " = bitcast double " + reg(args[0])
                        + " to i64")
            "Prim.floatFromBits" ->
                line(e, "  " + dst + " = bitcast i64 " + reg(args[0])
                        + " to double")
            "Prim.floatFitsInt" -> {
                let ordered = fresh(e)
                let low = fresh(e)
                let high = fresh(e)
                let inRange = fresh(e)
                line(e, "  " + ordered + " = fcmp ord double " + reg(args[0])
                        + ", " + reg(args[0]))
                line(e, "  " + low + " = fcmp oge double " + reg(args[0])
                        + ", 0xC3E0000000000000")
                line(e, "  " + high + " = fcmp olt double " + reg(args[0])
                        + ", 0x43E0000000000000")
                line(e, "  " + inRange + " = and i1 " + low + ", " + high)
                line(e, "  " + dst + " = and i1 " + ordered + ", " + inRange)
            }
            -- Not a silent wrong-typed value. An unimplemented primitive that
            -- emitted `add i8 0, 0` produced a module `cc` rejected a long way
            -- from the cause, and would have produced a *wrong program* wherever
            -- the types happened to line up. The dump says so instead, in the
            -- same shape the lowering reports an unhandled Core form.
            _ -> {
                line(e, "  " + dst + " = " + zeroInstruction(ty, r))
                Set.add(e.missing, name)
            }
        }
    }
}

-- Primitive name -> the runtime symbol and what it answers.
--
-- Only the ones with a runtime entry point. Everything arithmetic became an
-- opcode in `Turkey.SsaLower.primitive` and never reaches here.
fun runtimeOf(name : String) -> Option (String, String) = match name {
    "Prim.print" -> Some(("turkey_print", "i8"))
    "Prim.write" -> Some(("turkey_write", "i8"))
    "Prim.stderrWrite" -> Some(("turkey_stderr_write", "i8"))
    "Prim.exit" -> Some(("turkey_exit", "void"))
    "Prim.intToString" -> Some(("turkey_int_to_string", "ptr"))
    "Prim.floatToString" -> Some(("turkey_float_to_string", "ptr"))
    "Prim.charToString" -> Some(("turkey_char_to_string", "ptr"))
    "Prim.stringConcat" -> Some(("turkey_string_concat", "ptr"))
    "Prim.stringConcatAll" -> Some(("turkey_string_concat_all", "ptr"))
    "Prim.stringEq" -> Some(("turkey_string_eq", "i32"))
    "Prim.stringLt" -> Some(("turkey_string_lt", "i32"))
    "Prim.stringByteLength" -> Some(("turkey_string_byte_length", "i64"))
    "Prim.stringByteAt" -> Some(("turkey_string_byte_at", "i8"))
    "Prim.stringDecodeAt" -> Some(("turkey_string_decode_at", "i32"))
    "Prim.stringNextIndex" -> Some(("turkey_string_next_index", "i64"))
    "Prim.stringSlice" -> Some(("turkey_string_slice", "ptr"))
    "Prim.stringFind" -> Some(("turkey_string_find", "i64"))
    "Prim.stringRfind" -> Some(("turkey_string_rfind", "i64"))
    "Prim.stringIsValidUtf8" -> Some(("turkey_string_is_valid_utf8", "i32"))
    "Prim.floatParse" -> Some(("turkey_float_parse", "double"))
    "Prim.floatCanParse" -> Some(("turkey_float_can_parse", "i32"))
    "Prim.floatFmod" -> Some(("turkey_float_fmod", "double"))
    "Prim.floatRemainder" -> Some(("turkey_float_remainder", "double"))
    "Prim.floatFloor" -> Some(("turkey_float_floor", "double"))
    "Prim.floatCeil" -> Some(("turkey_float_ceil", "double"))
    "Prim.floatRound" -> Some(("turkey_float_round", "double"))
    "Prim.floatTrunc" -> Some(("turkey_float_trunc", "double"))
    "Prim.fileCanRead" -> Some(("turkey_file_can_read", "i32"))
    "Prim.stringFromBytes" -> Some(("turkey_string_from_bytes", "ptr"))
    -- The *storage* forms. `Turkey.SsaLower` names these rather than the
    -- language's `Prim.args`, `Prim.readFileBytes` and `Prim.stringToBytes`,
    -- because those answer an `Array` and the runtime can only build its
    -- contents -- the record around it carries a tag this compilation owns.
    "Prim.argsStorage" -> Some(("turkey_args_storage", "ptr"))
    "Prim.readFileStorage" -> Some(("turkey_read_file_bytes", "ptr"))
    "Prim.stringToByteStorage" -> Some(("turkey_string_to_byte_storage", "ptr"))
    "Prim.writeFileBytes" -> Some(("turkey_write_file_bytes", "i32"))
    _ -> None
}

-- -- the entry point -------------------------------------------------------------------

-- `main`, and the sequence that has to happen before the program's own.
--
-- Three things in order: the string literals are built, the module
-- initializer computes every global, and then `main` runs. The literals come
-- first because an initializer's value may be one, and `turkey_main` wraps the
-- lot because argument handling, the panic report and the final collection are
-- the runtime's and are written once in C rather than emitted here.
fun emitEntry(e : Emit, m : Module, entry : String) -> Unit {
    line(e, "define internal void @.turkey.entry() {")
    line(e, "b0:")
    -- The permanent frame, before anything allocates. It is never left: every
    -- global is live for the whole run, so the mask is all ones and the frame
    -- sits at the bottom of the chain until the process exits.
    line(e, "  call void @turkey_root_enter(ptr @.turkey.global.frame, ptr "
            + "@.turkey.global.roots, i64 " + Int.toString(len(e.globals))
            + ", ptr " + messageGlobal(e, "<globals>") + ")")
    let maskAddress = fresh(e)
    line(e, "  " + maskAddress + " = getelementptr inbounds i8, ptr "
            + "@.turkey.global.frame, i64 32")
    line(e, "  store i64 -1, ptr " + maskAddress)
    for (text, index) in Map.entries(e.literals) {
        let made = fresh(e)
        line(e, "  " + made + " = call ptr @turkey_string_new(ptr "
                + literalBytes(index) + ", i64 "
                + Int.toString(len(String.toBytes(text))) + ")")
        line(e, "  store ptr " + made + ", ptr " + literalValue(e, index))
    }
    line(e, "  call i8 " + symbol(m.initial) + "()")
    line(e, "  call i8 " + symbol(entry) + "()")
    line(e, "  ret void")
    line(e, "}")
    line(e, "")
    line(e, "define i32 @main(i32 %argc, ptr %argv) {")
    line(e, "b0:")
    line(e, "  %status = call i32 @turkey_main(i32 %argc, ptr %argv, ptr @.turkey.entry)")
    line(e, "  ret i32 %status")
    line(e, "}")
}
