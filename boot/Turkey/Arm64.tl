module Turkey.Arm64 (
    Reg(..), Bank(..), reg, physical, isPhysical, bankOf, Cond(..), Width(..),
    Mem(..), Arm(..), Inst(..), destRegs,
    xzr, sp, fp, lr, x0, d0, argRegs, fpArgRegs, callerSaved, calleeSaved,
    fpCallerSaved, fpCalleeSaved, showReg, condName, isCall,
)

-- The arm64 machine instruction set (`NATIVE-BACKEND.md`, M28 phase 4).
--
-- This is the *second* instantiation of `Turkey.Ssa`'s CFG. That is the whole
-- point of the parameter: dominance, liveness, the verifier's shape checks and
-- the printer were written once over `Inst i`, and instruction selection
-- produces a different instruction type rather than rewriting the low IR in
-- place -- so after selection a virtual opcode is not *representable*, and
-- every `match` in every later pass stays exhaustive over machine
-- instructions only.
--
-- QBE and Go both put virtual and machine opcodes in one enum, and neither
-- language checks a switch for exhaustiveness. The two in the comparison that
-- do -- Haskell with Hoopl, Scheme with nanopass -- both parameterize or
-- generate. This is the parameterizing.
--
-- **Registers are virtual until the allocator says otherwise.** A `Reg` is a
-- `Value` from selection and a physical register afterwards, and the two are
-- one type with a tag rather than two types, because every pass between them
-- reads both. The allocation itself is a side table -- `VCode::emit` in
-- Cranelift is immutable for the same reason -- so nothing here is rewritten
-- to record it.
--
-- **What is deliberately absent.** No addressing mode beyond a base and a
-- constant or register offset, because the low IR's memory operations are
-- `ObjectGet`, `ArrayGet` and the cell operations and none of them needs more.
-- No condition-code modelling beyond `Cmp` immediately followed by its
-- consumer, because selection emits the pair together and nothing separates
-- them. Both are places where a bigger backend would generalize and this one
-- has no instruction that would use it.

import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..),
                   Effects(..), Term(..))

-- A register: virtual while selection and the allocator are running, physical
-- afterwards.
--
-- One type rather than two. A separate `Physical` type would mean every
-- printer, verifier and encoder written twice, and the moment when a function
-- stops containing virtual registers is a *fact about a pass having run*, not
-- something the type system can usefully police at this size.
-- Which file a register comes from. arm64 has two, and they are numbered
-- independently: `x0` and `d0` are different registers that are both 0.
--
-- A *virtual* register's bank is not here, because it is already written down
-- -- `Func.reps` says whether the value is `F64`, and a second copy of that
-- fact is a second copy that can disagree. A *physical* register has no rep
-- to consult, so it carries its bank.
type Bank = Gp | Fp

type Reg
    -- A value from selection. The number is an `Ssa.Value` id.
    = Virtual(Int)
    -- `x0`..`x30` and `d0`..`d31`; 31 in the general file means `xzr` or `sp`
    -- depending on the instruction.
    | Physical(Bank, Int)

fun reg(v : Value) -> Reg = Virtual(Ssa.valueId(v))

fun physical(n : Int) -> Reg = Physical(Gp, n)

fun isPhysical(r : Reg) -> Bool = match r {
    Physical(_, _) -> True
    Virtual(_) -> False
}

-- The file a value of this representation lives in.
fun bankOf(r : Rep) -> Bank = match r.class_ {
    F64 -> Fp
    _ -> Gp
}

let xzr = Physical(Gp, 31)
let sp = Physical(Gp, 31)
let fp = Physical(Gp, 29)
let lr = Physical(Gp, 30)
let x0 = Physical(Gp, 0)
let d0 = Physical(Fp, 0)

-- The first eight are the argument and result registers.
-- Closure conversion keeps most calls under eight, because a closure's
-- captures travel in its environment rather than in registers -- but it does
-- not bound them, and the claim that it did was wrong: `tests/programs/
-- manyargs.tl` has a nine-parameter function. A call needing a ninth stops in
-- `Turkey.Select`, and a *function declaring* a ninth parameter selects
-- silently and wrongly, because the incoming convention is the prologue's job.
-- See NATIVE-BACKEND.md phase 4.
-- AAPCS64 numbers the two files' argument registers separately: an integer
-- argument takes the next `x` and a double the next `d`, so a call with one of
-- each passes them in `x0` and `d0` rather than in `x0` and `x1`.
let argRegs = [Physical(Gp, 0), Physical(Gp, 1), Physical(Gp, 2),
               Physical(Gp, 3), Physical(Gp, 4), Physical(Gp, 5),
               Physical(Gp, 6), Physical(Gp, 7)]
