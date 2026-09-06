module Turkey.Runtime (
    Entry(..), layoutCode, traceBit, elementWidth, entryPoint,
)

-- What `runtime/turkey_runtime.c` and `.h` say, in one place.
--
-- **The test for what belongs here is not "two backends write it".** It is
-- whether the thing is a fact about an artifact this compiler does not own.
-- The layout codes are a number `mark_children` reads out of an object header;
-- the entry points are symbols a C file defines. Neither is a decision a
-- backend gets to make, so a backend holding its own copy is not a second
-- implementation of a rule -- it is a second transcription of someone else's
-- constant, which is the kind that drifts without anything going red.
--
-- That is a different argument from the one in `Turkey.Roots`, and the
-- difference is worth keeping straight. `Roots` shares an *analysis* because
-- two backends could compute liveness differently and only a collection would
-- notice. Here nothing is computed at all. Both are extractions; only one of
-- them was about disagreement.
--
-- And it is the argument for what is *not* here. The overflow and division
-- checks stay in each emitter (`Turkey.Select`'s header): those are a shared
-- intention with no shared rule, and LLVM and arm64 write genuinely different
-- and better sequences. `Turkey.Llvm.declareRuntime`'s `declare` lines stay
-- there too, even though they name these same symbols -- they carry argument
-- types this table does not have, and an undeclared or mistyped symbol is
-- something LLVM refuses rather than miscompiles. Duplication a checker sees
-- is not the dangerous kind.

import Turkey.Ssa as Ssa
import Turkey.Ssa (Rep(..), RepClass(..))

-- -- representations ------------------------------------------------------------------

-- The number the runtime calls this representation.
--
-- Written into an object's header three bits at a time by
-- `Turkey.SsaLower.metadata`, and passed to `turkey_box`, `turkey_unbox`,
-- `turkey_cell_new` and `turkey_array_new` by every emitter. One numbering,
-- read by `mark_children` -- so the collector follows a pointer iff the writer
-- and the caller agreed, and there is no diagnostic when they do not.
fun layoutCode(r : Rep) -> Int = match r.class_ {
    Unit -> 0
    I1 -> 1
    I8 -> 2
    I32 -> 3
    I64 -> 4
    F64 -> 5
    Ptr -> if r.traced { 7 } else { 6 }
}

-- Whether the collector must follow a slot, for the one-bit-per-slot encoding
-- that closures and environments use. Not `layoutCode`: one C field carries
-- both encodings and the object's kind is what says which.
fun traceBit(r : Rep) -> Int = if r.traced { 1 } else { 0 }

-- Bytes one array element occupies. `turkey_array_new` is called with this
-- width and the element is then read at this stride, so the two uses have to
-- be the same rule -- as they must also stay the same rule as
-- `turkey/backend_lower._layout_width`.
fun elementWidth(c : RepClass) -> Int = match c {
    I8 -> 1
    I32 -> 4
    _ -> 8
}

-- -- entry points ---------------------------------------------------------------------

-- A primitive's runtime implementation: the C symbol, and what C says it
-- returns.
--
-- `result` is `None` where C says `void`. That is not the same as the
-- language's unit and must not be spelled as it: `Turkey.Llvm` holds unit in
-- an `i8` on purpose, so a `void` entry point needs the emitter to skip the
-- assignment rather than to name a type.
type Entry = Entry { symbol : String, result : Option RepClass }

fun returning(symbol : String, c : RepClass) -> Option Entry =
    Some(Entry { symbol = symbol, result = Some(c) })

fun void_(symbol : String) -> Option Entry =
    Some(Entry { symbol = symbol, result = None })

-- Primitive name to runtime entry point.
--
-- `None` means no entry point, which is not the same as unsupported: some
-- primitives are a compare or two and each emitter inlines them rather than
-- pay a call. An emitter that cannot inline one reports it and stops the
-- function.
--
-- Several of these answer `I32` where the language wants a `Bool`: they are C
-- predicates, and one compare at the call site is cheaper than a second set of
-- entry points.
fun entryPoint(name : String) -> Option Entry = match name {
    "Prim.print" -> returning("turkey_print", I8)
    "Prim.write" -> returning("turkey_write", I8)
    "Prim.stderrWrite" -> returning("turkey_stderr_write", I8)
    "Prim.exit" -> void_("turkey_exit")
    "Prim.intToString" -> returning("turkey_int_to_string", Ptr)
    "Prim.floatToString" -> returning("turkey_float_to_string", Ptr)
    "Prim.charToString" -> returning("turkey_char_to_string", Ptr)
    "Prim.stringConcat" -> returning("turkey_string_concat", Ptr)
    "Prim.stringConcatAll" -> returning("turkey_string_concat_all", Ptr)
    "Prim.stringEq" -> returning("turkey_string_eq", I32)
    "Prim.stringLt" -> returning("turkey_string_lt", I32)
    "Prim.stringByteLength" -> returning("turkey_string_byte_length", I64)
    "Prim.stringByteAt" -> returning("turkey_string_byte_at", I8)
    "Prim.stringDecodeAt" -> returning("turkey_string_decode_at", I32)
    "Prim.stringNextIndex" -> returning("turkey_string_next_index", I64)
    "Prim.stringSlice" -> returning("turkey_string_slice", Ptr)
    "Prim.stringFind" -> returning("turkey_string_find", I64)
    "Prim.stringRfind" -> returning("turkey_string_rfind", I64)
    "Prim.stringIsValidUtf8" -> returning("turkey_string_is_valid_utf8", I32)
    "Prim.stringFromBytes" -> returning("turkey_string_from_bytes", Ptr)
    "Prim.floatParse" -> returning("turkey_float_parse", F64)
    "Prim.floatCanParse" -> returning("turkey_float_can_parse", I32)
    "Prim.floatFmod" -> returning("turkey_float_fmod", F64)
    "Prim.floatRemainder" -> returning("turkey_float_remainder", F64)
    "Prim.floatFloor" -> returning("turkey_float_floor", F64)
    "Prim.floatCeil" -> returning("turkey_float_ceil", F64)
    "Prim.floatRound" -> returning("turkey_float_round", F64)
    "Prim.floatTrunc" -> returning("turkey_float_trunc", F64)
    "Prim.fileCanRead" -> returning("turkey_file_can_read", I32)
    "Prim.writeFileBytes" -> returning("turkey_write_file_bytes", I32)
    -- The *storage* forms. `Turkey.SsaLower` names these rather than the
    -- language's `Prim.args`, `Prim.readFileBytes` and `Prim.stringToBytes`,
    -- because those answer an `Array` and the runtime can only build its
    -- contents -- the record around it carries a tag this compilation owns.
    "Prim.argsStorage" -> returning("turkey_args_storage", Ptr)
    "Prim.readFileStorage" -> returning("turkey_read_file_bytes", Ptr)
    "Prim.stringToByteStorage" -> returning("turkey_string_to_byte_storage", Ptr)
    _ -> None
}
