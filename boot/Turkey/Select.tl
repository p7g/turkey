module Turkey.Select (
    Unsupported(..), selectFunc, selectModule,
)

-- The low IR to arm64 (`NATIVE-BACKEND.md`, M28 phase 4).
--
-- Built in slices against the corpus, the way the Core lowering was: an
-- opcode with no rule stops one function and is *reported*, so `boot asm`
-- prints how much of each program was selected and a histogram of what
-- stopped the rest. That report is the plan for the next slice, and it was
-- better at making one than reading the input was (FINDINGS 58).
--
-- **The output is a different instruction type, not a rewritten one.** That is
-- the parameterized CFG's whole purpose: after this, a virtual opcode is not
-- representable, so every later pass -- the allocator, the printer, the
-- encoder -- matches exhaustively over machine instructions only. QBE and Go
-- put both in one enum, and neither language checks a switch.
--
-- **Registers stay virtual.** A `Value` becomes `Virtual(id)`, and the
-- allocator replaces it later through a side table rather than by rewriting
-- what is emitted here.
--
-- What is *not* here, deliberately: the overflow and division checks, and the
-- panic test after a call. Those are emitted per target because each target
-- writes a different and better sequence -- arm64 reads a flag register the IR
-- cannot name -- and they are the job rather than the hazard. The GC roots are
-- not here either, but for the opposite reason: their *analysis* is shared,
-- in `Turkey.Roots`.

import Data.Map as Map
import Data.Set as Set
import Turkey.Arm64 as A
-- The machine constructors stay qualified and the IR's do not, which is
-- forced rather than chosen: `Add`, `Eq` and `Ret` name both a `BinOp` or a
-- `Term` and an `Arm`, patterns cannot be qualified, and this module matches
-- on the IR and constructs machine instructions. So the side being matched
-- gets the unqualified names.
import Turkey.Arm64 (Reg(..), Inst(..), Bank(..))
import Turkey.LowIr as L
import Turkey.LowIr (Op(..), Low(..), BinOp(..), CmpOp(..), Conv(..),
                     Callee(..))
import Turkey.Asm as Asm
import Turkey.Asm (Asm(..))
import Turkey.Runtime as Rt
import Turkey.Runtime (Entry(..))
import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..), Term(..),
                   Func(..), Block(..))
import Turkey.SsaLower as SsaLower
import Turkey.SsaLower (Module(..))

type Unsupported = Unsupported { name : String, what : String }

type Sel = Sel {
    source  : Func Low,
    out     : Func Inst,
    -- The block being emitted into. Not the block being *read* from: a guard
    -- splits a block, so selection may be several blocks ahead of its source.
    at      : Int,
    -- The first opcode with no rule, if any.
    stopped : Option String,
    -- The module's names, shared by every function in it.
    asm     : Asm,
}

fun emit(s : Sel, op : A.Arm) -> Unit {
    Array.push(s.out.blocks[s.at].insts, Inst { op = op })
}

fun stop(s : Sel, what : String) -> Unit {
    match s.stopped {
        None -> { s.stopped = Some(what) }
        Some(_) -> {}
    }
}

-- A fresh virtual register, for a value the low IR did not name.
--
-- Selection needs temporaries -- an address computed in two instructions, the
-- constant an immediate did not fit in -- and they share the numbering with
-- the low IR's values so that one `reps` table describes both.
fun temp(s : Sel, r : Rep) -> Reg = A.reg(tempValue(s, r))

-- The same, as a `Value`. A terminator names values rather than registers, so
-- a guard's condition and a block's parameters need this form.
fun tempValue(s : Sel, r : Rep) -> Value = Ssa.freshValue(s.out, r)

-- The register width a representation is held at.
--
-- `F64` is deliberately absent rather than mapped: a double lives in a `d`
-- register, which `Reg` has no way to say, so answering `W32` or `W64` for one
-- would put a double in an integer register and print it as `w` or `x`.
-- `floating` refuses the instruction before this is reached, which is why the
-- catch-all here is safe -- and why removing that guard is not a small change.
fun widthOf(r : Rep) -> A.Width = match r.class_ {
    I64 -> A.W64
    Ptr -> A.W64
    _ -> A.W32
}

-- -- selection ----------------------------------------------------------------------

fun selectModule(m : Module) -> (Array (Func Inst), Array Unsupported) {
    -- One `Asm` for the module, because a literal interned by one function is
    -- the same literal in the next and the root array is one array.
    let asm = Asm.newAsm(m)
    let done = []
    let skipped = []
    for f in m.functions {
        match selectFunc(f, asm) {
            Some(g) -> Array.push(done, g)
            None -> {}
        }
    }
    for f in m.functions {
        match reasonFor(f, asm) {
            Some(why) -> Array.push(skipped,
                                    Unsupported { name = f.name, what = why })
            None -> {}
        }
    }
    (done, skipped)
}