let fpArgRegs = [Physical(Fp, 0), Physical(Fp, 1), Physical(Fp, 2),
                 Physical(Fp, 3), Physical(Fp, 4), Physical(Fp, 5),
                 Physical(Fp, 6), Physical(Fp, 7)]

-- AAPCS64: x0-x17 are caller-saved, x19-x28 callee-saved. x18 is reserved by
-- the platform on Darwin and is not in either list.
let callerSaved = [Physical(Gp, 0), Physical(Gp, 1), Physical(Gp, 2),
                   Physical(Gp, 3), Physical(Gp, 4), Physical(Gp, 5),
                   Physical(Gp, 6), Physical(Gp, 7), Physical(Gp, 8),
                   Physical(Gp, 9), Physical(Gp, 10), Physical(Gp, 11),
                   Physical(Gp, 12), Physical(Gp, 13), Physical(Gp, 14),
                   Physical(Gp, 15), Physical(Gp, 16), Physical(Gp, 17)]
let calleeSaved = [Physical(Gp, 19), Physical(Gp, 20), Physical(Gp, 21),
                   Physical(Gp, 22), Physical(Gp, 23), Physical(Gp, 24),
                   Physical(Gp, 25), Physical(Gp, 26), Physical(Gp, 27),
                   Physical(Gp, 28)]

-- The vector file's halves are the other way round from the general file's:
-- `v0`-`v7` and `v16`-`v31` are caller-saved, and only the *low 64 bits* of
-- `v8`-`v15` are callee-saved. Every double this compiler makes is 64 bits, so
-- the distinction does not bite here -- but it is why these are named `d` and
-- not `v`, and it would bite the moment anything vectorized.
let fpCallerSaved = [Physical(Fp, 0), Physical(Fp, 1), Physical(Fp, 2),
                     Physical(Fp, 3), Physical(Fp, 4), Physical(Fp, 5),
                     Physical(Fp, 6), Physical(Fp, 7), Physical(Fp, 16),
                     Physical(Fp, 17), Physical(Fp, 18), Physical(Fp, 19),
                     Physical(Fp, 20), Physical(Fp, 21), Physical(Fp, 22),
                     Physical(Fp, 23), Physical(Fp, 24), Physical(Fp, 25),
                     Physical(Fp, 26), Physical(Fp, 27), Physical(Fp, 28),
                     Physical(Fp, 29), Physical(Fp, 30), Physical(Fp, 31)]
let fpCalleeSaved = [Physical(Fp, 8), Physical(Fp, 9), Physical(Fp, 10),
                     Physical(Fp, 11), Physical(Fp, 12), Physical(Fp, 13),
                     Physical(Fp, 14), Physical(Fp, 15)]

-- The operand width. arm64 spells the same instruction `add w0, ...` at 32
-- bits and `add x0, ...` at 64, so this is a field rather than two opcodes.
type Width = W32 | W64

type Cond = Eq | Ne | Lt | Le | Gt | Ge | Lo | Ls | Hi | Hs | Vs

-- A memory operand: a base register and an offset, scaled by the access width
-- the way arm64's addressing modes are.
type Mem
    = Offset(Reg, Int)
    | Indexed(Reg, Reg)

