"""Emit, verify, and execute llvmlite IR from the checked backend CFG."""

from __future__ import annotations

import ctypes
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
from .errors import TurkeyPanic, Unsupported


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
        self.runtime: dict[str, ir.Function] = {}
        self.string_count = 0
        self._declare_runtime()

    def _runtime(self, name: str, ret: ir.Type, args: list[ir.Type]) -> None:
        self.runtime[name] = ir.Function(self.module, ir.FunctionType(ret, args), name=name)

    def _declare_runtime(self) -> None:
        self._runtime("turkey_string_new", _PTR, [_PTR, _I64])
        self._runtime("turkey_string_concat", _PTR, [_PTR, _PTR])
        self._runtime("turkey_int_to_string", _PTR, [_I64])
        self._runtime("turkey_float_to_string", _PTR, [_F64])
        self._runtime("turkey_char_to_string", _PTR, [_I32])
        self._runtime("turkey_print", _I8, [_PTR])
        self._runtime("turkey_write", _I8, [_PTR])
        self._runtime("turkey_cell_new", _PTR, [_I64])
        self._runtime("turkey_cell_load", _I64, [_PTR])
        self._runtime("turkey_cell_store", ir.VoidType(), [_PTR, _I64])
        self._runtime("turkey_panic", ir.VoidType(), [_PTR])
        self._runtime("turkey_panicked", _I32, [])

    def emit(self) -> tuple[str, binding.TargetMachine]:
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
            for instruction in block.instructions:
                value, builder = self._instruction(
                    function, builder, instruction, values, slots)
                if instruction.result is not None:
                    values[instruction.result.name] = value
            term = block.terminator
            assert term is not None
            if isinstance(term, bir.Return):
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
                self._panic(builder, term.message)
                builder.ret(ir.Constant(function.function_type.return_type, None))

    def _operand(self, operand: bir.Operand, values: dict[str, ir.Value]) -> ir.Value:
        if isinstance(operand, bir.Value):
            return values[operand.name]
        ty = _llvm_type(operand.layout)
        if operand.layout is bir.Layout.F64:
            return ir.Constant(ty, float(operand.value))
        return ir.Constant(ty, int(operand.value))

    def _instruction(self, function: ir.Function, builder: ir.IRBuilder,
                     instruction: bir.Instruction, values: dict[str, ir.Value],
                     slots: dict[str, ir.AllocaInstr]) -> tuple[ir.Value | None, ir.IRBuilder]:
        op = instruction.op
        if op == "slot_store":
            builder.store(self._operand(instruction.args[1], values), slots[instruction.args[0]])
            return None, builder
        if op == "slot_load":
            return builder.load(slots[instruction.args[0]], name=instruction.result.name), builder
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
        args = [self._operand(arg, values) for arg in instruction.args
                if isinstance(arg, (bir.Value, bir.Constant))]
        if op == "call":
            return builder.call(self.functions[instruction.args[0]], args,
                                name=instruction.result.name), builder
        if op.startswith("prim."):
            return self._primitive(function, builder, op[5:], args, instruction.result.layout)
        if op == "cell_new":
            bits = self._to_i64(builder, args[0])
            return builder.call(self.runtime["turkey_cell_new"], [bits]), builder
        if op == "cell_load":
            bits = builder.call(self.runtime["turkey_cell_load"], args)
            return self._from_i64(builder, bits, instruction.result.layout), builder
        if op == "cell_store":
            builder.call(self.runtime["turkey_cell_store"],
                         [args[0], self._to_i64(builder, args[1])])
            return None, builder
        raise Unsupported(f"no LLVM emission rule for {op}")

    def _primitive(self, function: ir.Function, builder: ir.IRBuilder, name: str,
                   args: list[ir.Value], layout: bir.Layout) -> tuple[ir.Value, ir.IRBuilder]:
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
        runtime = {
            "intToString": "turkey_int_to_string", "floatToString": "turkey_float_to_string",
            "charToString": "turkey_char_to_string", "stringConcat": "turkey_string_concat",
            "print": "turkey_print", "write": "turkey_write",
        }.get(name)
        if runtime:
            return builder.call(self.runtime[runtime], args), builder
        if name == "error":
            # Runtime strings are length-delimited, while panic messages are C
            # strings. A dedicated runtime entry is added with full panic ABI;
            # for now preserve a stable diagnostic instead of reading past it.
            self._panic(builder, "error")
            return ir.Constant(_llvm_type(layout), None), builder
        raise Unsupported(f"LLVM primitive Prim.{name} is not implemented")

    def _guard(self, function: ir.Function, builder: ir.IRBuilder,
               failed: ir.Value, message: str) -> ir.IRBuilder:
        bad = function.append_basic_block("panic")
        good = function.append_basic_block("checked")
        builder.cbranch(failed, bad, good)
        panic_builder = ir.IRBuilder(bad)
        self._panic(panic_builder, message)
        panic_builder.ret(ir.Constant(function.function_type.return_type, None))
        return ir.IRBuilder(good)

    def _panic(self, builder: ir.IRBuilder, message: str) -> None:
        data = message.encode("utf-8") + b"\0"
        array = ir.Constant(ir.ArrayType(_I8, len(data)), bytearray(data))
        glob = ir.GlobalVariable(self.module, array.type,
                                 name=f".turkey.panic.{self.string_count}")
        self.string_count += 1
        glob.global_constant = True
        glob.linkage = "private"
        glob.initializer = array
        builder.call(self.runtime["turkey_panic"], [builder.bitcast(glob, _PTR)])

    def _to_i64(self, builder: ir.IRBuilder, value: ir.Value) -> ir.Value:
        if value.type == _F64: return builder.bitcast(value, _I64)
        if isinstance(value.type, ir.PointerType): return builder.ptrtoint(value, _I64)
        if value.type.width < 64: return builder.zext(value, _I64)
        return value

    def _from_i64(self, builder: ir.IRBuilder, value: ir.Value,
                  layout: bir.Layout) -> ir.Value:
        target = _llvm_type(layout)
        if target == _F64: return builder.bitcast(value, _F64)
        if isinstance(target, ir.PointerType): return builder.inttoptr(value, target)
        if target.width < 64: return builder.trunc(value, target)
        return value