-- Why a function could not be selected, or `None` if it could.
--
-- Run separately from `selectFunc` rather than threaded through it, because a
-- driver wants the reason and the caller of `selectFunc` wants the function,
-- and returning both made every call site destructure a pair it half ignored.
fun reasonFor(f : Func Low, asm : Asm) -> Option String {
    let s = start(f, asm)
    run(s)
    s.stopped
}

fun selectFunc(f : Func Low, asm : Asm) -> Option (Func Inst) {
    let s = start(f, asm)
    run(s)
    match s.stopped {
        Some(_) -> None
        None -> Some(s.out)
    }
}

fun start(f : Func Low, asm : Asm) -> Sel {
    let g = Ssa.newFunc(f.name, f.result)
    -- The same value numbering, so a `Value` means the same thing on both
    -- sides and `reps` describes both. Selection only ever adds.
    for r in f.reps { Array.push(g.reps, r) }
    for p in f.params { Array.push(g.params, p) }
    for block in f.blocks {
        let made = Ssa.newBlock()
        for p in block.params { Array.push(made.params, p) }
        Array.push(g.blocks, made)
    }
    g.entry = f.entry
    Sel { source = f, out = g, at = 0, stopped = None, asm = asm }
}

-- Every source block, in order, into the block of the same index -- and then
-- into whatever block a guard left selection in.
--
-- The terminator is copied *after* the body rather than by `start`, and that
-- is the whole accommodation splitting needs. A guard ends the block it is in
-- and continues in a fresh one, so the block that carries block `b`'s
-- terminator is `s.at` and not `b`. Blocks are only ever *appended*, never
-- inserted, so every `BlockId` a terminator already names still means the
-- block it meant.
fun run(s : Sel) -> Unit {
    for var b = 0; b < len(s.source.blocks); b = b + 1 {
        s.at = b
        for i in s.source.blocks[b].insts { selectInst(s, i) }
        s.out.blocks[s.at].term = s.source.blocks[b].term
    }
}

-- A new block at the end, and its id.
fun block(s : Sel) -> Int {
    Array.push(s.out.blocks, Ssa.newBlock())
    len(s.out.blocks) - 1
}

-- -- terminators --------------------------------------------------------------------

-- Terminators and block parameters are *kept*, not lowered.
--
-- The first attempt put `b`, `b.cond` and `ret` in the instruction stream and
-- left `Term` at its default, and that quietly destroys the CFG:
-- `Ssa.successors` reads `Term`, so `reversePostorder`, `dominators` and
-- `liveness` would all have seen a function of disconnected blocks -- and the
-- register allocator is the next thing to ask them. The branch would have been
-- right and everything reasoning about it wrong.
--
-- So the graph stays a graph. Block parameters survive selection as virtual
-- registers, a jump's arguments stay its arguments, and both are resolved when
-- the encoder lays blocks out -- which is also where the parallel-move problem
-- belongs, since only then is it known which moves are real. Cranelift keeps
-- block parameters into `VCode` for the same reason.

-- -- instructions -------------------------------------------------------------------

