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
                   missing = Set.new() }
    line(e, "; Generated by the Turkey bootstrap compiler.")
    line(e, "")
    declareRuntime(e)
    line(e, "")
    -- Every string literal in the program, found before anything is emitted
    -- so that a `ConstString` can be a load of an already-decided global.
    collectLiterals(m, e.literals)
    for (text, index) in Map.entries(e.literals) {
        emitLiteral(e, text, index)
    }
    line(e, "")
    for (name, r) in m.globals {
        line(e, symbol("global." + name) + " = internal global " + typeOf(r)
                + " " + zeroOf(r))
    }
    line(e, "")
    for f in m.functions { emitFunc(e, f) }
    emitEntry(e, m, entry)
    (String.build(e.out), Array.sort(Set.elements(e.missing)))
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
    line(e, literalValue(index) + " = internal global ptr null")
}

fun literalBytes(index : Int) -> String =
    "@.turkey.literal.bytes." + Int.toString(index)

fun literalValue(index : Int) -> String =
    "@.turkey.literal.value." + Int.toString(index)

-- Every byte as an escape. Not only the ones LLVM requires -- a literal may
-- hold any byte at all, including a NUL or a quote, and deciding per byte
-- which spelling is legal is a rule to get wrong for no gain.
fun byteEscape(n : Int) -> String =
    "\\" + hexDigit(n / 16) + hexDigit(n % 16)

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

fun emitFunc(e : Emit, f : Func Low) -> Unit {
    let params = []
    for p in f.params {
        Array.push(params, typeOf(Ssa.repOf(f, p)) + " " + reg(p))
    }
    line(e, "define internal " + typeOf(f.result) + " " + symbol(f.name)
            + "(" + String.join(params, ", ") + ") {")
    -- Where each block's phis come from. Read once: a block's predecessors are
    -- found by scanning every terminator, and doing that per block would be
    -- quadratic in a function the size of the ones this compiler produces.
    let incoming = incomingOf(f)
    for var b = 0; b < len(f.blocks); b = b + 1 {
        emitBlock(e, f, BlockId(b), incoming)
    }
    line(e, "}")
    line(e, "")
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

fun emitBlock(e : Emit, f : Func Low, at : BlockId,
              incoming : Map Int (Array (Int, Array Value))) -> Unit {
    let block = Ssa.blockAt(f, at)
    line(e, label(at) + ":")
    let sources = Map.getOr(incoming, Ssa.blockId(at), [])
    for var p = 0; p < len(block.params); p = p + 1 {
        let arms = []
        for (from, args) in sources {
            if p < len(args) {
                Array.push(arms, "[ " + reg(args[p]) + ", %"
                                 + label(BlockId(from)) + " ]")
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
    for i in block.insts { emitInst(e, f, i) }
    emitTerm(e, f, block.term)
}

fun undefOf(r : Rep) -> String = "undef"

fun emitTerm(e : Emit, f : Func Low, t : Term) -> Unit = match t {
    Ret(v) -> line(e, "  ret " + typeOf(Ssa.repOf(f, v)) + " " + reg(v))
    Jump(target, _) -> line(e, "  br label %" + label(target))
    Branch(c, yes, no) ->
        line(e, "  br i1 " + reg(c) + ", label %" + label(yes)
                + ", label %" + label(no))
    Panic(v) -> {
        line(e, "  call void @turkey_panic_string(ptr " + reg(v) + ")")
        line(e, "  ret " + typeOf(f.result) + " " + zeroOf(f.result))
    }
    -- Not `unreachable`: this is reached only where the lowering proved a
    -- value never arrives, and an LLVM `unreachable` licenses the optimizer to
    -- assume the path is impossible -- which is true, and which turns any
    -- mistake in that proof into miscompiled surrounding code rather than a
    -- crash. A return of zero is the same claim, made unexploitably.
    Unreachable -> line(e, "  ret " + typeOf(f.result) + " "
                           + zeroOf(f.result))
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
                                     + literalValue(literalIndex(e, text)))
        ConstUnit -> line(e, "  " + dst + " = add i8 0, 0")
        Bin(o, a, b) -> line(e, "  " + dst + " = " + binOpName(o, r) + " " + ty
                                + " " + reg(a) + ", " + reg(b))
        Cmp(o, a, b) -> {
            let operand = Ssa.repOf(f, a)
            line(e, "  " + dst + " = " + cmpOpName(o, operand) + " "
                    + typeOf(operand) + " " + reg(a) + ", " + reg(b))
        }
        Neg(a) -> if r.class_ == F64 {
            line(e, "  " + dst + " = fneg double " + reg(a))
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
        Call(callee, args) -> emitCall(e, f, dst, ty, r, callee, args)
        GlobalLoad(name) ->
            line(e, "  " + dst + " = load " + ty + ", ptr "
                    + symbol("global." + name))
        GlobalStore(name, v) -> {
            line(e, "  store " + typeOf(Ssa.repOf(f, v)) + " " + reg(v)
                    + ", ptr " + symbol("global." + name))
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
    "Prim.stringToBytes" -> Some(("turkey_string_to_byte_storage", "ptr"))
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
    for (text, index) in Map.entries(e.literals) {
        let made = fresh(e)
        line(e, "  " + made + " = call ptr @turkey_string_new(ptr "
                + literalBytes(index) + ", i64 "
                + Int.toString(len(String.toBytes(text))) + ")")
        line(e, "  store ptr " + made + ", ptr " + literalValue(index))
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