type Arm
    -- Moves and constants. `MovZ`/`MovK` build a 64-bit constant in up to four
    -- steps; selection decides how many are needed.
    = Mov(Reg, Reg, Width)
    | MovZ(Reg, Int, Int)
    | MovK(Reg, Int, Int)
    -- Arithmetic. `S` in the name means the flags are set, which is what an
    -- overflow check reads.
    | Add(Reg, Reg, Reg, Width)
    | AddImm(Reg, Reg, Int, Width)
    | AddS(Reg, Reg, Reg, Width)
    | Sub(Reg, Reg, Reg, Width)
    | SubImm(Reg, Reg, Int, Width)
    | SubS(Reg, Reg, Reg, Width)
    | Mul(Reg, Reg, Reg, Width)
    -- The high half of a 64x64 multiply, which is how overflow is detected
    -- when `MulS` does not exist -- and it does not, on arm64.
    | SMulH(Reg, Reg, Reg)
    | SDiv(Reg, Reg, Reg, Width)
    | MSub(Reg, Reg, Reg, Reg, Width)
    | And(Reg, Reg, Reg, Width)
    | Orr(Reg, Reg, Reg, Width)
    | Eor(Reg, Reg, Reg, Width)
    | EorImm(Reg, Reg, Int, Width)
    | Lsl(Reg, Reg, Reg, Width)
    | Lsr(Reg, Reg, Reg, Width)
    | Asr(Reg, Reg, Reg, Width)
    -- Comparison, and materializing its result. `Cmp` sets the flags and
    -- `CSet` reads them; selection emits the pair adjacently and nothing
    -- between them may clobber the flags.
    | Cmp(Reg, Reg, Width)
    | CmpImm(Reg, Int, Width)
    | CSet(Reg, Cond)
    -- Sign and zero extension, which is how a narrow representation reaches a
    -- wide one.
    | Sxtw(Reg, Reg)
    | Uxtb(Reg, Reg)
    | Uxth(Reg, Reg)
    | Ubfx(Reg, Reg, Int, Int)
    -- Memory. The width is the access size, not the register size.
    | Ldr(Reg, Mem, Width)
    | Ldrb(Reg, Mem)
    | Ldrsw(Reg, Mem)
    | Str(Reg, Mem, Width)
    | Strb(Reg, Mem)
    -- A pair, which is how a frame is set up and torn down in one instruction.
    | Stp(Reg, Reg, Reg, Int)
    | Ldp(Reg, Reg, Reg, Int)
    -- The address of a symbol, in the two-instruction form arm64 uses: a page
    -- and an offset within it.
    | Adrp(Reg, String)
    | AddLo(Reg, Reg, String)
    -- Floating point. One width only, because Turkey's `Float` is a double
    -- and there is no `f32` to spell.
    --
    -- `FMov` moves between two `d`s; `FMovIn` and `FMovOut` move the *bits*
    -- between a `d` and an `x`, which is how a double reaches the runtime's
    -- untyped `i64` interfaces and comes back. All three are `fmov` to the
    -- assembler, and they are three constructors here because the register
    -- banks differ and a printer that guessed from the operands would be
    -- guessing.
    | FMov(Reg, Reg)
    | FMovIn(Reg, Reg)
    | FMovOut(Reg, Reg)
    | FAdd(Reg, Reg, Reg)
    | FSub(Reg, Reg, Reg)
    | FMul(Reg, Reg, Reg)
    | FDiv(Reg, Reg, Reg)
    | FNeg(Reg, Reg)
    | FCmp(Reg, Reg)
    -- Signed integer to double, and back with truncation toward zero -- which
    -- is what LLVM's `fptosi` does and so what this has to do.
    | Scvtf(Reg, Reg)
    | Fcvtzs(Reg, Reg)
    -- A 64-bit constant, as one instruction.
    --
    -- arm64 builds one out of a `movz` and up to three `movk`s, and every
    -- `movk` reads the register it writes. That is not SSA: the value would be
    -- defined four times, which `Ssa.verify` rejects and a register allocator
    -- would believe. So the sequence is one instruction here and the printer
    -- expands it -- the same answer QBE and Cranelift give a "load immediate",
    -- and the reason a pseudo-instruction is a category the enum has at all.
    | MovConst(Reg, Int)
    -- Calls. `Bl` is direct and `Blr` is through a register.
    | Bl(String)
    | Blr(Reg)
    -- A pseudo-instruction: the arguments a call passes and the register it
    -- answers in are not operands of `Bl`, so without this the allocator would
    -- see a call that uses and defines nothing. It carries no encoding.
    | CallArgs(Array Reg, Array Reg)
    | Ret
    | B(BlockId)
    | BCond(Cond, BlockId)
    | Brk(Int)

-- An instruction and the register it defines, mirroring `Turkey.LowIr.Low`.
--
-- Not every arm64 instruction defines a register -- a store does not -- so
-- unlike the low IR this cannot give every instruction exactly one result.
-- `defs` reads the opcode instead, which is why `Arm` carries its destination
-- inside the constructor rather than beside it.
type Inst = Inst { op : Arm }