fun selectInst(s : Sel, i : Low) -> Unit {
    let d = A.reg(i.result)
    let r = Ssa.repOf(s.source, i.result)
    let w = widthOf(r)
    match i.op {
        ConstInt(n) -> constant(s, d, n)
        ConstUnit -> emit(s, A.Mov(d, A.xzr, A.W64))
        Bin(o, a, b) ->
            if r.class_ == F64 { floatBin(s, d, o, A.reg(a), A.reg(b)) }
            else { match binOp(o) {
                Some(make) -> emit(s, make(d, A.reg(a), A.reg(b), w))
                None -> trapping(s, i, d, r, o, A.reg(a), A.reg(b), w)
            } }
        Cmp(o, a, b) -> {
            let operand = Ssa.repOf(s.source, a)
            if operand.class_ == F64 {
                floatCmp(s, d, o, A.reg(a), A.reg(b))
            } else {
                emit(s, A.Cmp(A.reg(a), A.reg(b), widthOf(operand)))
                emit(s, A.CSet(d, condOf(o)))
            }
        }
        Neg(a) -> if r.class_ == F64 { emit(s, A.FNeg(d, A.reg(a))) }
                  else { emit(s, A.Sub(d, A.xzr, A.reg(a), w)) }
        -- A double's bit pattern, built in the general file and moved across.
        -- No literal pool: `fmov` takes only a small set of immediates, and a
        -- constant this compiler cannot spell as one would need a pool entry
        -- the module emitter has to lay out. Four `mov`s and a transfer need
        -- nothing from anybody.
        ConstFloat(x) -> {
            let bits = temp(s, Ssa.untraced(I64))
            constant(s, bits, Float.bits(x))
            emit(s, A.FMovIn(d, bits))
        }
        Convert(IntToFloat, a) -> emit(s, A.Scvtf(d, A.reg(a)))
        Convert(FloatToInt, a) -> emit(s, A.Fcvtzs(d, A.reg(a)))
        -- Both directions are one bitfield extract. `Widen` is a *zero*
        -- extension -- `Turkey.Llvm` emits `zext`, not `sext` -- and `Narrow`
        -- keeps the low bits of the target's width, so the two differ only in
        -- which representation says how many bits to keep.
        Convert(Widen, a) ->
            emit(s, A.Ubfx(d, A.reg(a), 0, bits(Ssa.repOf(s.source, a))))
        Convert(Narrow, a) -> emit(s, A.Ubfx(d, A.reg(a), 0, bits(r)))
        -- The same machine value with a different obligation on the
        -- collector, which is a fact about `Rep` and not an instruction.
        Convert(Retag, a) -> emit(s, A.Mov(d, A.reg(a), A.W64))
        Not(a) -> emit(s, A.EorImm(d, A.reg(a), 1, A.W32))
        -- An object slot is at a constant offset from the header.
        ObjectGet(target, index) ->
            emit(s, A.Ldr(d, A.Offset(A.reg(target), 24 + 8 * index), A.W64))
        ObjectTag(target) -> emit(s, A.Ldr(d, A.Offset(A.reg(target), 4), A.W32))
        ArrayLength(target) -> emit(s, A.Ldr(d, A.Offset(A.reg(target), 8), A.W64))
        CellLoad(target) -> emit(s, A.Ldr(d, A.Offset(A.reg(target), 0), A.W64))
        CellStore(target, v) -> {
            emit(s, A.Str(A.reg(v), A.Offset(A.reg(target), 0), A.W64))
            -- A `str` of a `d` register stores the same bits a `str` of an
            -- `x` would, so no transfer is needed here and none below: only
            -- the *call* boundaries need one.
            emit(s, A.Mov(d, A.xzr, A.W64))
        }
        -- An array's elements start after the header, and are the element's
        -- own width apart -- not eight, which is why the width comes from
        -- `Turkey.Runtime` rather than being assumed here.
        ArrayGet(target, index) -> {
            let at = elementAt(s, A.reg(target), A.reg(index), r.class_)
            match Rt.elementWidth(r.class_) {
                1 -> emit(s, A.Ldrb(d, at))
                4 -> emit(s, A.Ldr(d, at, A.W32))
                _ -> emit(s, A.Ldr(d, at, A.W64))
            }
        }
        ArraySet(target, index, v) -> {
            let held = Ssa.repOf(s.source, v)
            let at = elementAt(s, A.reg(target), A.reg(index), held.class_)
            match Rt.elementWidth(held.class_) {
                1 -> emit(s, A.Strb(A.reg(v), at))
                4 -> emit(s, A.Str(A.reg(v), at, A.W32))
                _ -> emit(s, A.Str(A.reg(v), at, A.W64))
            }
            emit(s, A.Mov(d, A.xzr, A.W64))
        }
        -- A literal's `TurkeyString` is built once by the module's entry
        -- sequence and cached in the permanent root array, so reading one is
        -- a load and not an allocation -- which is also why `ConstString` is
        -- not a safepoint (`Turkey.Roots.isSafepoint`).
        ConstString(text) ->
            emit(s, A.Ldr(d, slotOf(s, Asm.literalSlot(s.asm, text)), A.W64))
        GlobalLoad(name) -> emit(s, A.Ldr(d, globalAt(s, name), A.W64))
        GlobalStore(name, v) -> {
            emit(s, A.Str(A.reg(v), globalAt(s, name), A.W64))
            emit(s, A.Mov(d, A.xzr, A.W64))
        }
        -- Everything that becomes a runtime call. The heap operations are at
        -- the runtime's ABI by design (`Turkey.LowIr`'s header), so selecting
        -- them is choosing a symbol and marshalling arguments -- there is no
        -- instruction to pick.
        -- The runtime's untyped interfaces take a raw 64-bit word, so a double
        -- crosses as its bits rather than in its own file -- the same thing
        -- `Turkey.Llvm.widened` does with a `bitcast`.
        Box(a) -> runtimeCall(s, d, runtimeSymbolOf("turkey_box"), [raw(s, a)],
                              [immediate(s, Rt.layoutCode(Ssa.repOf(s.source, a)))])
        Unbox(a) -> runtimeCall(s, d, runtimeSymbolOf("turkey_unbox"), [A.reg(a)],
                                [immediate(s, Rt.layoutCode(r))])
        CellNew(a) -> runtimeCall(s, d, runtimeSymbolOf("turkey_cell_new"), [raw(s, a)],
                                  [immediate(s, Rt.traceBit(Ssa.repOf(s.source, a)))])
        ObjectNew(kind, tag, count, bitmap) ->
            runtimeCall(s, d, runtimeSymbolOf("turkey_object_new"), [],
                        [immediate(s, kind), immediate(s, tag),
                         immediate(s, count), immediate(s, bitmap)])
        ObjectSet(target, index, v) ->
            runtimeCall(s, d, runtimeSymbolOf("turkey_object_set"),
                        [A.reg(target)], [immediate(s, index), raw(s, v)])
        ArrayNew(length, initial, element) ->
            runtimeCall(s, d, runtimeSymbolOf("turkey_array_new"),
                        [A.reg(length), raw(s, initial)],
                        [immediate(s, Rt.elementWidth(element)),
                         immediate(s, Rt.layoutCode(Ssa.untraced(element)))])
        -- The code operand is the function's *address*, which is what the
        -- runtime stores and what an indirect call loads back out of slot 0.
        ClosureNew(code, count, bitmap) ->
            runtimeCall(s, d, runtimeSymbolOf("turkey_closure_new"),
                        [address(s, symbolFor(code))],
                        [immediate(s, count), immediate(s, bitmap)])
        ClosureCapture(closure, index, v) ->
            runtimeCall(s, d, runtimeSymbolOf("turkey_closure_capture"),
                        [A.reg(closure)], [immediate(s, index), raw(s, v)])
        Call(callee, args) -> {
            -- Each argument's file comes from its representation, which is the
            -- one place that fact is written down. A `Float` parameter is a
            -- `double` in the C signature too, so this agrees with the runtime
            -- without either side stating it twice.
            let passed = []
            for a in args {
                Array.push(passed, (A.reg(a), A.bankOf(Ssa.repOf(s.source, a))))
            }
            let want = A.bankOf(r)
            match callee {
                Direct(name) ->
                    call(s, d, want, want, Some(symbolFor(name)), A.xzr, passed)
                Runtime(name) -> match runtimeSymbol(name) {
                    Some(sym) ->
                        call(s, d, want, resultBank(name), Some(sym), A.xzr,
                             passed)
                    -- A few primitives are a handful of compares, and a
                    -- runtime entry point per predicate would be a call where
                    -- a compare will do. The same ones `Turkey.Llvm` inlines,
                    -- for the same reason.
                    None -> if !inlinePrim(s, d, name, passed) {
                        stop(s, "the runtime function " + name)
                    }
                }
                -- A closure: the code is slot 0 and the environment slot 1,
                -- and the environment goes first, which is the uniform
                -- convention `Turkey.SsaLower.closureCall` establishes.
                Indirect(closure) -> {
                    let code = temp(s, Ssa.traced(Ptr))
                    let env = temp(s, Ssa.traced(Ptr))
                    emit(s, A.Ldr(code, A.Offset(A.reg(closure), 24), A.W64))
                    emit(s, A.Ldr(env, A.Offset(A.reg(closure), 32), A.W64))
                    -- Every argument and the result of a closure call is
                    -- boxed, so this one is entirely in the general file
                    -- whatever the underlying function's types are.
                    call(s, d, want, want, None, code,
                         Array.append([(env, A.Gp)], passed))
                }
            }
        }
        _ -> stop(s, L.showOpName(i.op))
    }
}

