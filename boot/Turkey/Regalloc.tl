module Turkey.Regalloc (
    Pressure(..), pressureOf, effectivePressure,
    AcrossCalls(..), acrossCalls,
    availableGp, availableFp, calleeSavedGp, calleeSavedFp,
)

-- Register allocation for arm64 (`NATIVE-BACKEND.md`, M28 phase 5).
--
-- The design is spill, then colour, then coalesce -- the SSA decomposition
-- rather than linear scan or IRC. What makes it worth the three phases is one
-- theorem: the dominance relation of an SSA program induces a perfect
-- elimination order on its interference graph, so the graph never has to be
-- built, colouring is a walk of the dominator tree, and it *cannot fail* once
-- pressure fits. Chordal graphs have a clique number equal to their chromatic
-- number, which in this setting reads: the registers needed are exactly the
-- maximum number of values live at once.
--
-- That last sentence is why this file begins with a measurement instead of a
-- spiller. If maximum pressure never exceeds the registers arm64 has, then the
-- corpus needs no spilling at all, and the first working allocator is the
-- colourer alone. Spilling is the largest and least certain part of this pass
-- and the only part whose heuristic affects code quality, so knowing whether
-- the corpus needs it is worth more than starting it.
--
-- The precondition the theorem needs is one this project already checks on
-- every function: `Ssa.verify` enforces that a definition dominates its uses.

import Data.Set as Set
import Turkey.Arm64 as A
-- The bank constructors unqualified because patterns cannot be qualified,
-- which is the same reason `Turkey.Select` imports them this way.
import Turkey.Arm64 (Bank(..))
import Turkey.Ssa as Ssa
import Turkey.Ssa (Func, BlockId, Value)

-- How many registers are live at once, at the worst point in a function, in
-- each file. The two are counted apart because they are allocated apart:
-- AAPCS64 numbers them separately and a double can never occupy an `x`.
type Pressure = Pressure { gp : Int, fp : Int }

-- What is actually assignable, which is not the same as what exists.
--
-- The general file has 31 numbered registers. `x31` is `sp` or `xzr`
-- depending on the instruction and is never allocatable; `x29` is the frame
-- pointer and `x30` the link register, both of which the prologue owns; and
-- `x18` is reserved by the platform on Darwin. That leaves x0-x17 and
-- x19-x28.
let availableGp = 28
-- The vector file has 32 and this backend reserves none, because every double
-- it makes is 64 bits and nothing vectorizes yet.
let availableFp = 32

-- What survives a call, which is a much smaller set than what exists. A value
-- live across a `bl` and held in a caller-saved register is destroyed by the
-- callee, so it needs one of these or a spill slot -- and there are ten.
let calleeSavedGp = 10
let calleeSavedFp = 8

-- The maximum number of values simultaneously live, per file.
--
-- Measured at two kinds of program point: after each instruction of a block,
-- which is where a definition has just added to the pressure, and at each
-- block's live-out, which is where a value crossing an edge does. Those are
-- the peaks; a point inside an instruction cannot exceed both of its
-- neighbours.
--
-- Only *virtual* registers are counted. The physical registers selection pins
-- around a call -- the arguments in `x0`-`x7`, the result out of `x0` -- are
-- not `Ssa.Value`s and so are not in any live set. They are a real constraint
-- on the colourer and they are not pressure in this sense: their live ranges
-- are the two instructions either side of the `bl`, and what this number is
-- for is deciding whether a spiller is needed.
fun pressureOf(f : Func A.Inst) -> Pressure {
    var gp = 0
    var fp = 0
    let liveOut = Ssa.liveness(f)
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let at = Ssa.BlockId(b)
        let (og, of_) = countOf(f, liveOut[b])
        if og > gp { gp = og }
        if of_ > fp { fp = of_ }
        for live in Ssa.liveAfter(f, at, liveOut[b]) {
            let (ig, if_) = countOf(f, live)
            if ig > gp { gp = ig }
            if if_ > fp { fp = if_ }
        }
    }
    Pressure { gp = gp, fp = fp }
}

-- A live set split by register file. `Ssa.liveness` answers value ids, and
-- `f.reps` is what says which file each belongs in -- the same table the low
-- IR used, because selection copies it rather than deriving a second one.
fun countOf(f : Func A.Inst, live : Set.Set Int) -> (Int, Int) {
    var gp = 0
    var fp = 0
    for id in Set.elements(live) {
        match A.bankOf(f.reps[id]) {
            Gp -> { gp = gp + 1 }
            Fp -> { fp = fp + 1 }
        }
    }
    (gp, fp)
}