instance Ssa.Inst Inst {
    fun uses(i) = usesOf(i.op)
    fun defs(i) = defsOf(i.op)
    -- Every machine instruction is treated as writing memory and trapping.
    -- The optimizations run *above* this level, on the low IR, so nothing here
    -- reads effects for anything but the register allocator's benefit -- and
    -- a conservative answer costs it nothing.
    fun effects(i) = Ssa.writes()
}

-- Whether this instruction is a call, which for the allocator means two
-- things at once: every caller-saved register is clobbered here, so a value
-- live across it needs a callee-saved one or a spill slot -- and it is a
-- safepoint, so the collector may run and every live traced value must be
-- somewhere the collector can see.
--
-- It lives here rather than in the allocator because which opcodes call is a
-- fact about the instruction set. `Ssa.effects` cannot answer it: every
-- machine instruction reports `writes`, deliberately, because the
-- optimizations run above this level and a conservative answer costs them
-- nothing.
fun isCall(i : Inst) -> Bool = match i.op {
    Bl(_) -> True
    Blr(_) -> True
    _ -> False
}

-- The `Value` ids an instruction reads. Physical registers are not `Value`s
-- and are absent from this: the allocator learns about them from `CallArgs`
-- and from the clobber list, not from a use.
fun usesOf(op : Arm) -> Array Value = match op {
    Mov(_, a, _) -> virtualOf([a])
    MovZ(_, _, _) -> []
    MovK(d, _, _) -> virtualOf([d])
    Add(_, a, b, _) -> virtualOf([a, b])
    AddImm(_, a, _, _) -> virtualOf([a])
    AddS(_, a, b, _) -> virtualOf([a, b])
    Sub(_, a, b, _) -> virtualOf([a, b])
    SubImm(_, a, _, _) -> virtualOf([a])
    SubS(_, a, b, _) -> virtualOf([a, b])
    Mul(_, a, b, _) -> virtualOf([a, b])
    SMulH(_, a, b) -> virtualOf([a, b])
    SDiv(_, a, b, _) -> virtualOf([a, b])
    MSub(_, a, b, c, _) -> virtualOf([a, b, c])
    And(_, a, b, _) -> virtualOf([a, b])
    Orr(_, a, b, _) -> virtualOf([a, b])
    Eor(_, a, b, _) -> virtualOf([a, b])
    EorImm(_, a, _, _) -> virtualOf([a])
    Lsl(_, a, b, _) -> virtualOf([a, b])
    Lsr(_, a, b, _) -> virtualOf([a, b])
    Asr(_, a, b, _) -> virtualOf([a, b])
    Cmp(a, b, _) -> virtualOf([a, b])
    CmpImm(a, _, _) -> virtualOf([a])
    CSet(_, _) -> []
    Sxtw(_, a) -> virtualOf([a])
    Uxtb(_, a) -> virtualOf([a])
    Uxth(_, a) -> virtualOf([a])
    Ubfx(_, a, _, _) -> virtualOf([a])
    Ldr(_, m, _) -> memUses(m)
    Ldrb(_, m) -> memUses(m)
    Ldrsw(_, m) -> memUses(m)
    Str(v, m, _) -> Array.append(virtualOf([v]), memUses(m))
    Strb(v, m) -> Array.append(virtualOf([v]), memUses(m))
    Stp(a, b, base, _) -> virtualOf([a, b, base])
    Ldp(_, _, base, _) -> virtualOf([base])
    Adrp(_, _) -> []
    AddLo(_, a, _) -> virtualOf([a])
    MovConst(_, _) -> []
    FMov(_, a) -> virtualOf([a])
    FMovIn(_, a) -> virtualOf([a])
    FMovOut(_, a) -> virtualOf([a])
    FAdd(_, a, b) -> virtualOf([a, b])
    FSub(_, a, b) -> virtualOf([a, b])
    FMul(_, a, b) -> virtualOf([a, b])
    FDiv(_, a, b) -> virtualOf([a, b])
    FNeg(_, a) -> virtualOf([a])
    FCmp(a, b) -> virtualOf([a, b])
    Scvtf(_, a) -> virtualOf([a])
    Fcvtzs(_, a) -> virtualOf([a])
    Bl(_) -> []
    Blr(a) -> virtualOf([a])
    CallArgs(ins, _) -> virtualOf(ins)
    Ret -> []
    B(_) -> []
    BCond(_, _) -> []
    Brk(_) -> []
}

