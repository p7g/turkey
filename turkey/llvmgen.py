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
# `RootFrame` from runtime/turkey_runtime.c: {previous, function_name, count,
# values, live}. `live` is a bitmap of which slots of `values` hold a live
# pointer at the safepoint about to run; see `_safepoint_live`. Slot 64 and up
# is always scanned, which is how the frames the runtime itself builds -- and
# the permanently live ones this module registers -- stay describable.
_ROOT_FRAME = ir.LiteralStructType([_PTR, _PTR, _I64, _PTR, _I64])
_ROOT_FRAME_LIVE = 4
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


def _value_layouts(function: bir.Function) -> dict[str, bir.Layout]:
    layouts = {value.name: value.layout
               for value in list(function.params) + list(function.slots)}
    for block in function.blocks:
        for param in block.params:
            layouts[param.name] = param.layout
        for instruction in block.instructions:
            if instruction.result is not None:
                layouts[instruction.result.name] = instruction.result.layout
    return layouts


def _safepoint_live(function: bir.Function) -> dict[tuple[str, int], frozenset[str]]:
    """Which pointers are live across each individual safepoint.

    A root set is a property of a program point, not of a function. This used
    to union the per-point sets and root everything in the union for the whole
    call, which let one cold call site decide what the hot path costs:
    `Main#inc` in the brainfuck benchmark has five roots and exactly two
    safepoints, and both safepoints are the cold out-of-bounds calls, so every
    *in-range* access paid for a frame it could not use. Keeping the sets apart
    is the same dataflow -- ordinary backward liveness over the CFG -- with the
    answer not thrown away.

    Two wrinkles. A value passed *to* a safepoint is live across it, since the
    callee holds it while it may collect; a value the safepoint *returns* is
    not, since it does not exist until the collection is over. And a slot is
    function-wide mutable storage, so it is killed by a store rather than by a
    definition, while `bir.check` rebuilds the SSA scope per block, so an
    instruction result cannot outlive its block and a block parameter is killed
    at the top of the block that declares it.
    """
    layouts = _value_layouts(function)
    tracked = {name for name, layout in layouts.items()
               if layout in (bir.Layout.PTR, bir.Layout.BOXED)}
    if not tracked:
        return {}
    declared = {block.name: {param.name for param in block.params}
                for block in function.blocks}

    def uses(instruction: bir.Instruction) -> set[str]:
        found = {operand.name for operand in _operands(instruction)}
        if instruction.op == "slot_load":
            found.add(instruction.args[0])
        return found & tracked

    def defines(instruction: bir.Instruction) -> set[str]:
        found = set()
        if instruction.result is not None:
            found.add(instruction.result.name)
        if instruction.op == "slot_store":
            found.add(instruction.args[0])
        return found & tracked

    def out_of(block: bir.Block, live_in: dict[str, set[str]]) -> set[str]:
        assert block.terminator is not None
        live = {operand.name
                for operand in _terminator_operands(block.terminator)} & tracked
        for name in _successors(block.terminator):
            live |= live_in[name]
        return live

    live_in: dict[str, set[str]] = {block.name: set() for block in function.blocks}
    changed = True
    while changed:
        changed = False
        for block in reversed(function.blocks):
            live = out_of(block, live_in)
            for instruction in reversed(block.instructions):
                live -= defines(instruction)
                live |= uses(instruction)
            live -= declared[block.name]
            if live != live_in[block.name]:
                live_in[block.name] = live
                changed = True

    found: dict[tuple[str, int], frozenset[str]] = {}
    for block in function.blocks:
        live = out_of(block, live_in)
        for index in reversed(range(len(block.instructions))):
            instruction = block.instructions[index]
            live -= defines(instruction)
            if _is_safepoint(instruction.op):
                found[(block.name, index)] = frozenset(live | uses(instruction))
            live |= uses(instruction)
    return found