-- A constant in a fresh register, for an operand the IR gives as an integer
-- and the machine wants in a register.
fun immediate(s : Sel, n : Int) -> Reg {
    let t = temp(s, Ssa.untraced(I64))
    constant(s, t, n)
    t
}


-- A call to one of the runtime's untyped entry points: every argument and the
-- result is a machine word, because `raw` has already moved any double's bits
-- into the general file.
fun runtimeCall(s : Sel, d : Reg, sym : String, first : Array Reg,
                rest : Array Reg) -> Unit {
    let args = []
    for a in Array.append(first, rest) { Array.push(args, (a, A.Gp)) }
    call(s, d, A.Gp, A.Gp, Some(sym), A.xzr, args)
}

-- A value as a general register holding its bits.
--
-- The runtime's untyped interfaces take an `i64`, so a double crosses as its
-- bit pattern. `Turkey.Llvm` spells the same step `bitcast double ... to i64`.
fun raw(s : Sel, v : Value) -> Reg {
    if Ssa.repOf(s.source, v).class_ != F64 { return A.reg(v) }
    let bits = temp(s, Ssa.untraced(I64))
    emit(s, A.FMovOut(bits, A.reg(v)))
    bits
}

-- A move between any two banks. The pair of banks is what picks the
-- instruction, and `fmov` spells three of the four.
fun move(s : Sel, d : Reg, dBank : A.Bank, a : Reg, aBank : A.Bank) -> Unit =
    match dBank {
        Gp -> match aBank {
            Gp -> emit(s, A.Mov(d, a, A.W64))
            Fp -> emit(s, A.FMovOut(d, a))
        }
        Fp -> match aBank {
            Gp -> emit(s, A.FMovIn(d, a))
            Fp -> emit(s, A.FMov(d, a))
        }
    }