-- The registers an instruction *writes*, physical ones included.
--
-- `defsOf` is this filtered to the virtual ones, which is what liveness and
-- the verifier want. The allocator wants the unfiltered answer: a `mov x0, %5`
-- before a call writes a real `x0`, and any value the allocator has put in
-- `x0` and needs after that instruction has just been destroyed. Deriving both
-- from one match is the point -- a second sixty-armed `match` listing the same
-- destinations is exactly the kind of duplication that drifts.
fun destRegs(op : Arm) -> Array Reg = match op {
    Mov(d, _, _) -> [d]
    MovZ(d, _, _) -> [d]
    MovK(d, _, _) -> [d]
    Add(d, _, _, _) -> [d]
    AddImm(d, _, _, _) -> [d]
    AddS(d, _, _, _) -> [d]
    Sub(d, _, _, _) -> [d]
    SubImm(d, _, _, _) -> [d]
    SubS(d, _, _, _) -> [d]
    Mul(d, _, _, _) -> [d]
    SMulH(d, _, _) -> [d]
    SDiv(d, _, _, _) -> [d]
    MSub(d, _, _, _, _) -> [d]
    And(d, _, _, _) -> [d]
    Orr(d, _, _, _) -> [d]
    Eor(d, _, _, _) -> [d]
    EorImm(d, _, _, _) -> [d]
    Lsl(d, _, _, _) -> [d]
    Lsr(d, _, _, _) -> [d]
    Asr(d, _, _, _) -> [d]
    Cmp(_, _, _) -> []
    CmpImm(_, _, _) -> []
    CSet(d, _) -> [d]
    Sxtw(d, _) -> [d]
    Uxtb(d, _) -> [d]
    Uxth(d, _) -> [d]
    Ubfx(d, _, _, _) -> [d]
    Ldr(d, _, _) -> [d]
    Ldrb(d, _) -> [d]
    Ldrsw(d, _) -> [d]
    Str(_, _, _) -> []
    Strb(_, _) -> []
    Stp(_, _, _, _) -> []
    Ldp(a, b, _, _) -> [a, b]
    Adrp(d, _) -> [d]
    AddLo(d, _, _) -> [d]
    MovConst(d, _) -> [d]
    FMov(d, _) -> [d]
    FMovIn(d, _) -> [d]
    FMovOut(d, _) -> [d]
    FAdd(d, _, _) -> [d]
    FSub(d, _, _) -> [d]
    FMul(d, _, _) -> [d]
    FDiv(d, _, _) -> [d]
    FNeg(d, _) -> [d]
    -- `fcmp` writes the flags and no register, which is exactly why the guard
    -- that reads it has to stay adjacent.
    FCmp(_, _) -> []
    Scvtf(d, _) -> [d]
    Fcvtzs(d, _) -> [d]
    Bl(_) -> []
    Blr(_) -> []
    CallArgs(_, outs) -> outs
    Ret -> []
    B(_) -> []
    BCond(_, _) -> []
    Brk(_) -> []
}

fun defsOf(op : Arm) -> Array Value = virtualOf(destRegs(op))


fun virtualOf(rs : Array Reg) -> Array Value {
    let out = []
    for r in rs {
        match r {
            Virtual(n) -> Array.push(out, Value(n))
            Physical(_, _) -> {}
        }
    }
    out
}

fun memUses(m : Mem) -> Array Value = match m {
    Offset(base, _) -> virtualOf([base])
    Indexed(base, index) -> virtualOf([base, index])
}

-- -- printing -----------------------------------------------------------------------

-- The assembly the system assembler accepts, which is the point: `as`
-- assembles what this prints, so it is the oracle for the encoder that comes
-- after (`LINKER.md`). A printer that spelled something `as` would reject
-- would be a printer that could not check anything.
fun showReg(r : Reg, w : Width) -> String {
    let prefix = match w {
        W32 -> "w"
        W64 -> "x"
    }
    match r {
        Virtual(n) -> "%" + Int.toString(n)
        -- The bank decides the letter, not the width: a double is always
        -- `d`, and only the general file has a narrow spelling.
        Physical(Fp, n) -> "d" + Int.toString(n)
        Physical(Gp, 31) -> "xzr"
        Physical(Gp, n) -> if n == 29 { "x29" }
                           else if n == 30 { "x30" }
                           else { prefix + Int.toString(n) }
    }
}

