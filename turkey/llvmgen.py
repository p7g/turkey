"""Emit, verify, and execute llvmlite IR from the checked backend CFG."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from llvmlite import binding, ir

from . import backend_ir as bir
from .backend_lower import lower
from .core import CProgram
from .decls import DeclTable
from .errors import Span, TurkeyPanic, Unsupported


_I1 = ir.IntType(1)
_I8 = ir.IntType(8)
_I32 = ir.IntType(32)
_I64 = ir.IntType(64)
_F64 = ir.DoubleType()
_PTR = _I8.as_pointer()
_ROOT_FRAME = ir.LiteralStructType([_PTR, _PTR, _I64, _PTR])
# `PanicCallFrame` is now {previous, site}, and `PanicSite` is the constant the
# frame points at. Moving a frame is one store of a pointer to a constant this
# module already holds, rather than four stores of its fields -- and that
# happens before every operation that can panic, so the four were in the body
# of every loop that could overflow or index out of bounds.
_PANIC_FRAME = ir.LiteralStructType([_PTR, _PTR])
_PANIC_SITE = ir.LiteralStructType([_PTR, _PTR, _I64, _I64])
# `TurkeyObject` from runtime/turkey_runtime.h, field for field. Reading a slot
# is a `getelementptr` rather than a call because the layout it would be read
# at is a compile-time constant in a monomorphized program; see
# `test_no_conformance_program_reconciles_a_scalar_layout` for the licence.
# The trailing zero-length array is how a C flexible member is spelled here, so
# slot `i` is field 4, index `i`. Every slot holds a raw 64-bit pattern -- a
# `Float` is bitcast in, a pointer is `ptrtoint`ed in -- which is why the load
# is always `i64` and `_from_i64` does the rest.
_OBJECT = ir.LiteralStructType(
    [_I32, _I32, _I64, _I64, ir.ArrayType(_I64, 0)])
_OBJECT_KIND, _OBJECT_TAG, _OBJECT_COUNT, _OBJECT_BITMAP, _OBJECT_SLOTS = range(5)
# `TurkeyCell`: one traced word plus the flag saying whether to trace it.
_CELL = ir.LiteralStructType([_I64, _I32])


def _llvm_type(layout: bir.Layout) -> ir.Type:
    return {
        bir.Layout.UNIT: _I8, bir.Layout.I1: _I1, bir.Layout.I8: _I8,
        bir.Layout.I32: _I32, bir.Layout.I64: _I64, bir.Layout.F64: _F64,
        bir.Layout.PTR: _PTR, bir.Layout.BOXED: _PTR,
    }[layout]


def _layout_code(layout: bir.Layout) -> int:
    return {
        bir.Layout.UNIT: 0, bir.Layout.I1: 1, bir.Layout.I8: 2,
        bir.Layout.I32: 3, bir.Layout.I64: 4, bir.Layout.F64: 5,
        bir.Layout.PTR: 6, bir.Layout.BOXED: 7,
    }[layout]


def _layout_width(layout: bir.Layout) -> int:
    """Bytes one array element of this layout occupies.

    The same rule as `backend_lower._layout_width`, and it has to stay the
    same: that one decides the width `turkey_array_new` is called with, and
    this one decides the stride the element is then read at.
    """
    if layout is bir.Layout.I8:
        return 1
    if layout is bir.Layout.I32:
        return 4
    return 8


# Operations that emit a call, and so may collect before they return. Being
# wrong in the *inclusive* direction costs a redundant root store; being wrong
# the other way costs a live object freed under a caller's feet, which is why
# a call that cannot itself allocate is still listed. The GC-stress tests are
# what check this set: they collect at every allocation, so a root this misses
# is a panic on the first collection rather than a rare corruption.
_CALLING_OPS = frozenset({
    "call", "closure_call", "box", "unbox", "cell_new", "object_new",
    "array_new", "closure_new", "function_closure", "closure_capture",
})
_CALLING_PRIMS = frozenset({
    "intToString", "floatToString", "charToString", "stringConcat", "print",
    "write", "stringByteLength", "stringByteAt", "stringDecodeAt",
    "stringNextIndex", "stringSlice", "stringFind", "stringRfind",
    "stringToByteStorage", "stringFromBytes", "stringConcatAll", "floatParse",
    "floatFmod", "floatRemainder", "floatFloor", "floatCeil", "floatRound",
    "floatTrunc", "stringIsValidUtf8", "floatCanParse", "stringEq", "stringLt",
    "arrayNew", "arrayNewUninit", "error",
})
_LAYOUT_SUFFIXES = frozenset(layout.value for layout in bir.Layout)


def _is_safepoint(op: str) -> bool:
    if op in _CALLING_OPS:
        return True
    if not op.startswith("prim."):
        return False
    name = op[5:]
    if name.rpartition(".")[2] in _LAYOUT_SUFFIXES:
        name = name.rpartition(".")[0]
    return name in _CALLING_PRIMS


def _operands(instruction: bir.Instruction) -> list[bir.Value]:
    return [arg for arg in instruction.args if isinstance(arg, bir.Value)]


def _successors(terminator) -> list[str]:
    if isinstance(terminator, bir.Jump):
        return [terminator.target]
    if isinstance(terminator, bir.Branch):
        return [terminator.yes, terminator.no]
    return []


def _live_across_safepoint(function: bir.Function) -> set[str]:
    """Which parameters and slots hold a pointer over a possible collection.

    `_root_slots` bounds an instruction result's live range inside one block,
    because `bir.check` rebuilds the SSA scope per block. Parameters and slots
    are the two things that escape that rule -- a parameter is nameable from
    every block, a slot is function-wide mutable storage -- so both used to be
    rooted unconditionally, on the grounds that there are few of them.

    There are not few of them. `backend_lower` gives every ANF temporary its
    own slot, so a two-line `Main#inc` has thirty-eight, twenty-four of them
    pointers; it allocates nothing itself and paid twenty-six root stores per
    call. What is needed is ordinary backward liveness over the CFG, with a
    slot killed by a store to it and a parameter never killed, asking of each
    safepoint which of them is live across it. This is how any collector with
    a precise stack map computes one (OCaml, Go, and LLVM's own
    `gc.statepoint` all do the same dataflow); the only wrinkle here is that
    a value passed *to* the safepoint counts as live across it, since the
    callee holds it while it may collect.
    """
    tracked = {value.name for value in list(function.params) + list(function.slots)
               if value.layout in (bir.Layout.PTR, bir.Layout.BOXED)}
    if not tracked:
        return set()
    blocks = {block.name: block for block in function.blocks}
    if not any(_is_safepoint(instruction.op)
               for block in function.blocks
               for instruction in block.instructions):
        return set()

    def uses(instruction: bir.Instruction) -> set[str]:
        found = {operand.name for operand in _operands(instruction)}
        if instruction.op == "slot_load":
            found.add(instruction.args[0])
        return found & tracked

    def killed(instruction: bir.Instruction) -> str | None:
        if instruction.op == "slot_store" and instruction.args[0] in tracked:
            return instruction.args[0]
        return None

    live_in: dict[str, set[str]] = {block.name: set() for block in function.blocks}
    changed = True
    while changed:
        changed = False
        for block in reversed(function.blocks):
            assert block.terminator is not None
            live = {operand.name
                    for operand in _terminator_operands(block.terminator)} & tracked
            for name in _successors(block.terminator):
                live |= live_in[name]
            for instruction in reversed(block.instructions):
                dead = killed(instruction)
                if dead is not None:
                    live.discard(dead)
                live |= uses(instruction)
            if live != live_in[block.name]:
                live_in[block.name] = live
                changed = True

    wanted: set[str] = set()
    for block in function.blocks:
        assert block.terminator is not None
        live = {operand.name
                for operand in _terminator_operands(block.terminator)} & tracked
        for name in _successors(block.terminator):
            live |= live_in[name]
        for instruction in reversed(block.instructions):
            if _is_safepoint(instruction.op):
                wanted |= live | uses(instruction)
            dead = killed(instruction)
            if dead is not None:
                live.discard(dead)
            live |= uses(instruction)
    return wanted


def _root_slots(function: bir.Function) -> tuple[dict[str, int], int]:
    """Stack storage in the exact-root frame, for the values that need it.

    A pointer needs a root only where a collection can happen while it is
    live. Rooting every pointer-typed value in the function -- which is what
    this did, and what `LLVM-BACKEND.md` allowed as a first implementation --
    costs a store per definition and, worse, is unremovable: the frame's
    address escapes into `turkey_root_enter`, so no LLVM pass may drop a store
    into it. A nine-line `main` paid 76 slots and a 608-byte memset.

    Two kinds of value are rooted unconditionally. A function parameter is
    nameable from every block (`bir.check` scopes SSA per block but exempts
    parameters), and a slot is function-wide mutable storage, so neither has a
    live range this can bound cheaply. There are few of both.

    Everything else -- block parameters and instruction results -- is
    block-local by construction, since `bir.check` rebuilds the scope per block
    and anything crossing an edge must become a block parameter or a slot. So
    its live range is an interval inside one block, and it needs a root exactly
    when a safepoint falls in that interval. A value passed *to* a safepoint
    counts: the callee holds it while it may collect.

    This is what makes the Phase 2 work pay: with field access no longer a
    call, a loop that touches no allocation has no safepoint in its body, and
    so takes no root stores at all.
    """
    index: dict[str, int] = {}

    def take(value: bir.Value) -> None:
        if (value.layout in (bir.Layout.PTR, bir.Layout.BOXED)
                and value.name not in index):
            index[value.name] = len(index)

    wanted = _live_across_safepoint(function)
    for value in function.params:
        if value.name in wanted:
            take(value)
    for value in function.slots:
        if value.name in wanted:
            take(value)

    for block in function.blocks:
        safepoints = [position for position, instruction
                      in enumerate(block.instructions)
                      if _is_safepoint(instruction.op)]
        if not safepoints:
            continue
        # The last position each block-local value is read at. The terminator
        # reads after every instruction, and none of the terminators emits a
        # call, so `len(instructions)` is a fine stand-in for "to the end".
        last_use: dict[str, int] = {}
        for position, instruction in enumerate(block.instructions):
            for operand in _operands(instruction):
                last_use[operand.name] = position
        terminator = block.terminator
        assert terminator is not None
        for operand in _terminator_operands(terminator):
            last_use[operand.name] = len(block.instructions)

        def live_across(name: str, defined: int) -> bool:
            end = last_use.get(name)
            return end is not None and any(
                defined < point <= end for point in safepoints)

        for param in block.params:
            if live_across(param.name, -1):
                take(param)
        for position, instruction in enumerate(block.instructions):
            result = instruction.result
            if result is not None and live_across(result.name, position):
                take(result)

    return index, len(index)


def _terminator_operands(terminator) -> list[bir.Value]:
    if isinstance(terminator, bir.Return):
        found = [terminator.value]
    elif isinstance(terminator, bir.Jump):
        found = list(terminator.args)
    elif isinstance(terminator, bir.Branch):
        found = [terminator.condition]
    else:
        found = [terminator.message]
    return [value for value in found if isinstance(value, bir.Value)]


class _Emitter:
    def __init__(self, source: bir.Module) -> None:
        binding.initialize_native_target()
        binding.initialize_native_asmprinter()
        self.source = source
        self.target = binding.Target.from_default_triple()
        self.machine = self.target.create_target_machine()
        self.module = ir.Module(name="turkey")
        self.module.triple = binding.get_default_triple()
        self.module.data_layout = str(self.machine.target_data)
        self.panic_flag = ir.GlobalVariable(
            self.module, _I32, name="turkey_has_panicked")
        self.functions: dict[str, ir.Function] = {}
        self.source_functions = {function.name: function
                                 for function in source.functions}
        self.globals: dict[str, ir.GlobalVariable] = {}
        self.runtime: dict[str, ir.Function] = {}
        self.closure_thunks: dict[str, ir.Function] = {}
        self.string_literals: dict[str, tuple[ir.GlobalVariable, ir.GlobalVariable, int]] = {}
        self.string_count = 0
        self.c_strings: dict[str, ir.GlobalVariable] = {}
        self.panic_sites: dict[bir.Frame, ir.GlobalVariable] = {}
        self.nullary_objects: dict[tuple[int, int], ir.GlobalVariable] = {}
        self._declare_runtime()

    def _runtime(self, name: str, ret: ir.Type, args: list[ir.Type]) -> None:
        self.runtime[name] = ir.Function(self.module, ir.FunctionType(ret, args), name=name)

    def _declare_runtime(self) -> None:
        self._runtime("turkey_string_new", _PTR, [_PTR, _I64])
        self._runtime("turkey_string_concat", _PTR, [_PTR, _PTR])
        self._runtime("turkey_int_to_string", _PTR, [_I64])
        self._runtime("turkey_float_to_string", _PTR, [_F64])
        self._runtime("turkey_float_parse", _F64, [_PTR])
        self._runtime("turkey_float_can_parse", _I32, [_PTR])
        self._runtime("turkey_float_fmod", _F64, [_F64, _F64])
        self._runtime("turkey_float_remainder", _F64, [_F64, _F64])
        self._runtime("turkey_float_floor", _F64, [_F64])
        self._runtime("turkey_float_ceil", _F64, [_F64])
        self._runtime("turkey_float_round", _F64, [_F64])
        self._runtime("turkey_float_trunc", _F64, [_F64])
        self._runtime("turkey_char_to_string", _PTR, [_I32])
        self._runtime("turkey_string_byte_length", _I64, [_PTR])
        self._runtime("turkey_string_byte_at", _I8, [_PTR, _I64])
        self._runtime("turkey_string_decode_at", _I32, [_PTR, _I64])
        self._runtime("turkey_string_next_index", _I64, [_PTR, _I64])
        self._runtime("turkey_string_slice", _PTR, [_PTR, _I64, _I64])
        self._runtime("turkey_string_find", _I64, [_PTR, _PTR, _I64])
        self._runtime("turkey_string_rfind", _I64, [_PTR, _PTR])
        self._runtime("turkey_string_to_byte_storage", _PTR, [_PTR])
        self._runtime("turkey_string_from_bytes", _PTR, [_PTR])
        self._runtime("turkey_string_is_valid_utf8", _I32, [_PTR])
        self._runtime("turkey_string_concat_all", _PTR, [_PTR])
        self._runtime("turkey_string_eq", _I32, [_PTR, _PTR])
        self._runtime("turkey_string_lt", _I32, [_PTR, _PTR])
        self._runtime("turkey_print", _I8, [_PTR])
        self._runtime("turkey_write", _I8, [_PTR])
        self._runtime("turkey_cell_new", _PTR, [_I64, _I32])
        self._runtime("turkey_cell_load", _I64, [_PTR])
        self._runtime("turkey_cell_store", ir.VoidType(), [_PTR, _I64])
        self._runtime("turkey_object_new", _PTR, [_I32, _I32, _I64, _I64])
        self._runtime("turkey_object_tag", _I32, [_PTR])
        self._runtime("turkey_object_get", _I64, [_PTR, _I64])
        self._runtime("turkey_object_set", ir.VoidType(), [_PTR, _I64, _I64])
        self._runtime("turkey_object_get_as", _I64, [_PTR, _I64, _I32])
        self._runtime("turkey_object_set_as", ir.VoidType(), [_PTR, _I64, _I64, _I32])
        self._runtime("turkey_box", _PTR, [_I64, _I32])
        self._runtime("turkey_unbox", _I64, [_PTR, _I32])
        self._runtime("turkey_array_new", _PTR, [_I64, _I64, _I32, _I32])
        self._runtime("turkey_array_length", _I64, [_PTR])
        self._runtime("turkey_array_get", _I64, [_PTR, _I64])
        self._runtime("turkey_array_set", ir.VoidType(), [_PTR, _I64, _I64])
        self._runtime("turkey_array_get_as", _I64, [_PTR, _I64, _I32])
        self._runtime("turkey_array_set_as", ir.VoidType(), [_PTR, _I64, _I64, _I32])
        self._runtime("turkey_array_get_boxed", _PTR, [_PTR, _I64])
        self._runtime("turkey_array_set_boxed", ir.VoidType(), [_PTR, _I64, _PTR])
        self._runtime("turkey_closure_new", _PTR, [_I64, _I64, _I64])
        self._runtime("turkey_closure_code", _I64, [_PTR])
        self._runtime("turkey_closure_environment", _PTR, [_PTR])
        self._runtime("turkey_closure_capture", ir.VoidType(), [_PTR, _I64, _I64])
        self._runtime("turkey_panic", ir.VoidType(), [_PTR])
        self._runtime("turkey_panic_string", ir.VoidType(), [_PTR])
        self._runtime("turkey_panicked", _I32, [])
        self._runtime("turkey_frame_enter", ir.VoidType(), [_PTR, _PTR])
        self._runtime("turkey_frame_leave", ir.VoidType(), [_PTR])
        self._runtime("turkey_root_enter", ir.VoidType(), [_PTR, _PTR, _I64, _PTR])
        self._runtime("turkey_root_leave", ir.VoidType(), [_PTR])

    def emit(self) -> tuple[str, binding.TargetMachine]:
        literals = sorted({
            instruction.args[0]
            for function in self.source.functions
            for block in function.blocks
            for instruction in block.instructions
            if instruction.op == "string_const"
        })
        for index, value in enumerate(literals):
            data = value.encode("utf-8")
            terminated = data + b"\0"
            array = ir.Constant(
                ir.ArrayType(_I8, len(terminated)), bytearray(terminated))
            bytes_global = ir.GlobalVariable(
                self.module, array.type, name=f".turkey.literal.bytes.{index}")
            bytes_global.global_constant = True
            bytes_global.linkage = "private"
            bytes_global.initializer = array
            value_global = ir.GlobalVariable(
                self.module, _PTR, name=f".turkey.literal.value.{index}")
            value_global.linkage = "internal"
            value_global.initializer = ir.Constant(_PTR, None)
            self.string_literals[value] = (bytes_global, value_global, len(data))
        # A constructor with no fields carries no information beyond its own
        # identity: `None` is one word of `kind`, one of `tag`, a zero count
        # and an empty bitmap, and nothing can ever write to it. Allocating a
        # fresh one per evaluation is the single largest source of garbage in
        # an iterator-driven program -- `Iterator.next` returns `None` once per
        # loop and `Some` once per element -- so each distinct (kind, tag) gets
        # one object built at module entry and shared for the whole run. This
        # is what OCaml does with constant constructors (immediates, no heap
        # cell at all), what GHC does with nullary constructors (a single
        # static closure per constructor), and what Java does for `enum`.
        for kind, tag in sorted({
                (int(instruction.args[0]), int(instruction.args[1]))
                for function in self.source.functions
                for block in function.blocks
                for instruction in block.instructions
                if instruction.op == "object_new" and instruction.args[2] == "0"}):
            value_global = ir.GlobalVariable(
                self.module, _PTR,
                name=f".turkey.nullary.value.{kind}.{tag}".replace("-", "_"))
            value_global.linkage = "internal"
            value_global.initializer = ir.Constant(_PTR, None)
            self.nullary_objects[(kind, tag)] = value_global
        # A pointer-typed global holds a top-level dictionary or value for the
        # whole run, so it is a GC root and has to be one *explicitly*. It was
        # never registered as one: what kept these alive was that
        # `_root_slots` rooted every pointer in a function and never cleared a
        # slot, so the frame that built them held them by accident. Rooting by
        # liveness removes that accident, and `dicts.tl` collects its own
        # instance dictionaries out from under itself under GC stress.
        #
        # So the storage *is* the root array: one module-level array of
        # pointers, registered once with a frame that is never left, and
        # `global_load`/`global_store` index into it. A separate global plus a
        # shadow copy would need every store to update both.
        pointers = [source for source in self.source.globals
                    if source.layout in (bir.Layout.PTR, bir.Layout.BOXED)]
        self.global_roots = {source.name: index
                             for index, source in enumerate(pointers)}
        self.global_array = None
        if pointers:
            array = ir.ArrayType(_PTR, len(pointers))
            self.global_array = ir.GlobalVariable(
                self.module, array, name=".turkey.global.roots")
            self.global_array.linkage = "internal"
            self.global_array.initializer = ir.Constant(array, None)
            self.global_frame = ir.GlobalVariable(
                self.module, _ROOT_FRAME, name=".turkey.global.frame")
            self.global_frame.linkage = "internal"
            self.global_frame.initializer = ir.Constant(_ROOT_FRAME, None)
        for source in self.source.globals:
            if source.name in self.global_roots:
                continue
            global_ = ir.GlobalVariable(self.module, _llvm_type(source.layout),
                                        name=source.name)
            global_.linkage = "internal"
            global_.initializer = ir.Constant(_llvm_type(source.layout), None)
            self.globals[source.name] = global_
        for source in self.source.functions:
            ty = ir.FunctionType(_llvm_type(source.result),
                                 [_llvm_type(p.layout) for p in source.params])
            self.functions[source.name] = ir.Function(self.module, ty, name=source.name)
        for source in self.source.functions:
            self._function(source, self.functions[source.name])
        text = str(self.module)
        parsed = binding.parse_assembly(text)
        parsed.verify()
        return str(parsed), self.machine

    def _function(self, source: bir.Function, function: ir.Function) -> None:
        blocks = {block.name: function.append_basic_block(block.name)
                  for block in source.blocks}
        values: dict[str, ir.Value] = {}
        for param, llvm_value in zip(source.params, function.args):
            llvm_value.name = param.name
            values[param.name] = llvm_value

        entry_builder = ir.IRBuilder(blocks[source.entry])
        slots = {slot.name: entry_builder.alloca(_llvm_type(slot.layout), name=slot.name)
                 for slot in source.slots}
        root_index, value_root_count = _root_slots(source)
        in_entry = source.name == self.source.entry
        literal_roots = list(self.string_literals.values()) if in_entry else []
        nullary_roots = list(self.nullary_objects.items()) if in_entry else []
        root_count = value_root_count + len(literal_roots) + len(nullary_roots)
        root_frame = entry_builder.alloca(_ROOT_FRAME, name="root_frame")
        panic_frame_storage = entry_builder.alloca(_PANIC_FRAME, name="panic_frame")
        root_array_type = ir.ArrayType(_PTR, max(1, root_count))
        root_values = entry_builder.alloca(root_array_type, name="root_values")
        for index in range(root_count):
            pointer = entry_builder.gep(
                root_values, [ir.Constant(_I32, 0), ir.Constant(_I32, index)])
            entry_builder.store(ir.Constant(_PTR, None), pointer)
        if self.global_array is not None and source.name == self.source.entry:
            # Registered from the entry function, which does not return until
            # the program is over, so it sits below every other frame for the
            # whole run. It goes in before this function's own frame and comes
            # out after it, since `turkey_root_leave` insists the frame being
            # left is the one on top. It is *left*, rather than being a
            # permanent registration, because the runtime outlives any one
            # compiled module: a frame never popped would still be on the
            # runtime's list pointing into a module the engine has freed.
            entry_builder.call(self.runtime["turkey_root_enter"], [
                entry_builder.bitcast(self.global_frame, _PTR),
                entry_builder.bitcast(self.global_array, _PTR),
                ir.Constant(_I64, len(self.global_roots)),
                self._c_string(entry_builder, "<globals>", ".turkey.function"),
            ])
        # A function with nothing to root registers no frame. The collector
        # walks a list of frames; an empty one contributes nothing to a trace
        # and costs a push and a pop on every call, which for a leaf like
        # `Main#move` is most of what the call does.
        if root_count:
            entry_builder.call(self.runtime["turkey_root_enter"], [
                entry_builder.bitcast(root_frame, _PTR),
                entry_builder.bitcast(root_values, _PTR),
                ir.Constant(_I64, root_count),
                self._c_string(entry_builder, source.name, ".turkey.function"),
            ])
        # Entered at the function's own name with no position: line 0 is what
        # `capture_panic_trace` reads as "this frame has not reached a site
        # yet", and such a frame is left out of the trace.
        entry_builder.call(self.runtime["turkey_frame_enter"], [
            entry_builder.bitcast(panic_frame_storage, _PTR),
            entry_builder.bitcast(
                self._panic_site(bir.Frame(source.name, None, 0, 0)), _PTR),
        ])
        self._root_frame = root_frame
        self._root_values = root_values
        self._root_index = root_index
        self._panic_frame_storage = panic_frame_storage
        self._in_entry = source.name == self.source.entry
        for offset, (bytes_global, value_global, length) in enumerate(literal_roots):
            literal = entry_builder.call(self.runtime["turkey_string_new"], [
                entry_builder.bitcast(bytes_global, _PTR),
                ir.Constant(_I64, length),
            ])
            entry_builder.store(literal, value_global)
            pointer = entry_builder.gep(root_values, [
                ir.Constant(_I32, 0),
                ir.Constant(_I32, value_root_count + offset),
            ])
            entry_builder.store(literal, pointer)
        for offset, ((kind, tag), value_global) in enumerate(nullary_roots):
            made = entry_builder.call(self.runtime["turkey_object_new"], [
                ir.Constant(_I32, kind), ir.Constant(_I32, tag),
                ir.Constant(_I64, 0), ir.Constant(_I64, 0),
            ])
            entry_builder.store(made, value_global)
            pointer = entry_builder.gep(root_values, [
                ir.Constant(_I32, 0),
                ir.Constant(_I32, value_root_count + len(literal_roots) + offset),
            ])
            entry_builder.store(made, pointer)
        self._slot_layouts = {slot.name: slot.layout for slot in source.slots}
        for param in source.params:
            if param.name in root_index:
                self._set_root(entry_builder, param.name, values[param.name])
        phis: dict[tuple[str, str], ir.PhiInstr] = {}
        builders: dict[str, ir.IRBuilder] = {}
        for block in source.blocks:
            builder = entry_builder if block.name == source.entry else ir.IRBuilder(blocks[block.name])
            builders[block.name] = builder
            for param in block.params:
                phi = builder.phi(_llvm_type(param.layout), name=param.name)
                phis[(block.name, param.name)] = phi
                values[param.name] = phi
            for param in block.params:
                if param.name in root_index:
                    self._set_root(builder, param.name, values[param.name])

        for block in source.blocks:
            builder = builders[block.name]
            for instruction in block.instructions:
                if instruction.frame is not None:
                    self._frame_update(builder, instruction.frame)
                value, builder = self._instruction(
                    function, builder, instruction, values, slots)
                if instruction.result is not None:
                    values[instruction.result.name] = value
                    if instruction.result.name in root_index:
                        self._set_root(builder, instruction.result.name, value)
                if (instruction.op == "slot_store"
                        and instruction.args[0] in root_index):
                    self._set_root(builder, instruction.args[0],
                                   self._operand(instruction.args[1], values))
            term = block.terminator
            assert term is not None
            if isinstance(term, bir.Return):
                builder.call(self.runtime["turkey_frame_leave"], [
                    builder.bitcast(panic_frame_storage, _PTR)])
                if root_count:
                    builder.call(self.runtime["turkey_root_leave"], [
                        builder.bitcast(root_frame, _PTR)])
                self._leave_globals(builder)
                builder.ret(self._operand(term.value, values))
            elif isinstance(term, bir.Jump):
                builder.branch(blocks[term.target])
                target = next(b for b in source.blocks if b.name == term.target)
                for param, arg in zip(target.params, term.args):
                    phis[(target.name, param.name)].add_incoming(
                        self._operand(arg, values), builder.block)
            elif isinstance(term, bir.Branch):
                builder.cbranch(self._operand(term.condition, values),
                                blocks[term.yes], blocks[term.no])
            else:
                if term.frame is not None:
                    self._frame_update(builder, term.frame)
                if isinstance(term.message, str):
                    self._panic(builder, term.message)
                else:
                    builder.call(self.runtime["turkey_panic_string"], [
                        self._operand(term.message, values)])
                builder.call(self.runtime["turkey_frame_leave"], [
                    builder.bitcast(panic_frame_storage, _PTR)])
                if root_count:
                    builder.call(self.runtime["turkey_root_leave"], [
                        builder.bitcast(root_frame, _PTR)])
                self._leave_globals(builder)
                builder.ret(ir.Constant(function.function_type.return_type, None))

    def _leave_globals(self, builder: ir.IRBuilder) -> None:
        """Pop the globals frame, on every path out of the entry function."""
        if self._in_entry and self.global_array is not None:
            builder.call(self.runtime["turkey_root_leave"],
                         [builder.bitcast(self.global_frame, _PTR)])

    def _object_field(self, builder: ir.IRBuilder, pointer: ir.Value,
                      field: int) -> ir.Value:
        """The address of one named header field of a `TurkeyObject`."""
        object_ = builder.bitcast(pointer, _OBJECT.as_pointer())
        return builder.gep(object_, [ir.Constant(_I32, 0),
                                     ir.Constant(_I32, field)])

    def _object_slot(self, builder: ir.IRBuilder, pointer: ir.Value,
                     index: int) -> ir.Value:
        """The address of payload slot `index`, as an `i64*`."""
        object_ = builder.bitcast(pointer, _OBJECT.as_pointer())
        return builder.gep(object_, [ir.Constant(_I32, 0),
                                     ir.Constant(_I32, _OBJECT_SLOTS),
                                     ir.Constant(_I64, index)])

    def _global(self, builder: ir.IRBuilder, name: str) -> ir.Value:
        """The address a global's value lives at.

        A pointer-typed one lives in the permanently rooted array rather than
        in a global of its own, so that storing to it and rooting it are the
        same store.
        """
        index = self.global_roots.get(name)
        if index is None:
            return self.globals[name]
        return builder.gep(self.global_array, [ir.Constant(_I32, 0),
                                               ir.Constant(_I32, index)])

    def _cell_value(self, builder: ir.IRBuilder,
                    pointer: ir.Value) -> ir.Value:
        """The address of a `TurkeyCell`'s payload word.

        A cell's `pointer_value` flag decides only whether the collector traces
        the word; reading and writing it never consults the flag, so this is
        the whole of `turkey_cell_load` and `turkey_cell_store`.
        """
        cell = builder.bitcast(pointer, _CELL.as_pointer())
        return builder.gep(cell, [ir.Constant(_I32, 0), ir.Constant(_I32, 0)])

    def _array_element(self, builder: ir.IRBuilder, pointer: ir.Value,
                       index: ir.Value, layout: bir.Layout) -> tuple[ir.Value, int]:
        """The address of array element `index`, at the element's own width.

        An array is kind 2, so its `count` is the length and its
        `pointer_bitmap` slot is reused as the element width in bytes -- 1 for
        `Array Byte`, 4 for `Array Char`, 8 otherwise. The width is a function
        of the element layout, which is a compile-time constant here, so the
        stride is baked into the pointer type rather than read back out of the
        header.

        Unchecked, deliberately. `Prim.*` is spellable only from a library
        module (`modules.py`), and every one of the nineteen call sites in
        `turkey/lib` either bounds-checks first (`Data.Array`'s `Index`
        instance, via `bounds`) or indexes modulo the capacity (`Data.Map`).
        The check this replaces was against the *physical* capacity while
        `Data.Array.bounds` checks the *logical* length, and length <= capacity,
        so it could never be the one to fire on that path.
        """
        width = _layout_width(layout)
        element = _I8 if width == 1 else _I32 if width == 4 else _I64
        object_ = builder.bitcast(pointer, _OBJECT.as_pointer())
        base = builder.gep(object_, [ir.Constant(_I32, 0),
                                     ir.Constant(_I32, _OBJECT_SLOTS),
                                     ir.Constant(_I64, 0)])
        return builder.gep(builder.bitcast(base, element.as_pointer()),
                           [index]), width

    def _array_load(self, builder: ir.IRBuilder, pointer: ir.Value,
                    index: ir.Value, layout: bir.Layout) -> ir.Value:
        address, width = self._array_element(builder, pointer, index, layout)
        loaded = builder.load(address)
        # A narrow element is already its own type; a wide one is a raw 64-bit
        # pattern, the same as an object slot.
        return (self._from_i64(builder, loaded, layout) if width == 8
                else loaded)

    def _array_store(self, builder: ir.IRBuilder, pointer: ir.Value,
                     index: ir.Value, value: ir.Value,
                     layout: bir.Layout) -> None:
        address, width = self._array_element(builder, pointer, index, layout)
        builder.store(self._to_i64(builder, value) if width == 8 else value,
                      address)

    def _set_root(self, builder: ir.IRBuilder, name: str, value: ir.Value) -> None:
        pointer = builder.gep(self._root_values, [
            ir.Constant(_I32, 0), ir.Constant(_I32, self._root_index[name]),
        ])
        builder.store(value, pointer)

    def _frame_update(self, builder: ir.IRBuilder, frame: bir.Frame) -> None:
        pointer = builder.gep(self._panic_frame_storage, [
            ir.Constant(_I32, 0), ir.Constant(_I32, 1)])
        builder.store(builder.bitcast(self._panic_site(frame), _PTR), pointer)

    def _operand(self, operand: bir.Operand, values: dict[str, ir.Value]) -> ir.Value:
        if isinstance(operand, bir.Value):
            return values[operand.name]
        ty = _llvm_type(operand.layout)
        if operand.layout is bir.Layout.F64:
            return ir.Constant(ty, float(operand.value))
        if isinstance(ty, ir.PointerType) and int(operand.value) == 0:
            return ir.Constant(ty, None)
        return ir.Constant(ty, int(operand.value))

    def _instruction(self, function: ir.Function, builder: ir.IRBuilder,
                     instruction: bir.Instruction, values: dict[str, ir.Value],
                     slots: dict[str, ir.AllocaInstr]) -> tuple[ir.Value | None, ir.IRBuilder]:
        op = instruction.op
        args = [self._operand(arg, values) for arg in instruction.args
                if isinstance(arg, (bir.Value, bir.Constant))]
        if op == "slot_store":
            builder.store(self._operand(instruction.args[1], values), slots[instruction.args[0]])
            return None, builder
        if op == "slot_load":
            return builder.load(slots[instruction.args[0]], name=instruction.result.name), builder
        if op == "relabel":
            return args[0], builder
        if op == "box":
            value = builder.call(self.runtime["turkey_box"], [
                self._to_i64(builder, args[0]),
                ir.Constant(_I32, _layout_code(instruction.args[0].layout)),
            ])
            return value, self._propagate(function, builder)
        if op == "unbox":
            bits = builder.call(self.runtime["turkey_unbox"], [
                args[0], ir.Constant(_I32, _layout_code(instruction.result.layout)),
            ])
            value = self._from_i64(builder, bits, instruction.result.layout)
            return value, self._propagate(function, builder)
        if op == "global_store":
            builder.store(self._operand(instruction.args[1], values),
                          self._global(builder, instruction.args[0]))
            return None, builder
        if op == "global_load":
            return builder.load(self._global(builder, instruction.args[0]),
                                name=instruction.result.name), builder
        if op == "string_const":
            return builder.load(
                self.string_literals[instruction.args[0]][1],
                name=instruction.result.name), builder
        if op == "call":
            value = builder.call(self.functions[instruction.args[0]], args,
                                 name=instruction.result.name)
            return value, self._propagate(function, builder)
        if op.startswith("prim."):
            return self._primitive(function, builder, op[5:], args, instruction.result.layout)
        if op == "cell_new":
            bits = self._to_i64(builder, args[0])
            pointer = int(instruction.args[0].layout in (bir.Layout.PTR, bir.Layout.BOXED))
            value = builder.call(self.runtime["turkey_cell_new"],
                                 [bits, ir.Constant(_I32, pointer)])
            return value, self._propagate(function, builder)
        if op == "cell_load":
            bits = builder.load(self._cell_value(builder, args[0]))
            return self._from_i64(builder, bits, instruction.result.layout), builder
        if op == "cell_store":
            builder.store(self._to_i64(builder, args[1]),
                          self._cell_value(builder, args[0]))
            return None, builder
        if op == "object_new":
            if instruction.args[2] == "0":
                shared = self.nullary_objects[
                    (int(instruction.args[0]), int(instruction.args[1]))]
                return builder.load(shared, name=instruction.result.name), builder
            raw = [ir.Constant(_I32, int(instruction.args[0])),
                   ir.Constant(_I32, int(instruction.args[1])),
                   ir.Constant(_I64, int(instruction.args[2])),
                   ir.Constant(_I64, int(instruction.args[3]))]
            return builder.call(self.runtime["turkey_object_new"], raw), builder
        if op == "object_tag":
            return builder.load(
                self._object_field(builder, args[0], _OBJECT_TAG),
                name=instruction.result.name), builder
        if op == "object_get":
            bits = builder.load(
                self._object_slot(builder, args[0], int(instruction.args[1])))
            value = self._from_i64(builder, bits, instruction.result.layout)
            return value, builder
        if op == "object_set":
            builder.store(
                self._to_i64(builder, args[1]),
                self._object_slot(builder, args[0], int(instruction.args[1])))
            return None, builder
        if op == "array_new":
            return builder.call(self.runtime["turkey_array_new"],
                                [ir.Constant(_I64, int(instruction.args[0])),
                                 ir.Constant(_I64, 0),
                                 ir.Constant(_I32, int(instruction.args[1])),
                                 ir.Constant(_I32, _layout_code(
                                     bir.Layout(instruction.args[2])))]), builder
        if op == "array_get":
            return self._array_load(builder, args[0], args[1],
                                    instruction.result.layout), builder
        if op == "array_set":
            index = (ir.Constant(_I64, int(instruction.args[1]))
                     if isinstance(instruction.args[1], str) else args[1])
            value = args[1] if isinstance(instruction.args[1], str) else args[2]
            layout = instruction.args[-1].layout
            self._array_store(builder, args[0], index, value, layout)
            return None, builder
        if op == "closure_new":
            code = builder.ptrtoint(self.functions[instruction.args[0]], _I64)
            return builder.call(self.runtime["turkey_closure_new"], [
                code, ir.Constant(_I64, int(instruction.args[1])),
                ir.Constant(_I64, int(instruction.args[2])),
            ]), builder
        if op == "function_closure":
            thunk = self._closure_thunk(instruction.args[0])
            code = builder.ptrtoint(thunk, _I64)
            return builder.call(self.runtime["turkey_closure_new"], [
                code, ir.Constant(_I64, 0), ir.Constant(_I64, 0),
            ]), builder
        if op == "closure_capture":
            builder.call(self.runtime["turkey_closure_capture"], [
                args[0], ir.Constant(_I64, int(instruction.args[1])),
                self._to_i64(builder, args[1]),
            ])
            return None, builder
        if op == "closure_call":
            closure = args[0]
            # A closure is kind 3 with exactly two slots, code then
            # environment, so both are constant-offset loads.
            code = builder.load(self._object_slot(builder, closure, 0))
            environment = builder.inttoptr(
                builder.load(self._object_slot(builder, closure, 1)), _PTR)
            signature = ir.FunctionType(
                _llvm_type(instruction.result.layout),
                [_PTR, *(arg.type for arg in args[1:])],
            ).as_pointer()
            callee = builder.inttoptr(code, signature)
            value = builder.call(callee, [environment, *args[1:]],
                                 name=instruction.result.name)
            return value, self._propagate(function, builder)
        if op in ("scalar_eq", "float_eq"):
            return (builder.fcmp_ordered("==", args[0], args[1]) if op == "float_eq"
                    else builder.icmp_unsigned("==", args[0], args[1])), builder
        if op == "string_eq":
            value = builder.call(self.runtime["turkey_string_eq"], args)
            return builder.icmp_unsigned("!=", value, ir.Constant(_I32, 0)), builder
        raise Unsupported(f"no LLVM emission rule for {op}")

    def _closure_thunk(self, symbol: str) -> ir.Function:
        found = self.closure_thunks.get(symbol)
        if found is not None:
            return found
        target = self.functions[symbol]
        source = self.source_functions[symbol]
        original = target.function_type
        thunk = ir.Function(
            self.module,
            ir.FunctionType(_PTR, [_PTR, *(_PTR for _ in original.args)]),
            name=symbol + "_closure",
        )
        builder = ir.IRBuilder(thunk.append_basic_block("entry"))
        arguments = []
        for boxed, expected, parameter in zip(thunk.args[1:], original.args,
                                              source.params):
            if parameter.layout in (bir.Layout.PTR, bir.Layout.BOXED):
                arguments.append(boxed)
            else:
                bits = builder.call(self.runtime["turkey_unbox"], [
                    boxed, ir.Constant(_I32, _layout_code(parameter.layout)),
                ])
                arguments.append(self._from_i64_type(builder, bits, expected))
        result = builder.call(target, arguments)
        if source.result in (bir.Layout.PTR, bir.Layout.BOXED):
            boxed_result = result
        elif source.result is bir.Layout.UNIT:
            boxed_result = ir.Constant(_PTR, None)
        else:
            boxed_result = builder.call(self.runtime["turkey_box"], [
                self._to_i64(builder, result),
                ir.Constant(_I32, _layout_code(source.result)),
            ])
        builder.ret(boxed_result)
        self.closure_thunks[symbol] = thunk
        return thunk

    def _primitive(self, function: ir.Function, builder: ir.IRBuilder, name: str,
                   args: list[ir.Value], layout: bir.Layout) -> tuple[ir.Value, ir.IRBuilder]:
        array_layout: bir.Layout | None = None
        suffix = name.rpartition(".")[2]
        if suffix in {layout.value for layout in bir.Layout}:
            array_layout = bir.Layout(suffix)
            name = name.rpartition(".")[0]
        overflow = {"intAdd": builder.sadd_with_overflow,
                    "intSub": builder.ssub_with_overflow,
                    "intMul": builder.smul_with_overflow}
        if name in overflow:
            pair = overflow[name](args[0], args[1])
            value = builder.extract_value(pair, 0)
            failed = builder.extract_value(pair, 1)
            builder = self._guard(function, builder, failed,
                                  f"integer overflow in { {'intAdd': '+', 'intSub': '-', 'intMul': '*'}[name] }")
            return value, builder
        if name == "intNeg":
            pair = builder.ssub_with_overflow(ir.Constant(_I64, 0), args[0])
            value, failed = builder.extract_value(pair, 0), builder.extract_value(pair, 1)
            builder = self._guard(function, builder, failed, "integer overflow in unary -")
            return value, builder
        if name in ("intDiv", "intRem"):
            zero = builder.icmp_signed("==", args[1], ir.Constant(_I64, 0))
            builder = self._guard(function, builder, zero,
                                  "division by zero" if name == "intDiv"
                                  else "remainder by zero")
            if name == "intDiv":
                minimum = builder.icmp_signed("==", args[0], ir.Constant(_I64, -(1 << 63)))
                minus_one = builder.icmp_signed("==", args[1], ir.Constant(_I64, -1))
                builder = self._guard(function, builder, builder.and_(minimum, minus_one),
                                      "integer overflow in /")
                return builder.sdiv(args[0], args[1]), builder
            # LLVM defines the overflowing quotient's remainder as zero only
            # indirectly; avoid depending on target lowering for this pair.
            special = builder.and_(
                builder.icmp_signed("==", args[0], ir.Constant(_I64, -(1 << 63))),
                builder.icmp_signed("==", args[1], ir.Constant(_I64, -1)))
            ordinary = function.append_basic_block("remainder")
            merged = function.append_basic_block("remainder.done")
            special_block = function.append_basic_block("remainder.min")
            builder.cbranch(special, special_block, ordinary)
            special_builder = ir.IRBuilder(special_block)
            special_builder.branch(merged)
            ordinary_builder = ir.IRBuilder(ordinary)
            remainder = ordinary_builder.srem(args[0], args[1])
            ordinary_builder.branch(merged)
            final = ir.IRBuilder(merged)
            phi = final.phi(_I64)
            phi.add_incoming(ir.Constant(_I64, 0), special_block)
            phi.add_incoming(remainder, ordinary)
            return phi, final
        if name in ("intShl", "intShr"):
            negative = builder.icmp_signed("<", args[1], ir.Constant(_I64, 0))
            large = builder.icmp_signed(">", args[1], ir.Constant(_I64, 63))
            builder = self._guard(function, builder, builder.or_(negative, large),
                                  "shift amount is not in 0..63")
            return (builder.shl(args[0], args[1]) if name == "intShl"
                    else builder.ashr(args[0], args[1])), builder
        binary = {
            "intAddWrapping": builder.add, "intSubWrapping": builder.sub,
            "intMulWrapping": builder.mul, "intAnd": builder.and_, "intOr": builder.or_,
            "intXor": builder.xor, "floatAdd": builder.fadd, "floatSub": builder.fsub,
            "floatMul": builder.fmul, "floatDiv": builder.fdiv,
        }
        if name in binary:
            return binary[name](args[0], args[1]), builder
        if name == "intNegWrapping": return builder.sub(ir.Constant(_I64, 0), args[0]), builder
        if name == "intNot": return builder.xor(args[0], ir.Constant(_I64, -1)), builder
        if name == "floatNeg": return builder.fsub(ir.Constant(_F64, -0.0), args[0]), builder
        if name in ("intEq", "byteEq", "charEq", "boolEq"):
            return builder.icmp_signed("==", args[0], args[1]), builder
        if name in ("intLt", "byteLt", "charLt", "boolLt"):
            pred = "<"
            return (builder.icmp_unsigned(pred, args[0], args[1]) if name in ("byteLt", "charLt", "boolLt")
                    else builder.icmp_signed(pred, args[0], args[1])), builder
        if name == "floatEq": return builder.fcmp_ordered("==", args[0], args[1]), builder
        if name == "floatLt": return builder.fcmp_ordered("<", args[0], args[1]), builder
        if name == "floatGt": return builder.fcmp_ordered(">", args[0], args[1]), builder
        if name == "floatLte": return builder.fcmp_ordered("<=", args[0], args[1]), builder
        if name == "floatGte": return builder.fcmp_ordered(">=", args[0], args[1]), builder
        if name == "floatIsNaN": return builder.fcmp_unordered("uno", args[0], args[0]), builder
        if name == "not": return builder.xor(args[0], ir.Constant(_I1, 1)), builder
        if name == "intToFloat": return builder.sitofp(args[0], _F64), builder
        if name in ("byteToInt", "charToInt"): return builder.zext(args[0], _I64), builder
        if name == "byteFromInt":
            negative = builder.icmp_signed("<", args[0], ir.Constant(_I64, 0))
            large = builder.icmp_signed(">", args[0], ir.Constant(_I64, 255))
            builder = self._guard(function, builder, builder.or_(negative, large),
                                  "value is not a Byte")
            return builder.trunc(args[0], _I8), builder
        if name == "charFromInt":
            negative = builder.icmp_signed("<", args[0], ir.Constant(_I64, 0))
            large = builder.icmp_signed(">", args[0], ir.Constant(_I64, 0x10ffff))
            low = builder.icmp_signed(">=", args[0], ir.Constant(_I64, 0xd800))
            high = builder.icmp_signed("<=", args[0], ir.Constant(_I64, 0xdfff))
            invalid = builder.or_(builder.or_(negative, large), builder.and_(low, high))
            builder = self._guard(function, builder, invalid,
                                  "value is not a Unicode scalar")
            return builder.trunc(args[0], _I32), builder
        if name == "charIsScalar":
            large = builder.icmp_unsigned(">", args[0], ir.Constant(_I64, 0x10ffff))
            low = builder.icmp_unsigned(">=", args[0], ir.Constant(_I64, 0xd800))
            high = builder.icmp_unsigned("<=", args[0], ir.Constant(_I64, 0xdfff))
            return builder.not_(builder.or_(large, builder.and_(low, high))), builder
        if name == "floatBits": return builder.bitcast(args[0], _I64), builder
        if name == "floatFromBits": return builder.bitcast(args[0], _F64), builder
        if name == "floatFitsInt":
            ordered = builder.fcmp_ordered("ord", args[0], args[0])
            low = builder.fcmp_ordered(">=", args[0], ir.Constant(_F64, float(-(1 << 63))))
            high = builder.fcmp_ordered("<", args[0], ir.Constant(_F64, float(1 << 63)))
            return builder.and_(ordered, builder.and_(low, high)), builder
        if name == "floatTruncate":
            ordered = builder.fcmp_ordered("ord", args[0], args[0])
            low = builder.fcmp_ordered(">=", args[0], ir.Constant(_F64, float(-(1 << 63))))
            high = builder.fcmp_ordered("<", args[0], ir.Constant(_F64, float(1 << 63)))
            builder = self._guard(function, builder,
                                  builder.not_(builder.and_(ordered, builder.and_(low, high))),
                                  "Float is not representable as an Int")
            return builder.fptosi(args[0], _I64), builder
        runtime = {
            "intToString": "turkey_int_to_string", "floatToString": "turkey_float_to_string",
            "charToString": "turkey_char_to_string", "stringConcat": "turkey_string_concat",
            "print": "turkey_print", "write": "turkey_write",
            "stringByteLength": "turkey_string_byte_length",
            "stringByteAt": "turkey_string_byte_at",
            "stringDecodeAt": "turkey_string_decode_at",
            "stringNextIndex": "turkey_string_next_index",
            "stringSlice": "turkey_string_slice",
            "stringFind": "turkey_string_find",
            "stringRfind": "turkey_string_rfind",
            "stringToByteStorage": "turkey_string_to_byte_storage",
            "stringFromBytes": "turkey_string_from_bytes",
            "stringConcatAll": "turkey_string_concat_all",
            "floatParse": "turkey_float_parse",
            "floatFmod": "turkey_float_fmod",
            "floatRemainder": "turkey_float_remainder",
            "floatFloor": "turkey_float_floor",
            "floatCeil": "turkey_float_ceil",
            "floatRound": "turkey_float_round",
            "floatTrunc": "turkey_float_trunc",
        }.get(name)
        if runtime:
            value = builder.call(self.runtime[runtime], args)
            return value, self._propagate(function, builder)
        if name == "stringIsValidUtf8":
            raw = builder.call(self.runtime["turkey_string_is_valid_utf8"], args)
            value = builder.icmp_unsigned("!=", raw, ir.Constant(_I32, 0))
            return value, self._propagate(function, builder)
        if name == "floatCanParse":
            raw = builder.call(self.runtime["turkey_float_can_parse"], args)
            value = builder.icmp_unsigned("!=", raw, ir.Constant(_I32, 0))
            return value, self._propagate(function, builder)
        if name in ("stringEq", "stringLt"):
            raw = builder.call(self.runtime[
                "turkey_string_eq" if name == "stringEq" else "turkey_string_lt"], args)
            return builder.icmp_unsigned("!=", raw, ir.Constant(_I32, 0)), builder
        if name == "arrayLength":
            return builder.load(
                self._object_field(builder, args[0], _OBJECT_COUNT)), builder
        if name == "arrayGet":
            requested = array_layout or layout
            value = self._array_load(builder, args[0], args[1], requested)
            # `array_layout` is the width the element was allocated at; the
            # result may still want a different *view* of those same bits.
            return (value if requested is layout
                    else self._from_i64_type(
                        builder, self._to_i64(builder, value),
                        _llvm_type(layout))), builder
        if name == "arraySet":
            requested = array_layout or bir.Layout.BOXED
            self._array_store(builder, args[0], args[1], args[2], requested)
            return ir.Constant(_I8, 0), builder
        if name in ("arrayNew", "arrayNewUninit"):
            initial = (self._to_i64(builder, args[1]) if len(args) == 2
                       else ir.Constant(_I64, 0))
            # The element layout is carried by specialization in later phases;
            # scalar/pointer tracing is conservatively selected at the call.
            element_layout = (array_layout if array_layout is not None else
                              bir.Layout.PTR if len(args) == 2 and
                              isinstance(args[1].type, ir.PointerType) else bir.Layout.I64)
            width = 1 if array_layout is bir.Layout.I8 else 4 if array_layout is bir.Layout.I32 else 8
            return builder.call(self.runtime["turkey_array_new"],
                                [args[0], initial, ir.Constant(_I32, width),
                                 ir.Constant(_I32, _layout_code(element_layout))]), builder
        if name == "error":
            # Runtime strings are length-delimited, while panic messages are C
            # strings. A dedicated runtime entry is added with full panic ABI;
            # for now preserve a stable diagnostic instead of reading past it.
            self._panic(builder, "error")
            return ir.Constant(_llvm_type(layout), None), builder
        raise Unsupported(f"LLVM primitive Prim.{name} is not implemented")

    def _guard(self, function: ir.Function, builder: ir.IRBuilder,
               failed: ir.Value, message: str | None) -> ir.IRBuilder:
        bad = function.append_basic_block("panic")
        good = function.append_basic_block("checked")
        builder.cbranch(failed, bad, good)
        panic_builder = ir.IRBuilder(bad)
        if message is not None:
            self._panic(panic_builder, message)
        panic_builder.call(self.runtime["turkey_frame_leave"], [
            panic_builder.bitcast(self._panic_frame_storage, _PTR)])
        panic_builder.call(self.runtime["turkey_root_leave"], [
            panic_builder.bitcast(self._root_frame, _PTR)])
        self._leave_globals(panic_builder)
        panic_builder.ret(ir.Constant(function.function_type.return_type, None))
        return ir.IRBuilder(good)

    def _propagate(self, function: ir.Function,
                   builder: ir.IRBuilder) -> ir.IRBuilder:
        panicked = builder.load(self.panic_flag, name="panicked")
        return self._guard(
            function, builder,
            builder.icmp_unsigned("!=", panicked, ir.Constant(_I32, 0)), None,
        )

    def _panic(self, builder: ir.IRBuilder, message: str) -> None:
        builder.call(self.runtime["turkey_panic"], [
            self._c_string(builder, message, ".turkey.panic")])

    def _c_string(self, builder: ir.IRBuilder, value: str,
                  prefix: str) -> ir.Value:
        return builder.bitcast(self._c_string_global(value, prefix), _PTR)

    def _c_string_global(self, value: str, prefix: str) -> ir.GlobalVariable:
        """The NUL-terminated bytes, as a private constant.

        Interned: a panic site names its function and its file, and a function
        with twenty of them would otherwise emit its own name twenty times.
        """
        found = self.c_strings.get(value)
        if found is not None:
            return found
        data = value.encode("utf-8") + b"\0"
        array = ir.Constant(ir.ArrayType(_I8, len(data)), bytearray(data))
        glob = ir.GlobalVariable(self.module, array.type,
                                 name=f"{prefix}.{self.string_count}")
        self.string_count += 1
        glob.global_constant = True
        glob.linkage = "private"
        glob.initializer = array
        self.c_strings[value] = glob
        return glob

    def _panic_site(self, frame: bir.Frame) -> ir.GlobalVariable:
        """The `PanicSite` constant for one source position.

        One per distinct position rather than per mention, so a loop body that
        updates the frame before each of its operations shares whatever sites
        repeat, and the module holds a table rather than a copy per store.
        """
        found = self.panic_sites.get(frame)
        if found is not None:
            return found
        empty = ir.Constant(_PTR, None)
        site = ir.Constant(_PANIC_SITE, [
            self._c_string_global(frame.function, ".turkey.frame").gep(
                [ir.Constant(_I32, 0), ir.Constant(_I32, 0)]),
            empty if frame.file is None else
            self._c_string_global(frame.file, ".turkey.file").gep(
                [ir.Constant(_I32, 0), ir.Constant(_I32, 0)]),
            ir.Constant(_I64, frame.line), ir.Constant(_I64, frame.col),
        ])
        glob = ir.GlobalVariable(self.module, _PANIC_SITE,
                                 name=f".turkey.site.{len(self.panic_sites)}")
        glob.global_constant = True
        glob.linkage = "private"
        glob.initializer = site
        self.panic_sites[frame] = glob
        return glob

    def _to_i64(self, builder: ir.IRBuilder, value: ir.Value) -> ir.Value:
        if value.type == _F64: return builder.bitcast(value, _I64)
        if isinstance(value.type, ir.PointerType): return builder.ptrtoint(value, _I64)
        if value.type.width < 64: return builder.zext(value, _I64)
        return value

    def _from_i64(self, builder: ir.IRBuilder, value: ir.Value,
                  layout: bir.Layout) -> ir.Value:
        target = _llvm_type(layout)
        return self._from_i64_type(builder, value, target)

    def _from_i64_type(self, builder: ir.IRBuilder, value: ir.Value,
                       target: ir.Type) -> ir.Value:
        if target == _F64: return builder.bitcast(value, _F64)
        if isinstance(target, ir.PointerType): return builder.inttoptr(value, target)
        if target.width < 64: return builder.trunc(value, target)
        return value


_RUNTIME_SYMBOLS = (
    "turkey_string_new", "turkey_string_concat", "turkey_int_to_string",
    "turkey_float_to_string", "turkey_float_parse", "turkey_float_can_parse",
    "turkey_float_fmod", "turkey_float_remainder", "turkey_float_floor",
    "turkey_float_ceil", "turkey_float_round", "turkey_float_trunc",
    "turkey_char_to_string", "turkey_print",
    "turkey_write", "turkey_cell_new", "turkey_cell_load", "turkey_cell_store",
    "turkey_panic", "turkey_panic_string", "turkey_panicked",
    "turkey_frame_enter", "turkey_frame_leave", "turkey_frame_count",
    "turkey_frame_function", "turkey_frame_file", "turkey_frame_line",
    "turkey_frame_col",
    "turkey_string_byte_length", "turkey_string_byte_at",
    "turkey_string_decode_at", "turkey_string_next_index",
    "turkey_string_slice", "turkey_string_find", "turkey_string_rfind",
    "turkey_string_to_byte_storage", "turkey_string_from_bytes",
    "turkey_string_is_valid_utf8", "turkey_string_concat_all",
    "turkey_string_eq", "turkey_string_lt",
    "turkey_object_new", "turkey_object_tag", "turkey_object_get",
    "turkey_object_set", "turkey_object_get_as", "turkey_object_set_as",
    "turkey_box", "turkey_unbox",
    "turkey_array_new", "turkey_array_length", "turkey_array_get",
    "turkey_array_set", "turkey_array_get_as", "turkey_array_set_as",
    "turkey_array_get_boxed", "turkey_array_set_boxed",
    "turkey_closure_new", "turkey_closure_code",
    "turkey_closure_environment", "turkey_closure_capture",
    "turkey_root_enter", "turkey_root_leave",
)
# The runtime entry points Python itself calls, rather than generated code:
# panic reporting at the JIT boundary and the collector's diagnostics. Written
# out because a linked-in module has no `ctypes.CDLL` to read `restype` off,
# and a wrong `restype` here is a silently truncated pointer.
_RUNTIME_CALLS: dict[str, tuple[object, tuple]] = {
    "turkey_panic_clear": (None, ()),
    "turkey_panicked": (ctypes.c_int32, ()),
    "turkey_panic_message": (ctypes.c_char_p, ()),
    "turkey_frame_count": (ctypes.c_int64, ()),
    "turkey_frame_function": (ctypes.c_char_p, (ctypes.c_int64,)),
    "turkey_frame_file": (ctypes.c_char_p, (ctypes.c_int64,)),
    "turkey_frame_line": (ctypes.c_int64, (ctypes.c_int64,)),
    "turkey_frame_col": (ctypes.c_int64, (ctypes.c_int64,)),
    "turkey_collect": (None, ()),
    "turkey_gc_set_stress": (None, (ctypes.c_int32,)),
    "turkey_heap_objects": (ctypes.c_int64, ()),
    "turkey_collection_count": (ctypes.c_int64, ()),
    "turkey_layout_reconciliations": (ctypes.c_int64, ()),
}
_runtime_ir: str | None = None


class _Runtime:
    """The runtime, reached through the engine that compiled it.

    It used to be a `ctypes.CDLL` over a dynamic library, and callers still
    spell it that way -- `module.runtime.turkey_heap_objects()`. What changed
    is where the code is: the runtime is now linked into the JIT module, so
    there is exactly one copy of its state and the optimizer can see through
    it, and an address comes from the engine rather than from `dlsym`.
    """

    def __init__(self, engine: binding.ExecutionEngine) -> None:
        self._engine = engine
        self._resolved: dict[str, object] = {}

    def __getattr__(self, name: str):
        found = self._resolved.get(name)
        if found is None:
            if name not in _RUNTIME_CALLS:
                raise AttributeError(name)
            result, arguments = _RUNTIME_CALLS[name]
            address = self._engine.get_function_address(name)
            if not address:
                raise Unsupported(f"the runtime did not define {name}")
            found = ctypes.CFUNCTYPE(result, *arguments)(address)
            self._resolved[name] = found
        return found


def _runtime_source() -> Path:
    root = Path(__file__).resolve().parent.parent
    source = root / "runtime" / "turkey_runtime.c"
    if not source.is_file():
        source = Path(sys.prefix) / "runtime" / "turkey_runtime.c"
    if not source.is_file():
        raise Unsupported("the packaged Turkey native runtime source is missing")
    return source


def _runtime_module(triple: str) -> str:
    """The runtime as LLVM IR, to be linked into the module being compiled.

    Compiled rather than shipped: the IR has a target baked into it, so it
    cannot be a build artifact the way the C source can. It is cached on disk
    under a key that covers the source, the target and the compiler, because
    `turkey run` is a fresh process each time -- `tests/test_programs.py`
    starts about forty of them -- and none of them should pay for this twice.
    """
    global _runtime_ir
    if _runtime_ir is not None:
        return _runtime_ir
    source = _runtime_source()
    try:
        version = subprocess.run(["cc", "--version"], check=True,
                                 capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Unsupported(f"could not run the C compiler: {exc}") from exc
    key = hashlib.sha256(
        b"\0".join([source.read_bytes(), triple.encode(), version.encode()])
    ).hexdigest()[:32]
    cached = Path(tempfile.gettempdir()) / f"turkey-runtime-{key}.ll"
    if not cached.is_file():
        command = ["cc", "-std=c11", "-O2", "-fPIC", "-S", "-emit-llvm",
                   str(source), "-o", str(cached) + f".{os.getpid()}"]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            # Renamed into place so that two processes racing here cannot leave
            # a half-written file behind for a third to read.
            os.replace(command[-1], cached)
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = (exc.stderr.strip()
                      if isinstance(exc, subprocess.CalledProcessError)
                      else str(exc))
            raise Unsupported(
                f"could not build the Turkey runtime: {detail}") from exc
    _runtime_ir = _retarget(cached.read_text(encoding="utf-8"))
    return _runtime_ir


# Function attributes the C compiler attaches for the machine *it* was going to
# emit for, which are wrong for the machine the JIT emits for.
# `probe-stack="__chkstk_darwin"` is fatal -- the engine's target machine has no
# such probing method and LLVM aborts the process with "Unsupported stack
# probing method" rather than diagnosing it -- and `target-cpu`/`target-features`
# name a specific Apple core, which the generic target machine reports as
# unrecognised for every feature on every compile.
_RETARGET = re.compile(
    r'\s*"(?:probe-stack|target-cpu|target-features)"="[^"]*"')


def _retarget(text: str) -> str:
    return _RETARGET.sub("", text)


@dataclass
class NativeModule:
    engine: binding.ExecutionEngine
    module: binding.ModuleRef
    runtime: _Runtime
    entry: str
    result: bir.Layout

    def execute(self) -> None:
        self.runtime.turkey_panic_clear()
        self.runtime.turkey_gc_set_stress(int("TURKEY_GC_STRESS" in os.environ))
        address = self.engine.get_function_address(self.entry)
        ctypes.CFUNCTYPE(_ctype(self.result))(address)()
        self.runtime.turkey_collect()
        if self.runtime.turkey_panicked():
            raw = self.runtime.turkey_panic_message()
            panic = TurkeyPanic(raw.decode("utf-8", "replace"))
            for index in range(self.runtime.turkey_frame_count()):
                function = self.runtime.turkey_frame_function(index)
                file = self.runtime.turkey_frame_file(index)
                line = self.runtime.turkey_frame_line(index)
                col = self.runtime.turkey_frame_col(index)
                span = None if line == 0 else Span(
                    line, col, None if file is None else file.decode("utf-8", "replace"))
                panic.add_frame(function.decode("utf-8", "replace"), span)
            raise panic


def _ctype(layout: bir.Layout):
    return {
        bir.Layout.UNIT: ctypes.c_uint8, bir.Layout.I1: ctypes.c_bool,
        bir.Layout.I8: ctypes.c_uint8, bir.Layout.I32: ctypes.c_uint32,
        bir.Layout.I64: ctypes.c_int64, bir.Layout.F64: ctypes.c_double,
        bir.Layout.PTR: ctypes.c_void_p, bir.Layout.BOXED: ctypes.c_void_p,
    }[layout]


def _optimize(module, machine) -> None:
    """Run an -O2 pipeline over the module, on either pass-manager API.

    LLVM's legacy pass manager is gone from llvmlite 0.45 onwards, and with it
    `PassManagerBuilder`. The replacement takes the target machine, which the
    legacy one never did, so the two are spelled differently enough to be worth
    one branch rather than a shim: `speed_level=2` is `opt_level = 2`.
    """
    if hasattr(binding, "PassManagerBuilder"):
        manager = binding.ModulePassManager()
        builder = binding.PassManagerBuilder()
        builder.opt_level = 2
        builder.populate(manager)
        manager.run(module)
        return
    options = binding.create_pipeline_tuning_options(speed_level=2)
    passes = binding.create_pass_builder(machine, options)
    passes.getModulePassManager().run(module, passes)


def generate(program: CProgram, decls: DeclTable, main: str = "main") -> str:
    source = lower(program, decls, main)
    return _Emitter(source).emit()[0]


def compile(program: CProgram, decls: DeclTable, main: str = "main") -> NativeModule:
    source = lower(program, decls, main)
    emitter = _Emitter(source)
    text, machine = emitter.emit()
    try:
        binding.check_jit_execution()
    except OSError as exc:
        raise Unsupported("this host does not permit JIT execution") from exc
    module = binding.parse_assembly(text)
    module.verify()
    # Linked before optimizing, which is the whole point: with the runtime in
    # the module, allocation's fast path can inline into its caller and the
    # `turkey_has_panicked` load can be forwarded across calls the optimizer
    # can now see the insides of.
    runtime_module = binding.parse_assembly(_runtime_module(module.triple))
    runtime_module.verify()
    module.link_in(runtime_module)
    _optimize(module, machine)
    backing = binding.parse_assembly("")
    engine = binding.create_mcjit_compiler(backing, machine)
    engine.add_module(module)
    engine.finalize_object()
    engine.run_static_constructors()
    entry = next(fn for fn in source.functions if fn.name == source.entry)
    return NativeModule(engine, module, _Runtime(engine), source.entry,
                        entry.result)


def execute(program: CProgram, decls: DeclTable, main: str = "main",
            filename: str = "<input>") -> None:
    compile(program, decls, main).execute()


__all__ = ["NativeModule", "compile", "execute", "generate"]
