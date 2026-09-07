module Turkey.Regalloc (
    Pressure(..), pressureOf, availableGp, availableFp,
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