fun condName(c : Cond) -> String = match c {
    Eq -> "eq"
    Ne -> "ne"
    Lt -> "lt"
    Le -> "le"
    Gt -> "gt"
    Ge -> "ge"
    Lo -> "lo"
    Ls -> "ls"
    Hi -> "hi"
    Hs -> "hs"
    Vs -> "vs"
}

instance Show Reg {
    fun show(r) = showReg(r, W64)
}

instance Show Mem {
    fun show(m) = match m {
        Offset(base, 0) -> "[" + baseName(base) + "]"
        Offset(base, n) -> "[" + baseName(base) + ", #" + Int.toString(n) + "]"
        Indexed(base, index) ->
            "[" + baseName(base) + ", " + showReg(index, W64) + "]"
    }
}

-- A base register is `sp` where a general register would be `xzr`: the same
-- encoding, read differently by the instruction, which is why `Reg` cannot
-- decide it alone.
fun baseName(r : Reg) -> String = match r {
    Physical(Gp, 31) -> "sp"
    other -> showReg(other, W64)
}

instance Show Inst {
    fun show(i) = showArm(i.op)
}

fun showArm(op : Arm) -> String = match op {
    Mov(d, a, w) -> "mov " + showReg(d, w) + ", " + showReg(a, w)
    MovZ(d, v, shift) -> "movz " + showReg(d, W64) + ", #" + Int.toString(v)
                         + (if shift == 0 { "" }
                            else { ", lsl #" + Int.toString(shift) })
    MovK(d, v, shift) -> "movk " + showReg(d, W64) + ", #" + Int.toString(v)
                         + (if shift == 0 { "" }
                            else { ", lsl #" + Int.toString(shift) })
    Add(d, a, b, w) -> three("add", d, a, b, w)
    AddImm(d, a, n, w) -> imm("add", d, a, n, w)
    AddS(d, a, b, w) -> three("adds", d, a, b, w)
    Sub(d, a, b, w) -> three("sub", d, a, b, w)
    SubImm(d, a, n, w) -> imm("sub", d, a, n, w)
    SubS(d, a, b, w) -> three("subs", d, a, b, w)
    Mul(d, a, b, w) -> three("mul", d, a, b, w)
    SMulH(d, a, b) -> three("smulh", d, a, b, W64)
    SDiv(d, a, b, w) -> three("sdiv", d, a, b, w)
    MSub(d, a, b, c, w) -> "msub " + showReg(d, w) + ", " + showReg(a, w) + ", "
                           + showReg(b, w) + ", " + showReg(c, w)
    And(d, a, b, w) -> three("and", d, a, b, w)
    Orr(d, a, b, w) -> three("orr", d, a, b, w)
    Eor(d, a, b, w) -> three("eor", d, a, b, w)
    EorImm(d, a, n, w) -> imm("eor", d, a, n, w)
    Lsl(d, a, b, w) -> three("lsl", d, a, b, w)
    Lsr(d, a, b, w) -> three("lsr", d, a, b, w)
    Asr(d, a, b, w) -> three("asr", d, a, b, w)
    Cmp(a, b, w) -> "cmp " + showReg(a, w) + ", " + showReg(b, w)
    CmpImm(a, n, w) -> "cmp " + showReg(a, w) + ", #" + Int.toString(n)
    CSet(d, c) -> "cset " + showReg(d, W64) + ", " + condName(c)
    Sxtw(d, a) -> "sxtw " + showReg(d, W64) + ", " + showReg(a, W32)
    Uxtb(d, a) -> "uxtb " + showReg(d, W32) + ", " + showReg(a, W32)
    Uxth(d, a) -> "uxth " + showReg(d, W32) + ", " + showReg(a, W32)
    Ubfx(d, a, lsb, width) -> "ubfx " + showReg(d, W64) + ", " + showReg(a, W64)
                              + ", #" + Int.toString(lsb) + ", #"
                              + Int.toString(width)
    Ldr(d, m, w) -> "ldr " + showReg(d, w) + ", " + show(m)
    Ldrb(d, m) -> "ldrb " + showReg(d, W32) + ", " + show(m)
    Ldrsw(d, m) -> "ldrsw " + showReg(d, W64) + ", " + show(m)
    Str(v, m, w) -> "str " + showReg(v, w) + ", " + show(m)
    Strb(v, m) -> "strb " + showReg(v, W32) + ", " + show(m)
    Stp(a, b, base, n) -> "stp " + showReg(a, W64) + ", " + showReg(b, W64)
                          + ", [" + baseName(base) + ", #" + Int.toString(n)
                          + "]"
    Ldp(a, b, base, n) -> "ldp " + showReg(a, W64) + ", " + showReg(b, W64)
                          + ", [" + baseName(base) + ", #" + Int.toString(n)
                          + "]"
    MovConst(d, n) -> showConst(d, n)
    -- A virtual register prints as `%n` whichever bank it is in, so these read
    -- the same as their integer counterparts until the allocator has run.
    FMov(d, a) -> "fmov " + fpName(d) + ", " + fpName(a)
    FMovIn(d, a) -> "fmov " + fpName(d) + ", " + showReg(a, W64)
    FMovOut(d, a) -> "fmov " + showReg(d, W64) + ", " + fpName(a)
    FAdd(d, a, b) -> fp3("fadd", d, a, b)
    FSub(d, a, b) -> fp3("fsub", d, a, b)
    FMul(d, a, b) -> fp3("fmul", d, a, b)
    FDiv(d, a, b) -> fp3("fdiv", d, a, b)
    FNeg(d, a) -> "fneg " + fpName(d) + ", " + fpName(a)
    FCmp(a, b) -> "fcmp " + fpName(a) + ", " + fpName(b)
    Scvtf(d, a) -> "scvtf " + fpName(d) + ", " + showReg(a, W64)
    Fcvtzs(d, a) -> "fcvtzs " + showReg(d, W64) + ", " + fpName(a)
    Adrp(d, sym) -> "adrp " + showReg(d, W64) + ", " + sym + "@PAGE"
    AddLo(d, a, sym) -> "add " + showReg(d, W64) + ", " + showReg(a, W64)
                        + ", " + sym + "@PAGEOFF"
    Bl(sym) -> "bl " + sym
    Blr(a) -> "blr " + showReg(a, W64)
    -- Printed as a comment: it has no encoding and exists to tell the register
    -- allocator what a call reads and writes.
    CallArgs(ins, outs) -> "; call uses " + Int.toString(len(ins))
                           + " defs " + Int.toString(len(outs))
    Ret -> "ret"
    B(target) -> "b " + blockLabel(target)
    BCond(c, target) -> "b." + condName(c) + " " + blockLabel(target)
    Brk(n) -> "brk #" + Int.toString(n)
}

