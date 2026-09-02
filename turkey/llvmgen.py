"""Emit, verify, and execute llvmlite IR from the checked backend CFG."""

from __future__ import annotations

import ctypes
import os
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
        self.functions: dict[str, ir.Function] = {}
        self.source_functions = {function.name: function
                                 for function in source.functions}
        self.globals: dict[str, ir.GlobalVariable] = {}
        self.runtime: dict[str, ir.Function] = {}
        self.closure_thunks: dict[str, ir.Function] = {}
        self.string_count = 0
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
        self._runtime("turkey_frame_enter", _PTR, [_PTR, _PTR, _I64, _I64])
        self._runtime("turkey_frame_leave", ir.VoidType(), [_PTR])
        self._runtime("turkey_root_push", _PTR, [_I64, _PTR])
        self._runtime("turkey_root_set", ir.VoidType(), [_PTR, _I64, _PTR])
        self._runtime("turkey_root_pop", ir.VoidType(), [_PTR])

    def emit(self) -> tuple[str, binding.TargetMachine]:
        for source in self.source.globals:
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
        root_values = [value for value in source.params + source.slots
                       if value.layout in (bir.Layout.PTR, bir.Layout.BOXED)]
        root_values += [value for block in source.blocks for value in block.params
                        if value.layout in (bir.Layout.PTR, bir.Layout.BOXED)]
        root_values += [instruction.result for block in source.blocks
                        for instruction in block.instructions
                        if instruction.result is not None and instruction.result.layout
                        in (bir.Layout.PTR, bir.Layout.BOXED)]
        root_index = {value.name: index for index, value in enumerate(root_values)}
        root_frame = entry_builder.call(
            self.runtime["turkey_root_push"],
            [ir.Constant(_I64, len(root_values)),
             self._c_string(entry_builder, source.name, ".turkey.function")],
            name="roots")
        self._root_frame = root_frame
        self._root_index = root_index
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
                panic_frame = (self._frame_enter(builder, instruction.frame)
                               if instruction.frame is not None else None)
                value, builder = self._instruction(
                    function, builder, instruction, values, slots)
                if panic_frame is not None:
                    builder.call(self.runtime["turkey_frame_leave"], [panic_frame])
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
                builder.call(self.runtime["turkey_root_pop"], [root_frame])
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
                    self._frame_enter(builder, term.frame)
                if isinstance(term.message, str):
                    self._panic(builder, term.message)
                else:
                    builder.call(self.runtime["turkey_panic_string"], [
                        self._operand(term.message, values)])
                builder.call(self.runtime["turkey_root_pop"], [root_frame])
                builder.ret(ir.Constant(function.function_type.return_type, None))

    def _set_root(self, builder: ir.IRBuilder, name: str, value: ir.Value) -> None:
        builder.call(self.runtime["turkey_root_set"], [
            self._root_frame, ir.Constant(_I64, self._root_index[name]), value,
        ])

    def _frame_enter(self, builder: ir.IRBuilder, frame: bir.Frame) -> ir.Value:
        file = (ir.Constant(_PTR, None) if frame.file is None else
                self._c_string(builder, frame.file, ".turkey.file"))
        return builder.call(self.runtime["turkey_frame_enter"], [
            self._c_string(builder, frame.function, ".turkey.frame"), file,
            ir.Constant(_I64, frame.line), ir.Constant(_I64, frame.col),
        ])

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
                          self.globals[instruction.args[0]])
            return None, builder
        if op == "global_load":
            return builder.load(self.globals[instruction.args[0]],
                                name=instruction.result.name), builder
        if op == "string_const":
            data = instruction.args[0].encode("utf-8")
            array = ir.Constant(ir.ArrayType(_I8, len(data)), bytearray(data))
            glob = ir.GlobalVariable(self.module, array.type,
                                     name=f".turkey.string.{self.string_count}")
            self.string_count += 1
            glob.global_constant = True
            glob.linkage = "private"
            glob.initializer = array
            pointer = builder.bitcast(glob, _PTR)
            return builder.call(self.runtime["turkey_string_new"],
                                [pointer, ir.Constant(_I64, len(data))]), builder
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
            bits = builder.call(self.runtime["turkey_cell_load"], args)
            return self._from_i64(builder, bits, instruction.result.layout), builder
        if op == "cell_store":
            builder.call(self.runtime["turkey_cell_store"],
                         [args[0], self._to_i64(builder, args[1])])
            return None, builder
        if op == "object_new":
            raw = [ir.Constant(_I32, int(instruction.args[0])),
                   ir.Constant(_I32, int(instruction.args[1])),
                   ir.Constant(_I64, int(instruction.args[2])),
                   ir.Constant(_I64, int(instruction.args[3]))]
            return builder.call(self.runtime["turkey_object_new"], raw), builder
        if op == "object_tag":
            return builder.call(self.runtime["turkey_object_tag"], args), builder
        if op == "object_get":
            bits = builder.call(self.runtime["turkey_object_get_as"], [
                args[0], ir.Constant(_I64, int(instruction.args[1])),
                ir.Constant(_I32, _layout_code(instruction.result.layout)),
            ])
            value = self._from_i64(builder, bits, instruction.result.layout)
            return value, self._propagate(function, builder)
        if op == "object_set":
            layout = instruction.args[-1].layout
            builder.call(self.runtime["turkey_object_set_as"], [
                args[0], ir.Constant(_I64, int(instruction.args[1])),
                self._to_i64(builder, args[1]),
                ir.Constant(_I32, _layout_code(layout)),
            ])
            return None, self._propagate(function, builder)
        if op == "array_new":
            return builder.call(self.runtime["turkey_array_new"],
                                [ir.Constant(_I64, int(instruction.args[0])),
                                 ir.Constant(_I64, 0),
                                 ir.Constant(_I32, int(instruction.args[1])),
                                 ir.Constant(_I32, _layout_code(
                                     bir.Layout(instruction.args[2])))]), builder
        if op == "array_get":
            bits = builder.call(self.runtime["turkey_array_get_as"], [
                *args, ir.Constant(_I32, _layout_code(instruction.result.layout))])
            value = self._from_i64(builder, bits, instruction.result.layout)
            return value, self._propagate(function, builder)
        if op == "array_set":
            index = (ir.Constant(_I64, int(instruction.args[1]))
                     if isinstance(instruction.args[1], str) else args[1])
            value = args[1] if isinstance(instruction.args[1], str) else args[2]
            layout = instruction.args[-1].layout
            builder.call(self.runtime["turkey_array_set_as"], [
                args[0], index, self._to_i64(builder, value),
                ir.Constant(_I32, _layout_code(layout)),
            ])
            return None, self._propagate(function, builder)
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
            code = builder.call(self.runtime["turkey_closure_code"], [closure])
            environment = builder.call(
                self.runtime["turkey_closure_environment"], [closure])
            builder = self._propagate(function, builder)
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
            return builder.call(self.runtime["turkey_array_length"], args), builder
        if name == "arrayGet":
            requested = array_layout or layout
            bits = builder.call(self.runtime["turkey_array_get_as"], [
                *args, ir.Constant(_I32, _layout_code(requested))])
            value = self._from_i64(builder, bits, layout)
            return value, self._propagate(function, builder)
        if name == "arraySet":
            requested = array_layout or bir.Layout.BOXED
            builder.call(self.runtime["turkey_array_set_as"], [
                args[0], args[1], self._to_i64(builder, args[2]),
                ir.Constant(_I32, _layout_code(requested)),
            ])
            return ir.Constant(_I8, 0), self._propagate(function, builder)
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
        panic_builder.call(self.runtime["turkey_root_pop"], [self._root_frame])
        panic_builder.ret(ir.Constant(function.function_type.return_type, None))
        return ir.IRBuilder(good)

    def _propagate(self, function: ir.Function,
                   builder: ir.IRBuilder) -> ir.IRBuilder:
        panicked = builder.call(self.runtime["turkey_panicked"], [])
        return self._guard(
            function, builder,
            builder.icmp_unsigned("!=", panicked, ir.Constant(_I32, 0)), None,
        )

    def _panic(self, builder: ir.IRBuilder, message: str) -> None:
        builder.call(self.runtime["turkey_panic"], [
            self._c_string(builder, message, ".turkey.panic")])

    def _c_string(self, builder: ir.IRBuilder, value: str,
                  prefix: str) -> ir.Value:
        data = value.encode("utf-8") + b"\0"
        array = ir.Constant(ir.ArrayType(_I8, len(data)), bytearray(data))
        glob = ir.GlobalVariable(self.module, array.type,
                                 name=f"{prefix}.{self.string_count}")
        self.string_count += 1
        glob.global_constant = True
        glob.linkage = "private"
        glob.initializer = array
        return builder.bitcast(glob, _PTR)

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
    "turkey_root_push", "turkey_root_set", "turkey_root_pop",
)
_runtime_library: ctypes.CDLL | None = None