-- The AAPCS64 sequence: arguments into the argument registers, the call, the
-- result out of the result register.
--
-- **The two files are numbered separately.** An integer argument takes the
-- next `x` and a double the next `d`, so `f(1, 2.0, 3)` passes `x0`, `d0`,
-- `x1` -- not `x0`, `d1`, `x2`. Two counters rather than one, which is the
-- whole of the difference and the whole of what a single counter would get
-- silently wrong.
--
-- The moves to and from the physical registers are ordinary instructions and
-- the allocator will coalesce most of them away. `CallArgs` carries what the
-- call reads and writes, because `Bl` names neither -- without it the
-- allocator would see a call that uses nothing and keep nothing alive across
-- it.
fun call(s : Sel, d : Reg, dBank : A.Bank, resultBank : A.Bank,
         sym : Option String, indirect : Reg,
         args : Array (Reg, A.Bank)) -> Unit {
    let ins = []
    var general = 0
    var vector = 0
    for (a, bank) in args {
        let into = match bank {
            Gp -> { let at = general; general = general + 1; A.argRegs[at] }
            Fp -> { let at = vector; vector = vector + 1; A.fpArgRegs[at] }
        }
        if general > 8 || vector > 8 {
            stop(s, "a call with more than eight arguments in one file")
            return
        }
        move(s, into, bank, a, bank)
        Array.push(ins, into)
    }
    -- The *physical* registers the call reads and writes, not the virtuals
    -- either side of them. Passing the virtual destination here made it
    -- defined twice -- once by this and once by the `mov` out of `x0` below --
    -- which `Ssa.verify` says plainly and nothing else would have: the
    -- allocator would simply have seen two definitions of one value and
    -- believed them.
    --
    -- `virtualOf` drops physical registers, so this contributes nothing to
    -- liveness today. That is correct rather than pointless: the fact is
    -- recorded where the allocator will read it, and the allocator is what
    -- teaches `uses`/`defs` about physical registers.
    let out = match resultBank { Gp -> A.x0; Fp -> A.d0 }
    emit(s, A.CallArgs(ins, [out]))
    match sym {
        Some(name) -> emit(s, A.Bl(name))
        None -> emit(s, A.Blr(indirect))
    }
    -- The callee's result file and the destination's need not agree:
    -- `turkey_unbox` answers a double's bits in `x0`, and the value being
    -- built is a double.
    move(s, d, dBank, out, resultBank)
}

-- Where a global lives: in the root array if the collector must scan it, in
-- its own storage if not. One rule, in `Turkey.Asm`, because the module
-- emitter has to lay out the same array this reads from.
fun globalAt(s : Sel, name : String) -> A.Mem =
    match Asm.globalSlot(s.asm, name) {
        Some(slot) -> slotOf(s, slot)
        None -> A.Offset(address(s, Asm.globalSymbol(name)), 0)
    }

-- One slot of the permanent root array.
fun slotOf(s : Sel, slot : Int) -> A.Mem =
    A.Offset(address(s, Asm.rootsSymbol), 8 * slot)

-- A symbol's address, in a register. Two instructions on arm64: the page, then
-- the offset within it.
fun address(s : Sel, sym : String) -> Reg {
    let page = temp(s, Ssa.untraced(Ptr))
    let full = temp(s, Ssa.untraced(Ptr))
    emit(s, A.Adrp(page, sym))
    -- Into a *second* register, not back into the first. `add` takes distinct
    -- source and destination, so there is no reason to redefine a value here
    -- and every reason not to: `Ssa.verify` counts definitions, and the
    -- allocator will too.
    emit(s, A.AddLo(full, page, sym))
    full
}

fun symbolFor(name : String) -> String = Asm.symbolFor(name)

-- A runtime entry point this pass names directly rather than through the
-- primitive table: the heap operations have no `Prim.` spelling, because the
-- lowering emits them and no program calls them.
fun runtimeSymbolOf(name : String) -> String = Asm.runtimeFor(name)

-- One element of an array, by address. The header first, then the index scaled
-- by the element's width.
fun elementAt(s : Sel, array : Reg, index : Reg, c : RepClass) -> A.Mem {
    let base = temp(s, Ssa.untraced(Ptr))
    emit(s, A.AddImm(base, array, 24, A.W64))
    let width = Rt.elementWidth(c)
    if width == 1 { return A.Indexed(base, index) }
    let scaled = temp(s, Ssa.untraced(I64))
    emit(s, A.Lsl(scaled, index, immediate(s, shiftFor(width)), A.W64))
    A.Indexed(base, scaled)
}

fun shiftFor(width : Int) -> Int = match width {
    4 -> 2
    _ -> 3
}

