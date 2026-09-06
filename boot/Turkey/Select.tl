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
import Turkey.Arm64 (Reg(..), Inst(..))
import Turkey.LowIr as L
import Turkey.LowIr (Op(..), Low(..), BinOp(..), CmpOp(..), Conv(..),
                     Callee(..))
import Turkey.Runtime as Rt
import Turkey.Runtime (Entry(..))
import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..), Term(..),
                   Func(..), Block(..))

type Unsupported = Unsupported { name : String, what : String }

type Sel = Sel {
    source  : Func Low,
    out     : Func Inst,
    -- The block being emitted into.
    at      : Int,
    -- The first opcode with no rule, if any.
    stopped : Option String,
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
fun temp(s : Sel, r : Rep) -> Reg = A.reg(Ssa.freshValue(s.out, r))

fun widthOf(r : Rep) -> A.Width = match r.class_ {
    I64 -> A.W64
    Ptr -> A.W64
    _ -> A.W32
}

-- -- selection ----------------------------------------------------------------------

fun selectModule(functions : Array (Func Low))
        -> (Array (Func Inst), Array Unsupported) {
    let done = []
    let skipped = []
    for f in functions {
        match selectFunc(f) {
            Some(g) -> Array.push(done, g)
            None -> {}
        }
    }
    for f in functions {
        match reasonFor(f) {
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
fun reasonFor(f : Func Low) -> Option String {
    let s = start(f)
    run(s)
    s.stopped
}

fun selectFunc(f : Func Low) -> Option (Func Inst) {
    let s = start(f)
    run(s)
    match s.stopped {
        Some(_) -> None
        None -> Some(s.out)
    }
}

fun start(f : Func Low) -> Sel {
    let g = Ssa.newFunc(f.name, f.result)
    -- The same value numbering, so a `Value` means the same thing on both
    -- sides and `reps` describes both. Selection only ever adds.
    for r in f.reps { Array.push(g.reps, r) }
    for p in f.params { Array.push(g.params, p) }
    for block in f.blocks {
        let made = Ssa.newBlock()
        for p in block.params { Array.push(made.params, p) }
        made.term = block.term
        Array.push(g.blocks, made)
    }
    g.entry = f.entry
    Sel { source = f, out = g, at = 0, stopped = None }
}

fun run(s : Sel) -> Unit {
    for var b = 0; b < len(s.source.blocks); b = b + 1 {
        s.at = b
        for i in s.source.blocks[b].insts { selectInst(s, i) }
    }
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
        Bin(o, a, b) -> match binOp(o) {
            Some(make) -> emit(s, make(d, A.reg(a), A.reg(b), w))
            None -> stop(s, "the binary operation " + show(o))
        }
        Cmp(o, a, b) -> {
            emit(s, A.Cmp(A.reg(a), A.reg(b),
                        widthOf(Ssa.repOf(s.source, a))))
            emit(s, A.CSet(d, condOf(o)))
        }
        Neg(a) -> emit(s, A.Sub(d, A.xzr, A.reg(a), w))
        Not(a) -> emit(s, A.EorImm(d, A.reg(a), 1, A.W32))
        -- An object slot is at a constant offset from the header.
        ObjectGet(target, index) ->
            emit(s, A.Ldr(d, A.Offset(A.reg(target), 24 + 8 * index), A.W64))
        ObjectTag(target) -> emit(s, A.Ldr(d, A.Offset(A.reg(target), 4), A.W32))
        ArrayLength(target) -> emit(s, A.Ldr(d, A.Offset(A.reg(target), 8), A.W64))
        CellLoad(target) -> emit(s, A.Ldr(d, A.Offset(A.reg(target), 0), A.W64))
        CellLoad(target) -> emit(s, A.Ldr(d, A.Offset(A.reg(target), 0), A.W64))
        -- Everything that becomes a runtime call. The heap operations are at
        -- the runtime's ABI by design (`Turkey.LowIr`'s header), so selecting
        -- them is choosing a symbol and marshalling arguments -- there is no
        -- instruction to pick.
        Box(a) -> runtimeCall(s, d, "_turkey_box", [A.reg(a)],
                              [immediate(s, Rt.layoutCode(Ssa.repOf(s.source, a)))])
        Unbox(a) -> runtimeCall(s, d, "_turkey_unbox", [A.reg(a)],
                                [immediate(s, Rt.layoutCode(r))])
        CellNew(a) -> runtimeCall(s, d, "_turkey_cell_new", [A.reg(a)],
                                  [immediate(s, Rt.traceBit(Ssa.repOf(s.source, a)))])
        ObjectNew(kind, tag, count, bitmap) ->
            runtimeCall(s, d, "_turkey_object_new", [],
                        [immediate(s, kind), immediate(s, tag),
                         immediate(s, count), immediate(s, bitmap)])
        ObjectSet(target, index, v) ->
            runtimeCall(s, d, "_turkey_object_set",
                        [A.reg(target)], [immediate(s, index), A.reg(v)])
        Call(callee, args) -> {
            let passed = []
            for a in args { Array.push(passed, A.reg(a)) }
            match callee {
                Direct(name) -> call(s, d, Some(symbolFor(name)), A.xzr, passed)
                Runtime(name) -> match runtimeSymbol(name) {
                    Some(sym) -> call(s, d, Some(sym), A.xzr, passed)
                    None -> stop(s, "the runtime function " + name)
                }
                -- A closure: the code is slot 0 and the environment slot 1,
                -- and the environment goes first, which is the uniform
                -- convention `Turkey.SsaLower.closureCall` establishes.
                Indirect(closure) -> {
                    let code = temp(s, Ssa.traced(Ptr))
                    let env = temp(s, Ssa.traced(Ptr))
                    emit(s, A.Ldr(code, A.Offset(A.reg(closure), 24), A.W64))
                    emit(s, A.Ldr(env, A.Offset(A.reg(closure), 32), A.W64))
                    call(s, d, None, code, Array.append([env], passed))
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


fun runtimeCall(s : Sel, d : Reg, sym : String, first : Array Reg,
                rest : Array Reg) -> Unit =
    call(s, d, Some(sym), A.xzr, Array.append(first, rest))

-- The AAPCS64 sequence: arguments into x0..x7, the call, the result out of x0.
--
-- The moves to and from the physical registers are ordinary instructions and
-- the allocator will coalesce most of them away. `CallArgs` carries what the
-- call reads and writes, because `Bl` names neither -- without it the
-- allocator would see a call that uses nothing and keep nothing alive across
-- it.
fun call(s : Sel, d : Reg, sym : Option String, indirect : Reg,
         args : Array Reg) -> Unit {
    if len(args) > 8 {
        stop(s, "a call with more than eight arguments")
        return
    }
    let ins = []
    for var i = 0; i < len(args); i = i + 1 {
        emit(s, A.Mov(A.argRegs[i], args[i], A.W64))
        Array.push(ins, A.argRegs[i])
    }
    emit(s, A.CallArgs(args, [d]))
    match sym {
        Some(name) -> emit(s, A.Bl(name))
        None -> emit(s, A.Blr(indirect))
    }
    emit(s, A.Mov(d, A.x0, A.W64))
}

fun symbolFor(name : String) -> String = "_" + name

-- A primitive's runtime symbol, as the assembler wants it written.
--
-- The table is `Turkey.Runtime`'s -- these are the symbols a C file defines,
-- not a choice this pass makes -- and what is left here is Mach-O's leading
-- underscore. C's return type is not consulted: every one of these fits in
-- `x0` and the caller already knows the representation it asked for.
fun runtimeSymbol(name : String) -> Option String =
    match Rt.entryPoint(name) {
        Some(entry) -> Some(symbolFor(entry.symbol))
        None -> None
    }

-- A 64-bit constant, in as few instructions as it takes.
--
-- `movz` writes one 16-bit field and zeroes the rest; each `movk` after it
-- writes another and leaves the rest. So a small constant is one instruction
-- and the worst case is four, which is what arm64 offers and why constants are
-- not pooled here.
fun constant(s : Sel, d : Reg, n : Int) -> Unit {
    if n == 0 {
        emit(s, A.Mov(d, A.xzr, A.W64))
        return
    }
    var first = True
    for var shift = 0; shift < 64; shift = shift + 16 {
        let part = Int.and(Int.shr(n, shift), 65535)
        if part != 0 || (first && shift == 48) {
            if first { emit(s, A.MovZ(d, part, shift)) }
            else { emit(s, A.MovK(d, part, shift)) }
            first = False
        }
    }
    if first { emit(s, A.Mov(d, A.xzr, A.W64)) }
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
    -- The trapping forms need their check emitted around them, which is this
    -- backend's job and not the IR's -- and it is not written yet.
    _ -> None
}

fun condOf(o : CmpOp) -> A.Cond = match o {
    Eq -> A.Eq
    Ne -> A.Ne
    Lt -> A.Lt
    Le -> A.Le
    Gt -> A.Gt
    Ge -> A.Ge
}
