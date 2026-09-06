module Turkey.Arm64 (
    Reg(..), reg, physical, isPhysical, Cond(..), Width(..),
    Mem(..), Arm(..), Inst(..),
    xzr, sp, fp, lr, x0, argRegs, callerSaved, calleeSaved,
    showReg, condName,
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
type Reg
    -- A value from selection. The number is an `Ssa.Value` id.
    = Virtual(Int)
    -- `x0`..`x30`, and 31 meaning `xzr` or `sp` depending on the instruction.
    | Physical(Int)

fun reg(v : Value) -> Reg = Virtual(Ssa.valueId(v))

fun physical(n : Int) -> Reg = Physical(n)

fun isPhysical(r : Reg) -> Bool = match r {
    Physical(_) -> True
    Virtual(_) -> False
}

let xzr = Physical(31)
let sp = Physical(31)
let fp = Physical(29)
let lr = Physical(30)
let x0 = Physical(0)

-- The first eight are the argument and result registers, which is the whole of
-- the calling convention this backend needs: nothing takes more than eight
-- arguments after closure conversion, because a closure's captures travel in
-- its environment rather than in registers.
let argRegs = [Physical(0), Physical(1), Physical(2), Physical(3),
               Physical(4), Physical(5), Physical(6), Physical(7)]

-- AAPCS64: x0-x17 are caller-saved, x19-x28 callee-saved. x18 is reserved by
-- the platform on Darwin and is not in either list.
let callerSaved = [Physical(0), Physical(1), Physical(2), Physical(3),
                   Physical(4), Physical(5), Physical(6), Physical(7),
                   Physical(8), Physical(9), Physical(10), Physical(11),
                   Physical(12), Physical(13), Physical(14), Physical(15),
                   Physical(16), Physical(17)]
let calleeSaved = [Physical(19), Physical(20), Physical(21), Physical(22),
                   Physical(23), Physical(24), Physical(25), Physical(26),
                   Physical(27), Physical(28)]

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
    Bl(_) -> []
    Blr(a) -> virtualOf([a])
    CallArgs(ins, _) -> virtualOf(ins)
    Ret -> []
    B(_) -> []
    BCond(_, _) -> []
    Brk(_) -> []
}

fun defsOf(op : Arm) -> Array Value = match op {
    Mov(d, _, _) -> virtualOf([d])
    MovZ(d, _, _) -> virtualOf([d])
    MovK(d, _, _) -> virtualOf([d])
    Add(d, _, _, _) -> virtualOf([d])
    AddImm(d, _, _, _) -> virtualOf([d])
    AddS(d, _, _, _) -> virtualOf([d])
    Sub(d, _, _, _) -> virtualOf([d])
    SubImm(d, _, _, _) -> virtualOf([d])
    SubS(d, _, _, _) -> virtualOf([d])
    Mul(d, _, _, _) -> virtualOf([d])
    SMulH(d, _, _) -> virtualOf([d])
    SDiv(d, _, _, _) -> virtualOf([d])
    MSub(d, _, _, _, _) -> virtualOf([d])
    And(d, _, _, _) -> virtualOf([d])
    Orr(d, _, _, _) -> virtualOf([d])
    Eor(d, _, _, _) -> virtualOf([d])
    EorImm(d, _, _, _) -> virtualOf([d])
    Lsl(d, _, _, _) -> virtualOf([d])
    Lsr(d, _, _, _) -> virtualOf([d])
    Asr(d, _, _, _) -> virtualOf([d])
    Cmp(_, _, _) -> []
    CmpImm(_, _, _) -> []
    CSet(d, _) -> virtualOf([d])
    Sxtw(d, _) -> virtualOf([d])
    Uxtb(d, _) -> virtualOf([d])
    Uxth(d, _) -> virtualOf([d])
    Ubfx(d, _, _, _) -> virtualOf([d])
    Ldr(d, _, _) -> virtualOf([d])
    Ldrb(d, _) -> virtualOf([d])
    Ldrsw(d, _) -> virtualOf([d])
    Str(_, _, _) -> []
    Strb(_, _) -> []
    Stp(_, _, _, _) -> []
    Ldp(a, b, _, _) -> virtualOf([a, b])
    Adrp(d, _) -> virtualOf([d])
    AddLo(d, _, _) -> virtualOf([d])
    Bl(_) -> []
    Blr(_) -> []
    CallArgs(_, outs) -> virtualOf(outs)
    Ret -> []
    B(_) -> []
    BCond(_, _) -> []
    Brk(_) -> []
}

fun virtualOf(rs : Array Reg) -> Array Value {
    let out = []
    for r in rs {
        match r {
            Virtual(n) -> Array.push(out, Value(n))
            Physical(_) -> {}
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
        Physical(31) -> "xzr"
        Physical(n) -> if n == 29 { "x29" }
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
    Physical(31) -> "sp"
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