-- A primitive's runtime symbol, as the assembler wants it written.
--
-- The table is `Turkey.Runtime`'s -- these are the symbols a C file defines,
-- not a choice this pass makes -- and what is left here is Mach-O's leading
-- underscore. C's return type is not consulted: every one of these fits in
-- `x0` and the caller already knows the representation it asked for.
fun runtimeSymbol(name : String) -> Option String =
    match Rt.entryPoint(name) {
        Some(entry) -> Some(Asm.runtimeFor(entry.symbol))
        None -> None
    }

-- A 64-bit constant, in as few instructions as it takes.
--
-- `movz` writes one 16-bit field and zeroes the rest; each `movk` after it
-- writes another and leaves the rest. So a small constant is one instruction
-- and the worst case is four, which is what arm64 offers and why constants are
-- not pooled here.
-- A constant, as one instruction. The `movz`/`movk` sequence that builds it is
-- the printer's business: every `movk` after the first reads the register it
-- writes, so emitting them here would define one value four times.
fun constant(s : Sel, d : Reg, n : Int) -> Unit =
    if n == 0 { emit(s, A.Mov(d, A.xzr, A.W64)) }
    else { emit(s, A.MovConst(d, n)) }

-- The arithmetic that panics rather than wrapping, and the branches that make
-- it do so.
--
-- `Int` addition, subtraction and multiplication panic on overflow -- that is
-- what `Prim.intAddWrapping` exists to opt out of -- and division panics on a
-- zero divisor and on the one quotient that does not fit.
--
-- **The check stays here rather than moving into the IR**, which is the
-- decision `NATIVE-BACKEND.md` records: LLVM writes
-- `llvm.sadd.with.overflow`, arm64 reads a flag register the IR has no way to
-- name, and a target-neutral expansion would pessimize both. Two
-- implementations of one *rule* is the hazard; two encodings of one *check* is
-- the job.
--
-- Only at `i64`. `Byte` and `Char` arithmetic widens to `Int` first, so a
-- narrow `Bin` reaching here is already known to fit -- the same reasoning
-- `Turkey.Llvm.emitBin` states, and the reason both start by asking the
-- representation.
fun trapping(s : Sel, i : Low, d : Reg, r : Rep, o : BinOp, a : Reg, b : Reg,
             w : A.Width) -> Unit {
    if r.class_ != I64 {
        stop(s, "the binary operation " + show(o) + " at a narrow width")
        return
    }
    match o {
        Add -> overflowed(s, A.AddS(d, a, b, w), "integer overflow in +")
        Sub -> overflowed(s, A.SubS(d, a, b, w), "integer overflow in -")
        -- arm64 has no flag-setting multiply, so overflow is detected the way
        -- the hardware leaves it: the full 128-bit product overflows exactly
        -- when its high half is not the sign extension of its low half.
        Mul -> {
            let high = temp(s, Ssa.untraced(I64))
            let sign = temp(s, Ssa.untraced(I64))
            emit(s, A.Mul(d, a, b, w))
            emit(s, A.SMulH(high, a, b))
            emit(s, A.Asr(sign, d, immediate(s, 63), A.W64))
            emit(s, A.Cmp(high, sign, A.W64))
            guardOn(s, A.Ne, "integer overflow in *")
        }
        Div -> {
            divisorGuard(s, b, "division by zero")
            mostNegative(s, a, b, "integer overflow in /")
            emit(s, A.SDiv(d, a, b, w))
        }
        -- The remainder is the division's, backwards: `msub` computes
        -- `a - (a / b) * b`, which is what arm64 has instead of an `srem`.
        --
        -- The most negative integer divided by -1 is taken out rather than
        -- computed. Its mathematical remainder is zero, but the quotient it
        -- goes through does not exist -- the same case LLVM leaves undefined
        -- and `Turkey.Llvm` special-cases for the same reason.
        Rem -> {
            divisorGuard(s, b, "remainder by zero")
            let low = temp(s, Ssa.untraced(I64))
            constant(s, low, Int.shl(1, 63))
            emit(s, A.Cmp(a, low, A.W64))
            let isLow = tempValue(s, Ssa.untraced(I1))
            emit(s, A.CSet(A.reg(isLow), A.Eq))
            emit(s, A.CmpImm(b, -1, A.W64))
            let isNegative = tempValue(s, Ssa.untraced(I1))
            emit(s, A.CSet(A.reg(isNegative), A.Eq))
            let special = tempValue(s, Ssa.untraced(I1))
            emit(s, A.And(A.reg(special), A.reg(isLow), A.reg(isNegative),
                          A.W32))
            let zero = block(s)
            let ordinary = block(s)
            let after = block(s)
            -- The result is the merge block's parameter, which is what a
            -- select is in a graph with block parameters rather than phis.
            Array.push(s.out.blocks[after].params, i.result)
            s.out.blocks[s.at].term = Branch(special, BlockId(zero),
                                             BlockId(ordinary))
            s.at = zero
            let none = tempValue(s, Ssa.untraced(I64))
            emit(s, A.Mov(A.reg(none), A.xzr, A.W64))
            s.out.blocks[s.at].term = Jump(BlockId(after), [none])
            s.at = ordinary
            let quotient = temp(s, Ssa.untraced(I64))
            let rest = tempValue(s, Ssa.untraced(I64))
            emit(s, A.SDiv(quotient, a, b, w))
            emit(s, A.MSub(A.reg(rest), quotient, b, a, w))
            s.out.blocks[s.at].term = Jump(BlockId(after), [rest])
            s.at = after
        }
        _ -> stop(s, "the binary operation " + show(o))
    }
}