fun three(name : String, d : Reg, a : Reg, b : Reg, w : Width) -> String =
    name + " " + showReg(d, w) + ", " + showReg(a, w) + ", " + showReg(b, w)

fun imm(name : String, d : Reg, a : Reg, n : Int, w : Width) -> String =
    name + " " + showReg(d, w) + ", " + showReg(a, w) + ", #" + Int.toString(n)

-- Local labels. `L` is the assembler's convention for a symbol that does not
-- reach the symbol table, which is what a basic block should be.
fun blockLabel(b : BlockId) -> String = "Lb" + Int.toString(Ssa.blockId(b))

-- A register in the vector file. The width is not a parameter because there is
-- only one: `W64` reaches `showReg`, which answers `d` for anything in `Fp`.
fun fpName(r : Reg) -> String = showReg(r, W64)

fun fp3(name : String, d : Reg, a : Reg, b : Reg) -> String =
    name + " " + fpName(d) + ", " + fpName(a) + ", " + fpName(b)

-- A constant, as the `movz` and `movk`s that build it.
--
-- Sixteen bits at a time, low half first, skipping the halves that are zero --
-- and emitting one `movz` even for zero, so that the sequence is never empty.
fun showConst(d : Reg, n : Int) -> String {
    let out = []
    var first = True
    for var shift = 0; shift < 64; shift = shift + 16 {
        let part = Int.and(Int.shr(n, shift), 65535)
        if part != 0 {
            let piece = Int.toString(part) + (if shift == 0 { "" }
                                              else { ", lsl #" + Int.toString(shift) })
            if first { Array.push(out, "movz " + showReg(d, W64) + ", #" + piece) }
            else { Array.push(out, "movk " + showReg(d, W64) + ", #" + piece) }
            first = False
        }
    }
    if first { return "movz " + showReg(d, W64) + ", #0" }
    String.join(out, "\n    ")
}