def _load_runtime() -> ctypes.CDLL:
    global _runtime_library
    if _runtime_library is not None:
        return _runtime_library
    root = Path(__file__).resolve().parent.parent
    source = root / "runtime" / "turkey_runtime.c"
    if not source.is_file():
        source = Path(sys.prefix) / "runtime" / "turkey_runtime.c"
    if not source.is_file():
        raise Unsupported("the packaged Turkey native runtime source is missing")
    output = Path(tempfile.mkdtemp(prefix="turkey-runtime-")) / (
        "libturkey.dylib" if sys.platform == "darwin" else "libturkey.so")
    command = ["cc", "-std=c11", "-O2", "-fPIC", str(source), "-o", str(output)]
    command.insert(1, "-dynamiclib" if sys.platform == "darwin" else "-shared")
    if sys.platform != "darwin":
        command.append("-lm")
    if "TURKEY_RUNTIME_SANITIZE" in os.environ:
        command[2:2] = ["-O1", "-g", "-fsanitize=address,undefined",
                        "-fno-omit-frame-pointer"]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        raise Unsupported(f"could not build the Turkey runtime: {detail}") from exc
    library = ctypes.CDLL(str(output))
    for name in _RUNTIME_SYMBOLS:
        binding.add_symbol(name, ctypes.cast(getattr(library, name), ctypes.c_void_p).value)
    library.turkey_panicked.restype = ctypes.c_int32
    library.turkey_panic_message.restype = ctypes.c_char_p
    library.turkey_frame_count.restype = ctypes.c_int64
    library.turkey_frame_function.restype = ctypes.c_char_p
    library.turkey_frame_file.restype = ctypes.c_char_p
    library.turkey_frame_line.restype = ctypes.c_int64
    library.turkey_frame_col.restype = ctypes.c_int64
    library.turkey_heap_objects.restype = ctypes.c_int64
    library.turkey_panic_clear()
    _runtime_library = library
    return library


@dataclass
class NativeModule:
    engine: binding.ExecutionEngine
    module: binding.ModuleRef
    runtime: ctypes.CDLL
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
    runtime = _load_runtime()
    backing = binding.parse_assembly("")
    engine = binding.create_mcjit_compiler(backing, machine)
    module = binding.parse_assembly(text)
    module.verify()
    manager = binding.ModulePassManager()
    builder = binding.PassManagerBuilder()
    builder.opt_level = 2
    builder.populate(manager)
    manager.run(module)
    engine.add_module(module)
    engine.finalize_object()
    engine.run_static_constructors()
    entry = next(fn for fn in source.functions if fn.name == source.entry)
    return NativeModule(engine, module, runtime, source.entry, entry.result)


def execute(program: CProgram, decls: DeclTable, main: str = "main",
            filename: str = "<input>") -> None:
    compile(program, decls, main).execute()


__all__ = ["NativeModule", "compile", "execute", "generate"]