-- A flag-setting instruction, and a panic if it overflowed.
fun overflowed(s : Sel, made : A.Arm, message : String) -> Unit {
    emit(s, made)
    guardOn(s, A.Vs, message)
}

fun divisorGuard(s : Sel, b : Reg, message : String) -> Unit {
    emit(s, A.CmpImm(b, 0, A.W64))
    guardOn(s, A.Eq, message)
}

-- The one quotient that does not fit: the most negative integer over -1.
fun mostNegative(s : Sel, a : Reg, b : Reg, message : String) -> Unit {
    let low = temp(s, Ssa.untraced(I64))
    constant(s, low, Int.shl(1, 63))
    emit(s, A.Cmp(a, low, A.W64))
    let isLow = tempValue(s, Ssa.untraced(I1))
    emit(s, A.CSet(A.reg(isLow), A.Eq))
    emit(s, A.CmpImm(b, -1, A.W64))
    let isNegative = tempValue(s, Ssa.untraced(I1))
    emit(s, A.CSet(A.reg(isNegative), A.Eq))
    let both = tempValue(s, Ssa.untraced(I1))
    emit(s, A.And(A.reg(both), A.reg(isLow), A.reg(isNegative), A.W32))
    guard(s, both, message)
}

-- Materialize the flags into a register, then guard on it.
--
-- The `cset` is what a peephole would later fold back into a `b.cond`, and it
-- is written this way on purpose: `Term` is not parameterized the way the
-- instruction type is, so a condition-code branch is not something the machine
-- CFG can express as a terminator. Naming the bit keeps the graph a graph and
-- costs one instruction, which is the right trade while there is no encoder to
-- fold it.
--
-- Nothing may come between the flag-setting instruction and this `cset`. The
-- register allocator is the thing that could insert one, and it is not written
-- yet -- recorded in `NATIVE-BACKEND.md` so that it is a constraint the
-- allocator is designed against rather than a bug it causes.
fun guardOn(s : Sel, c : A.Cond, message : String) -> Unit {
    let failed = tempValue(s, Ssa.untraced(I1))
    emit(s, A.CSet(A.reg(failed), c))
    guard(s, failed, message)
}

-- Split the block: panic if `condition` holds, carry on if it does not.
--
-- The panic path leaves the function with a zero of its result type, exactly
-- as `Turkey.Llvm.guard` does -- `turkey_panic` sets a flag and returns rather
-- than unwinding, so every caller up the chain checks it and does the same.
fun guard(s : Sel, condition : Value, message : String) -> Unit {
    let failed = block(s)
    let carry = block(s)
    s.out.blocks[s.at].term = Branch(condition, BlockId(failed),
                                     BlockId(carry))
    s.at = failed
    let text = address(s, Asm.messageSymbol(s.asm, message))
    call(s, temp(s, Ssa.untraced(I64)), A.Gp, A.Gp,
         Some(Asm.runtimeFor("turkey_panic")), A.xzr, [(text, A.Gp)])
    let zero = tempValue(s, s.out.result)
    emit(s, A.Mov(A.reg(zero), A.xzr, A.W64))
    s.out.blocks[s.at].term = Ret(zero)
    s.at = carry
}

fun binOp(o : BinOp) -> Option (fun(Reg, Reg, Reg, A.Width) -> A.Arm) = match o {
    AddWrap -> Some(fun(d, a, b, w) = A.Add(d, a, b, w))
    SubWrap -> Some(fun(d, a, b, w) = A.Sub(d, a, b, w))
    MulWrap -> Some(fun(d, a, b, w) = A.Mul(d, a, b, w))
    And -> Some(fun(d, a, b, w) = A.And(d, a, b, w))
    Or -> Some(fun(d, a, b, w) = A.Orr(d, a, b, w))
    Xor -> Some(fun(d, a, b, w) = A.Eor(d, a, b, w))
    Shl -> Some(fun(d, a, b, w) = A.Lsl(d, a, b, w))
    Shr -> Some(fun(d, a, b, w) = A.Lsr(d, a, b, w))
    ShrU -> Some(fun(d, a, b, w) = A.Lsr(d, a, b, w))
    -- The trapping forms are `trapping` above: they are a branch and a block,
    -- not an instruction, so they cannot be a table entry.
    _ -> None
}