# How many roots one frame can describe with a bitmap. The runtime scans a slot
# at or above this index unconditionally, so a function needing more than this
# many falls back to what this all did before: every slot always live, and the
# array zeroed on entry. The most any conformance program asks for is thirteen.
_MAPPED_ROOTS = 64


def _root_slots(
    function: bir.Function,
) -> tuple[dict[str, int], int, dict[tuple[str, int], frozenset[str]]]:
    """Stack storage in the exact-root frame, and what is live at each safepoint.

    A pointer needs a root only where a collection can happen while it is live,
    and only *at* that collection. Rooting every pointer in the function --
    which is what this did, and what `LLVM-BACKEND.md` allowed as a first
    implementation -- costs a store per definition and, worse, is unremovable:
    the frame's address escapes into `turkey_root_enter`, so no LLVM pass may
    drop a store into it.

    Indices are assigned in a fixed order so the emitted bitmaps are stable
    between runs; nothing else depends on the numbering.
    """
    live = _safepoint_live(function)
    index: dict[str, int] = {}
    for key in sorted(live):
        for name in sorted(live[key]):
            if name not in index:
                index[name] = len(index)
    return index, len(index), live


def _frame_region(function: bir.Function,
                  present: dict[str, bool]) -> dict[str, bool]:
    """The blocks a frame has to stay registered across, given where it is read.

    Every block that reads the frame, plus every block on a cycle through one:
    registration then sinks onto the edges into that set and pops on the edges
    out of it, which alternate along any path and so keep the balance
    `turkey_root_leave`'s "the frame being left is the one on top" insists on.

    The cycles are the whole of what is added, and they are what stops a loop
    with a call in it from registering a frame per iteration. Widening any
    further -- to every block that has a reader both behind and ahead, which is
    the natural way to say "between two safepoints" -- puts the frame straight
    back on the hot path: `Main#inc` checks two indices, and each check's cold
    arm rejoins the fast one, so every block between the two checks has a call
    behind it and a call ahead of it without either being on the path that
    actually runs.

    Neither reachability alone nor dominance alone would do either. A safepoint
    is reachable from `Main#inc`'s entry, so reachability registers there; and
    the two cold blocks' nearest common dominator *is* the entry, so dominance
    registers there too.
    """
    successors = {block.name: _successors(block.terminator)
                  for block in function.blocks}
    predecessors: dict[str, list[str]] = {block.name: [] for block in function.blocks}
    for name, targets in successors.items():
        for target in targets:
            predecessors[target].append(name)

    def closure(start: str, edges: dict[str, list[str]]) -> set[str]:
        seen = {start}
        stack = [start]
        while stack:
            for name in edges[stack.pop()]:
                if name not in seen:
                    seen.add(name)
                    stack.append(name)
        return seen

    region = {name for name, value in present.items() if value}
    for name in sorted(region):
        region |= closure(name, successors) & closure(name, predecessors)
    return {block.name: block.name in region for block in function.blocks}


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
        # A generated heap access never touches a frame. Saying so is worth a
        # third of the brainfuck benchmark, because the panic-site store --
        # `_frame_update`, one store before anything that can panic -- is into
        # an alloca whose address escapes into `turkey_frame_enter`, and
        # nothing generated here carried alias metadata. So every load of an
        # array header, its length or its storage after that store had to be
        # redone, which is why the second bounds check reappears as soon as a
        # function with two array accesses is inlined into its caller.
        #
        # Scoped noalias rather than TBAA, deliberately. A TBAA tag makes a
        # claim about *all* accesses to that memory, and the runtime is linked
        # into this module and reads these same frames -- `turkey_collect`
        # walks `frame->values`, `capture_panic_trace` reads `frame->site` --
        # with the tags clang gave it, from a different tree. Accesses from
        # unrelated TBAA roots are taken not to alias, so the collector's read
        # of a root and this module's store of it would be free to be
        # reordered against each other. A scope cuts the edge only between
        # instructions annotated on both sides, so an unannotated access, and
        # every access in the linked-in runtime is one, stays conservative.
        domain = self.module.add_metadata([ir.MetaDataString(self.module, "turkey")])
        scope = self.module.add_metadata(
            [ir.MetaDataString(self.module, "turkey.frames"), domain])
        self.frame_scope = self.module.add_metadata([scope])
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
        self._literal_frame: ir.Value | None = None
        self._current_frame: bir.Frame | None = None
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
        root_index, root_count, safepoint_live = _root_slots(source)
        in_entry = source.name == self.source.entry
        root_frame = entry_builder.alloca(_ROOT_FRAME, name="root_frame")
        panic_frame_storage = entry_builder.alloca(_PANIC_FRAME, name="panic_frame")
        root_array_type = ir.ArrayType(_PTR, max(1, root_count))
        root_values = entry_builder.alloca(root_array_type, name="root_values")
        # Only the fallback needs this. A slot the bitmap does not name is
        # never read, so it does not have to hold anything -- which is what
        # takes the `movi.2d` and its stores off the front of every call.
        if root_count > _MAPPED_ROOTS:
            for index in range(root_count):
                pointer = entry_builder.gep(
                    root_values, [ir.Constant(_I32, 0), ir.Constant(_I32, index)])
                self._frame_store(entry_builder, ir.Constant(_PTR, None), pointer)

        self._root_frame = root_frame
        self._root_values = root_values
        self._root_index = root_index
        self._root_count = root_count
        self._panic_frame_storage = panic_frame_storage
        self._in_entry = in_entry
        self._slot_layouts = {slot.name: slot.layout for slot in source.slots}

        # Where each frame has to be registered. A collection reads the root
        # frame at a safepoint and nowhere else. A panic frame is read when a
        # panic is raised, which is at any operation carrying a source position
        # and at any call, since the callee may panic under it.
        root_present = {
            block.name: any((block.name, index) in safepoint_live
                            for index in range(len(block.instructions)))
            for block in source.blocks}
        panic_present = {
            block.name: any(_is_safepoint(instruction.op)
                            for instruction in block.instructions)
                        or isinstance(block.terminator, bir.Panic)
            for block in source.blocks}
        if in_entry:
            # The entry function registers the permanently live frames before
            # anything else and leaves them last, so it is inside both regions
            # for its whole body whatever its own instructions do.
            root_present[source.entry] = True
        root_region = ({block.name: False for block in source.blocks}
                       if not root_count else _frame_region(source, root_present))
        panic_region = _frame_region(source, panic_present)
        self._root_region = root_region
        self._panic_region = panic_region

        if in_entry:
            self._enter_permanent_roots(entry_builder)
        if root_region[source.entry]:
            self._root_enter(entry_builder, source.name)
        if panic_region[source.entry]:
            self._frame_enter(entry_builder, source.name)

        phis: dict[tuple[str, str], ir.PhiInstr] = {}
        builders: dict[str, ir.IRBuilder] = {}
        for block in source.blocks:
            builder = entry_builder if block.name == source.entry else ir.IRBuilder(blocks[block.name])
            builders[block.name] = builder
            for param in block.params:
                phi = builder.phi(_llvm_type(param.layout), name=param.name)
                phis[(block.name, param.name)] = phi
                values[param.name] = phi

        for block in source.blocks:
            builder = builders[block.name]
            self._block_here = block.name
            self._root_here = root_region[block.name]
            self._panic_here = panic_region[block.name]
            for index, instruction in enumerate(block.instructions):
                live = safepoint_live.get((block.name, index))
                if live is not None:
                    self._publish_roots(builder, live, values, slots)
                self._current_frame = instruction.frame
                # Only before a call. The callee raises the panic, so the site
                # has to be in place before control leaves; an inline check
                # raises it itself, and sets the site in the block where it
                # fails. `+` is checked, so leaving this unconditional put a
                # store in the body of every arithmetic loop in the program --
                # and, since it is a store into an alloca whose address has
                # escaped, one that the loads around it had to be ordered
                # against.
                if (instruction.frame is not None and self._panic_here
                        and _is_safepoint(instruction.op)):
                    self._frame_update(builder, instruction.frame)
                value, builder = self._instruction(
                    function, builder, instruction, values, slots)
                if instruction.result is not None:
                    values[instruction.result.name] = value
            term = block.terminator
            assert term is not None
            if isinstance(term, bir.Return):
                self._leave_frames(builder, block.name)
                builder.ret(self._operand(term.value, values))
            elif isinstance(term, bir.Jump):
                target = next(b for b in source.blocks if b.name == term.target)
                arguments = [self._operand(arg, values) for arg in term.args]
                incoming = self._edge(function, builder, block.name, term.target,
                                      blocks)
                for param, argument in zip(target.params, arguments):
                    phis[(target.name, param.name)].add_incoming(
                        argument, incoming)
            elif isinstance(term, bir.Branch):
                condition = self._operand(term.condition, values)
                yes = self._edge_block(function, block.name, term.yes, blocks)
                no = self._edge_block(function, block.name, term.no, blocks)
                builder.cbranch(condition, yes, no)
            else:
                if term.frame is not None and self._panic_here:
                    self._frame_update(builder, term.frame)
                if isinstance(term.message, str):
                    self._panic(builder, term.message)
                else:
                    builder.call(self.runtime["turkey_panic_string"], [
                        self._operand(term.message, values)])
                self._leave_frames(builder, block.name)
                builder.ret(ir.Constant(function.function_type.return_type, None))

    def _root_enter(self, builder: ir.IRBuilder, name: str) -> None:
        builder.call(self.runtime["turkey_root_enter"], [
            builder.bitcast(self._root_frame, _PTR),
            builder.bitcast(self._root_values, _PTR),
            ir.Constant(_I64, self._root_count),
            self._c_string(builder, name, ".turkey.function"),
        ])
        if self._root_count > _MAPPED_ROOTS:
            self._set_live(builder, -1)

    def _root_leave(self, builder: ir.IRBuilder) -> None:
        builder.call(self.runtime["turkey_root_leave"],
                     [builder.bitcast(self._root_frame, _PTR)])

    def _frame_enter(self, builder: ir.IRBuilder, name: str) -> None:
        # Entered at the function's own name with no position: line 0 is what
        # `capture_panic_trace` reads as "this frame has not reached a site
        # yet", and such a frame is left out of the trace.
        builder.call(self.runtime["turkey_frame_enter"], [
            builder.bitcast(self._panic_frame_storage, _PTR),
            builder.bitcast(self._panic_site(bir.Frame(name, None, 0, 0)), _PTR),
        ])

    def _frame_leave(self, builder: ir.IRBuilder) -> None:
        builder.call(self.runtime["turkey_frame_leave"],
                     [builder.bitcast(self._panic_frame_storage, _PTR)])

    def _frame_store(self, builder: ir.IRBuilder, value: ir.Value,
                     pointer: ir.Value) -> ir.Instruction:
        """Store into a root or panic frame: stack storage no Turkey value can
        name, since the only pointers into it are the ones this module hands
        the runtime."""
        made = builder.store(value, pointer)
        made.set_metadata("alias.scope", self.frame_scope)
        return made

    def _heap_load(self, builder: ir.IRBuilder, pointer: ir.Value,
                   name: str = "") -> ir.Value:
        made = builder.load(pointer, name=name)
        made.set_metadata("noalias", self.frame_scope)
        return made

    def _heap_store(self, builder: ir.IRBuilder, value: ir.Value,
                    pointer: ir.Value) -> ir.Instruction:
        made = builder.store(value, pointer)
        made.set_metadata("noalias", self.frame_scope)
        return made

    def _set_live(self, builder: ir.IRBuilder, mask: int) -> None:
        pointer = builder.gep(self._root_frame, [
            ir.Constant(_I32, 0), ir.Constant(_I32, _ROOT_FRAME_LIVE)])
        self._frame_store(builder, ir.Constant(_I64, mask), pointer)

    def _publish_roots(self, builder: ir.IRBuilder, live: frozenset[str],
                       values: dict[str, ir.Value],
                       slots: dict[str, ir.Value]) -> None:
        """Make the collector's view of this frame exact, for one safepoint.

        The stores are here rather than at each value's definition because a
        value is very often defined on the hot path and live only into a cold
        one -- which is the whole of `Main#inc` -- and a store at the definition
        cannot be sunk afterwards, since the frame's address has escaped into
        the runtime and no LLVM pass may touch it.
        """
        mask = 0
        for name in sorted(live):
            index = self._root_index[name]
            if index < _MAPPED_ROOTS:
                mask |= 1 << index
            value = (builder.load(slots[name]) if name in slots
                     else values[name])
            self._store_root(builder, index, value)
        if self._root_count <= _MAPPED_ROOTS:
            self._set_live(builder, mask)

    def _store_root(self, builder: ir.IRBuilder, index: int,
                    value: ir.Value) -> None:
        pointer = builder.gep(self._root_values, [
            ir.Constant(_I32, 0), ir.Constant(_I32, index)])
        self._frame_store(builder, builder.bitcast(value, _PTR), pointer)

    def _leave_frames(self, builder: ir.IRBuilder, block: str) -> None:
        """Pop what this block holds, innermost first, on the way out."""
        if self._panic_region[block]:
            self._frame_leave(builder)
        if self._root_region[block]:
            self._root_leave(builder)
        self._leave_globals(builder)

    def _edge_block(self, function: ir.Function, source: str, target: str,
                    blocks: dict[str, ir.BasicBlock]) -> ir.BasicBlock:
        """The block to branch to, with any frame transition on the way.

        A transition sits on the edge rather than in either end because a
        target can be reached both from inside a region and from outside it --
        one arm of a branch registering the frame says nothing about the other.
        """
        entering_root = self._root_region[target] and not self._root_region[source]
        leaving_root = self._root_region[source] and not self._root_region[target]
        entering_panic = self._panic_region[target] and not self._panic_region[source]
        leaving_panic = self._panic_region[source] and not self._panic_region[target]
        if not (entering_root or leaving_root or entering_panic or leaving_panic):
            return blocks[target]
        landing = function.append_basic_block(f"{source}.to.{target}.frames")
        builder = ir.IRBuilder(landing)
        if leaving_panic:
            self._frame_leave(builder)
        if leaving_root:
            self._root_leave(builder)
        if entering_root:
            self._root_enter(builder, function.name)
        if entering_panic:
            self._frame_enter(builder, function.name)
        builder.branch(blocks[target])
        return landing

    def _edge(self, function: ir.Function, builder: ir.IRBuilder, source: str,
              target: str, blocks: dict[str, ir.BasicBlock]) -> ir.BasicBlock:
        """Branch to `target`, and report the block the phis come in from.

        Which is the landing block when there is one, and otherwise whatever
        block the builder ended up in -- an instruction that emits a check
        splits one bir block into several LLVM ones, so it is not necessarily
        the block this one started in.
        """
        destination = self._edge_block(function, source, target, blocks)
        incoming = builder.block
        builder.branch(destination)
        return incoming if destination is blocks[target] else destination

    def _enter_permanent_roots(self, builder: ir.IRBuilder) -> None:
        """Register what stays live for the whole run, below everything else.

        Registered from the entry function, which does not return until the
        program is over, so these sit below every other frame for the whole
        run. They are *left*, rather than being permanent registrations,
        because the runtime outlives any one compiled module: a frame never
        popped would still be on the runtime's list pointing into a module the
        engine has freed.
        """
        if self.global_array is not None:
            builder.call(self.runtime["turkey_root_enter"], [
                builder.bitcast(self.global_frame, _PTR),
                builder.bitcast(self.global_array, _PTR),
                ir.Constant(_I64, len(self.global_roots)),
                self._c_string(builder, "<globals>", ".turkey.function"),
            ])
            pointer = builder.gep(self.global_frame, [
                ir.Constant(_I32, 0), ir.Constant(_I32, _ROOT_FRAME_LIVE)])
            builder.store(ir.Constant(_I64, -1), pointer)
        literals = list(self.string_literals.values())
        nullaries = list(self.nullary_objects.items())
        if not literals and not nullaries:
            return
        # One frame of its own rather than slots in the function's, because
        # these are live at every safepoint in the program and would otherwise
        # have to appear in every bitmap the entry function emits.
        frame = builder.alloca(_ROOT_FRAME, name="literal_frame")
        array_type = ir.ArrayType(_PTR, len(literals) + len(nullaries))
        array = builder.alloca(array_type, name="literal_values")
        for index in range(len(literals) + len(nullaries)):
            builder.store(ir.Constant(_PTR, None), builder.gep(
                array, [ir.Constant(_I32, 0), ir.Constant(_I32, index)]))
        builder.call(self.runtime["turkey_root_enter"], [
            builder.bitcast(frame, _PTR), builder.bitcast(array, _PTR),
            ir.Constant(_I64, len(literals) + len(nullaries)),
            self._c_string(builder, "<literals>", ".turkey.function"),
        ])
        builder.store(ir.Constant(_I64, -1), builder.gep(
            frame, [ir.Constant(_I32, 0), ir.Constant(_I32, _ROOT_FRAME_LIVE)]))
        self._literal_frame = frame
        for offset, (bytes_global, value_global, length) in enumerate(literals):
            literal = builder.call(self.runtime["turkey_string_new"], [
                builder.bitcast(bytes_global, _PTR), ir.Constant(_I64, length)])
            builder.store(literal, value_global)
            builder.store(literal, builder.gep(
                array, [ir.Constant(_I32, 0), ir.Constant(_I32, offset)]))
        for offset, ((kind, tag), value_global) in enumerate(nullaries):
            made = builder.call(self.runtime["turkey_object_new"], [
                ir.Constant(_I32, kind), ir.Constant(_I32, tag),
                ir.Constant(_I64, 0), ir.Constant(_I64, 0)])
            builder.store(made, value_global)
            builder.store(made, builder.gep(array, [
                ir.Constant(_I32, 0),
                ir.Constant(_I32, len(literals) + offset)]))

    def _leave_globals(self, builder: ir.IRBuilder) -> None:
        """Pop the permanent frames, on every path out of the entry function.

        Innermost first: the literals were registered after the globals.
        """
        if not self._in_entry:
            return
        if self._literal_frame is not None:
            builder.call(self.runtime["turkey_root_leave"],
                         [builder.bitcast(self._literal_frame, _PTR)])
        if self.global_array is not None:
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
        loaded = self._heap_load(builder, address)
        # A narrow element is already its own type; a wide one is a raw 64-bit
        # pattern, the same as an object slot.
        return (self._from_i64(builder, loaded, layout) if width == 8
                else loaded)

    def _array_store(self, builder: ir.IRBuilder, pointer: ir.Value,
                     index: ir.Value, value: ir.Value,
                     layout: bir.Layout) -> None:
        address, width = self._array_element(builder, pointer, index, layout)
        self._heap_store(builder, self._to_i64(builder, value) if width == 8
                         else value, address)

    def _frame_update(self, builder: ir.IRBuilder, frame: bir.Frame) -> None:
        pointer = builder.gep(self._panic_frame_storage, [
            ir.Constant(_I32, 0), ir.Constant(_I32, 1)])
        self._frame_store(builder, builder.bitcast(self._panic_site(frame), _PTR),
                          pointer)

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
            if instruction.diverges:
                return value, self._diverged(function, builder)
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
            bits = self._heap_load(builder, self._cell_value(builder, args[0]))
            return self._from_i64(builder, bits, instruction.result.layout), builder
        if op == "cell_store":
            self._heap_store(builder, self._to_i64(builder, args[1]),
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
            return self._heap_load(
                builder, self._object_field(builder, args[0], _OBJECT_TAG),
                name=instruction.result.name), builder
        if op == "object_get":
            bits = self._heap_load(
                builder,
                self._object_slot(builder, args[0], int(instruction.args[1])))
            value = self._from_i64(builder, bits, instruction.result.layout)
            return value, builder
        if op == "object_set":
            self._heap_store(
                builder, self._to_i64(builder, args[1]),
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
            code = self._heap_load(builder, self._object_slot(builder, closure, 0))
            environment = builder.inttoptr(
                self._heap_load(builder, self._object_slot(builder, closure, 1)),
                _PTR)
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
            return self._heap_load(
                builder,
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
               failed: ir.Value, message: str | None,
               site: bool = True) -> ir.IRBuilder:
        bad = function.append_basic_block("panic")
        good = function.append_basic_block("checked")
        builder.cbranch(failed, bad, good)
        panic_builder = ir.IRBuilder(bad)
        # An inline check needs this function's panic frame only where it
        # fails. `+` is checked, so every arithmetic loop in the program has a
        # guard in it, and registering for that on the way *in* put the frame
        # back on exactly the hot paths this is trying to clear -- for a branch
        # that, when it is taken, ends the program. So a block that is not
        # already in the panic region registers here instead, where the failure
        # is, and `capture_panic_trace` still sees the frame it needs.
        assert self._panic_here or self._current_frame is not None, (
            "a check with no source position cannot name itself in a trace")
        registered = self._panic_here
        if not registered and self._current_frame is not None:
            self._frame_enter(panic_builder, self._current_frame.function)
            registered = True
        if site and self._current_frame is not None:
            # Where the check failed. `_propagate` passes `site=False`: its
            # panic was raised by the callee, which captured the trace before
            # returning, so there is nothing left for a site to say.
            self._frame_update(panic_builder, self._current_frame)
        if message is not None:
            self._panic(panic_builder, message)
        # A guard's panic block is a way out of the enclosing bir block, so it
        # pops exactly what that block holds.
        if registered:
            panic_builder.call(self.runtime["turkey_frame_leave"], [
                panic_builder.bitcast(self._panic_frame_storage, _PTR)])
        if self._root_here:
            panic_builder.call(self.runtime["turkey_root_leave"], [
                panic_builder.bitcast(self._root_frame, _PTR)])
        self._leave_globals(panic_builder)
        panic_builder.ret(ir.Constant(function.function_type.return_type, None))
        return ir.IRBuilder(good)

    def _diverged(self, function: ir.Function,
                  builder: ir.IRBuilder) -> ir.IRBuilder:
        """Leave, after a call that has already panicked.

        The ordinary sequence is `_propagate`: test the flag, return if it is
        set, carry on if it is not. For a callee that always panics the second
        arm cannot be taken, and saying so is worth much more than the branch
        it removes. The arm that carries on merges back into the code after
        the call -- after a bounds check, that is the array access itself --
        and because the call may write anything, LLVM has to redo every load
        across the merge. `Main#inc` reloaded the array header and its length
        four times and checked the same index against the same length twice,
        entirely because the failure path was still, formally, able to come
        back.

        The rest of the block is emitted into a block nothing branches to, so
        the optimizer drops it along with the phi entries naming it.
        """
        self._leave_frames(builder, self._block_here)
        builder.ret(ir.Constant(function.function_type.return_type, None))
        return ir.IRBuilder(function.append_basic_block("after.panic"))

    def _propagate(self, function: ir.Function,
                   builder: ir.IRBuilder) -> ir.IRBuilder:
        panicked = builder.load(self.panic_flag, name="panicked")
        return self._guard(
            function, builder,
            builder.icmp_unsigned("!=", panicked, ir.Constant(_I32, 0)), None,
            site=False,
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