_RUNTIME_SYMBOLS = (
    "turkey_string_new", "turkey_string_concat", "turkey_int_to_string",
    "turkey_float_to_string", "turkey_char_to_string", "turkey_print",
    "turkey_write", "turkey_cell_new", "turkey_cell_load", "turkey_cell_store",
    "turkey_panic", "turkey_panicked",
)
_runtime_library: ctypes.CDLL | None = None


def _load_runtime() -> ctypes.CDLL:
    global _runtime_library
    if _runtime_library is not None:
        return _runtime_library
    root = Path(__file__).resolve().parent.parent
    source = root / "runtime" / "turkey_runtime.c"
    output = Path(tempfile.mkdtemp(prefix="turkey-runtime-")) / (
        "libturkey.dylib" if sys.platform == "darwin" else "libturkey.so")
    command = ["cc", "-std=c11", "-O2", "-fPIC", str(source), "-o", str(output)]
    command.insert(1, "-dynamiclib" if sys.platform == "darwin" else "-shared")
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
        address = self.engine.get_function_address(self.entry)
        ctypes.CFUNCTYPE(_ctype(self.result))(address)()
        if self.runtime.turkey_panicked():
            raw = self.runtime.turkey_panic_message()
            raise TurkeyPanic(raw.decode("utf-8", "replace"))


def _ctype(layout: bir.Layout):
    return {
        bir.Layout.UNIT: ctypes.c_uint8, bir.Layout.I1: ctypes.c_bool,
        bir.Layout.I8: ctypes.c_uint8, bir.Layout.I32: ctypes.c_uint32,
        bir.Layout.I64: ctypes.c_int64, bir.Layout.F64: ctypes.c_double,
        bir.Layout.PTR: ctypes.c_void_p, bir.Layout.BOXED: ctypes.c_void_p,
    }[layout]


def generate(program: CProgram, decls: DeclTable, main: str = "main") -> str:
    source = lower(program, main)
    return _Emitter(source).emit()[0]


def compile(program: CProgram, decls: DeclTable, main: str = "main") -> NativeModule:
    source = lower(program, main)
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