-- How many bits a representation keeps.
fun bits(r : Rep) -> Int = match r.class_ {
    I1 -> 1
    I8 -> 8
    I32 -> 32
    _ -> 64
}

-- A primitive written out rather than called.
--
-- Answers whether it handled the name, so that the caller reports the ones
-- that have neither an entry point nor a rule instead of emitting something
-- plausible for them (FINDINGS 63).
fun inlinePrim(s : Sel, d : Reg, name : String,
               args : Array (Reg, A.Bank)) -> Bool =
    match name {
        -- A scalar value is a code point that is not a surrogate: at most
        -- U+10FFFF, and outside U+D800..U+DFFF.
        "Prim.charIsScalar" -> {
            let (point, _) = args[0]
            let big = flag(s, point, 1114111, A.Hi)
            let atLeast = flag(s, point, 55296, A.Hs)
            let atMost = flag(s, point, 57343, A.Ls)
            let surrogate = temp(s, Ssa.untraced(I1))
            emit(s, A.And(surrogate, atLeast, atMost, A.W32))
            let bad = temp(s, Ssa.untraced(I1))
            emit(s, A.Orr(bad, big, surrogate, A.W32))
            emit(s, A.EorImm(d, bad, 1, A.W32))
            True
        }
        _ -> False
    }

-- One unsigned comparison against a constant, as a bit in a register.
fun flag(s : Sel, a : Reg, n : Int, c : A.Cond) -> Reg {
    emit(s, A.Cmp(a, immediate(s, n), A.W64))
    let out = temp(s, Ssa.untraced(I1))
    emit(s, A.CSet(out, c))
    out
}

-- Which file a runtime entry point answers in. `turkey_float_floor` returns a
-- `double` and so answers in `d0`; everything else answers in `x0`.
fun resultBank(name : String) -> A.Bank = match Rt.entryPoint(name) {
    Some(entry) -> match entry.result {
        Some(F64) -> A.Fp
        _ -> A.Gp
    }
    None -> A.Gp
}

-- Floating point arithmetic, which does not trap.
--
-- `Int` overflow panics and float overflow does not: IEEE 754 answers an
-- infinity, which is a value. So none of this is guarded, and the asymmetry is
-- the language's rather than this backend's.
fun floatBin(s : Sel, d : Reg, o : BinOp, a : Reg, b : Reg) -> Unit = match o {
    Add -> emit(s, A.FAdd(d, a, b))
    Sub -> emit(s, A.FSub(d, a, b))
    Mul -> emit(s, A.FMul(d, a, b))
    Div -> emit(s, A.FDiv(d, a, b))
    -- arm64 has no remainder instruction for doubles, and LLVM's `frem` is
    -- `fmod` by definition, so this is the call the other backend also
    -- eventually becomes.
    Rem -> call(s, d, A.Fp, A.Fp, Some(Asm.runtimeFor("turkey_float_fmod")),
                A.xzr, [(a, A.Fp), (b, A.Fp)])
    _ -> stop(s, "the binary operation " + show(o) + " on a double")
}

-- Comparing doubles, which is not comparing integers with a different
-- mnemonic.
--
-- `Turkey.Llvm` emits the *ordered* predicates -- `oeq`, `olt` and so on --
-- which are false when either side is NaN. ARM's condition codes do not line
-- up with those one for one: after `fcmp`, `ne` means "not equal **or
-- unordered**", which is LLVM's `une` and not its `one`. Every other ordered
-- predicate has a single code -- `lo` for less-than, since `fcmp` clears the
-- carry there, and `ls` for less-or-equal -- and `one` is the one that needs
-- two and an `orr`.
--
-- Getting this wrong would be invisible until a NaN reached a comparison,
-- which no corpus program does today.
fun floatCmp(s : Sel, d : Reg, o : CmpOp, a : Reg, b : Reg) -> Unit {
    emit(s, A.FCmp(a, b))
    match o {
        Eq -> emit(s, A.CSet(d, A.Eq))
        Lt -> emit(s, A.CSet(d, A.Lo))
        Le -> emit(s, A.CSet(d, A.Ls))
        Gt -> emit(s, A.CSet(d, A.Gt))
        Ge -> emit(s, A.CSet(d, A.Ge))
        -- Ordered and not equal: less than, or greater than. Not `ne`, which
        -- is also true of a NaN.
        Ne -> {
            let below = temp(s, Ssa.untraced(I1))
            let above = temp(s, Ssa.untraced(I1))
            emit(s, A.CSet(below, A.Lo))
            emit(s, A.CSet(above, A.Gt))
            emit(s, A.Orr(d, below, above, A.W32))
        }
    }
}

fun condOf(o : CmpOp) -> A.Cond = match o {
    Eq -> A.Eq
    Ne -> A.Ne
    Lt -> A.Lt
    Le -> A.Le
    Gt -> A.Gt
    Ge -> A.Ge
}