-- How many values are live *across* a call, which is the second number the
-- colourer's feasibility turns on and a tighter one than total pressure.
--
-- A call clobbers every caller-saved register, so a value live across one can
-- be held only in a callee-saved register -- of which the general file has ten
-- and the vector file eight -- or in memory. Total pressure can fit
-- comfortably in 28 registers and this can still not fit in 10.
--
-- Traced and untraced are counted apart because they are not equally
-- expensive to spill. A traced value live across a safepoint is *already*
-- being written to the root array, because that is what rooting is; spilling
-- it costs the reload and not the store. An untraced one has no such slot and
-- pays for both. So the untraced count is the one that says whether a spiller
-- is needed, and the traced count says how much cheaper the spilling is than
-- it looks.
type AcrossCalls = AcrossCalls {
    untracedGp : Int, tracedGp : Int, untracedFp : Int, tracedFp : Int,
    calls : Int,
}

fun acrossCalls(f : Func A.Inst) -> AcrossCalls {
    var ug = 0
    var tg = 0
    var uf = 0
    var tf = 0
    var calls = 0
    let liveOut = Ssa.liveness(f)
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let block = f.blocks[b]
        let after = Ssa.liveAfter(f, Ssa.BlockId(b), liveOut[b])
        for var n = 0; n < len(block.insts); n = n + 1 {
            if A.isCall(block.insts[n]) {
                calls = calls + 1
                var thisUg = 0
                var thisTg = 0
                var thisUf = 0
                var thisTf = 0
                for id in Set.elements(after[n]) {
                    let rep = f.reps[id]
                    match A.bankOf(rep) {
                        Gp -> if rep.traced { thisTg = thisTg + 1 }
                              else { thisUg = thisUg + 1 }
                        Fp -> if rep.traced { thisTf = thisTf + 1 }
                              else { thisUf = thisUf + 1 }
                    }
                }
                if thisUg > ug { ug = thisUg }
                if thisTg > tg { tg = thisTg }
                if thisUf > uf { uf = thisUf }
                if thisTf > tf { tf = thisTf }
            }
        }
    }
    AcrossCalls { untracedGp = ug, tracedGp = tg, untracedFp = uf,
                  tracedFp = tf, calls = calls }
}


-- Pressure once the root array is counted as the spill slot it already is.
--
-- `Turkey.Roots` gives a stack slot to every traced value live across a
-- safepoint, and the emitter *already* stores it there before the call --
-- that store is what rooting is. So such a value need not hold a register
-- across the call at all: it can be read back from its slot afterwards, at
-- the cost of a load and no store. Its live range is split at every call, and
-- between calls it is an ordinary value.
--
-- This is not a spiller. It is the observation that the spilling has already
-- happened, for a different reason, and that the allocator may as well use it.
-- The measurement it produces is the one the colourer's feasibility rests on:
-- if effective pressure fits in the register file, no spiller is needed.
--
-- Approximated deliberately, and in the safe direction. A value that is live
-- across *some* call is excluded from every live set here, not only from the
-- ones spanning a call, so this under-counts the registers such a value uses
-- between calls. What it cannot do is under-count a function that needs
-- spilling for some other reason, because every value not in that set is
-- counted in full. Read it as "is the colourer plausible", not as a
-- register count.
fun effectivePressure(f : Func A.Inst) -> Pressure {
    let liveOut = Ssa.liveness(f)
    -- The traced values live across some call: exactly what `Turkey.Roots`
    -- gives a slot to, recomputed here on the machine graph because selection
    -- keeps the value numbering and this module cannot see a `Func Low`.
    let rooted = Set.new()
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let block = f.blocks[b]
        let after = Ssa.liveAfter(f, Ssa.BlockId(b), liveOut[b])
        for var n = 0; n < len(block.insts); n = n + 1 {
            if A.isCall(block.insts[n]) {
                for id in Set.elements(after[n]) {
                    if f.reps[id].traced { Set.add(rooted, id) }
                }
            }
        }
    }
    var gp = 0
    var fp = 0
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let at = Ssa.BlockId(b)
        let (og, of_) = countExcept(f, liveOut[b], rooted)
        if og > gp { gp = og }
        if of_ > fp { fp = of_ }
        for live in Ssa.liveAfter(f, at, liveOut[b]) {
            let (ig, if_) = countExcept(f, live, rooted)
            if ig > gp { gp = ig }
            if if_ > fp { fp = if_ }
        }
    }
    Pressure { gp = gp, fp = fp }
}

fun countExcept(f : Func A.Inst, live : Set.Set Int,
                skip : Set.Set Int) -> (Int, Int) {
    var gp = 0
    var fp = 0
    for id in Set.elements(live) {
        if !Set.contains(skip, id) {
            match A.bankOf(f.reps[id]) {
                Gp -> { gp = gp + 1 }
                Fp -> { fp = fp + 1 }
            }
        }
    }
    (gp, fp)
}
