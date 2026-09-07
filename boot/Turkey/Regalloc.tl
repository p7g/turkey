module Turkey.Regalloc (
    Pressure(..), pressureOf, effectivePressure,
    AcrossCalls(..), acrossCalls,
    availableGp, availableFp, calleeSavedGp, calleeSavedFp,
    Colouring(..), colour, verifyColouring,
    untracedPressure, worstCall,
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

import Data.Map as Map
import Data.Set as Set
import Turkey.Arm64 as A
-- The bank constructors unqualified because patterns cannot be qualified,
-- which is the same reason `Turkey.Select` imports them this way.
import Turkey.Arm64 (Bank(..), Reg(..))
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


-- -- the colourer ------------------------------------------------------------

-- What the allocator answers: a physical register per value, and nothing
-- rewritten.
--
-- The assignment is kept **on the side** rather than substituted into the
-- instructions. That is regalloc2's design, and the reason it gives is the one
-- that applies here: Cranelift's emitter became "almost completely immutable"
-- once allocation stopped editing the code, and a pass that does not mutate
-- its input is a pass whose input can still be printed, verified and re-run.
type Colouring = Colouring {
    -- Value id -> register number within that value's own bank. `-1` for a
    -- value that was never assigned one, which after a successful run means
    -- only that it is dead.
    of_ : Array Int,
    -- Why it stopped, if it did. The discipline selection uses: a function
    -- that cannot be coloured is reported rather than approximated, and the
    -- histogram is the plan for the next slice.
    stopped : Option String,
}

-- Colour one function by walking its blocks in a dominance-compatible order.
--
-- Reverse postorder is dominance-compatible, so a definition is always
-- coloured before any use, and the theorem does the rest: at the point a value
-- is defined, everything it can interfere with is live there, so a colour that
-- avoids the live ones avoids all of them. No interference graph, no worklist,
-- no second pass.
--
-- Two things the theorem does not cover, and both are real here:
--
-- * **Physical registers.** Selection pins arguments into `x0`-`x7` and reads
--   the result out of `x0`, and a `mov x0, %5` destroys whatever the allocator
--   put in `x0`. So a value's forbidden set collects every physical register
--   written at a point where that value is live, and a call forbids all of the
--   caller-saved ones at once.
-- * **The flag pair.** Nothing may come between a flag-setting instruction and
--   the `cset` that reads it. This pass inserts nothing, so it cannot break
--   that -- and a spiller would, which is one more reason the measurement
--   saying none is needed was worth taking before writing one.
fun colour(f : Func A.Inst) -> Colouring {
    let (liveIn, liveOut) = Ssa.livenessInOut(f)
    let forbidden = forbiddenSets(f, liveOut)
    let of_ : Array Int = []
    for _ in f.reps { Array.push(of_, -1) }
    var stopped = None

    for at in Ssa.reversePostorder(f) {
        let b = Ssa.blockId(at)
        let block = f.blocks[b]
        -- A block's parameters are defined at its entry and are all live at
        -- once, so each avoids the values entering the block and the
        -- parameters already placed.
        let taken = Set.new()
        for id in Set.elements(liveIn[b]) {
            if of_[id] >= 0 { Set.add(taken, key(f, id, of_[id])) }
        }
        for p in block.params {
            let why = assign(f, of_, forbidden, taken, Ssa.valueId(p))
            match (why, stopped) {
                (Some(w), None) -> { stopped = Some(w) }
                _ -> {}
            }
        }
        let after = Ssa.liveAfter(f, at, liveOut[b])
        for var n = 0; n < len(block.insts); n = n + 1 {
            -- Interference is with what is live *after* the instruction, not
            -- before it: an operand that dies here may share the destination's
            -- register, which is what makes `add x0, x0, x1` legal.
            let live = Set.new()
            for id in Set.elements(after[n]) {
                if of_[id] >= 0 { Set.add(live, key(f, id, of_[id])) }
            }
            for d in Ssa.defs(block.insts[n]) {
                let why = assign(f, of_, forbidden, live, Ssa.valueId(d))
                match (why, stopped) {
                    (Some(w), None) -> { stopped = Some(w) }
                    _ -> {}
                }
            }
        }
    }
    Colouring { of_ = of_, stopped = stopped }
}

-- A taken-register key. The two files are allocated apart, so `x3` and `d3`
-- must not collide in one set; the bank is part of the key rather than the set
-- being split in two, which would double every loop above.
fun key(f : Func A.Inst, id : Int, n : Int) -> String =
    match A.bankOf(f.reps[id]) {
        Gp -> "x" + Int.toString(n)
        Fp -> "d" + Int.toString(n)
    }

-- Give one value the lowest register its bank allows that nothing live holds
-- and nothing forbids. Answers why it could not, if it could not.
fun assign(f : Func A.Inst, of_ : Array Int, forbidden : Array (Set.Set Int),
           taken : Set.Set String, id : Int) -> Option String {
    if of_[id] >= 0 { return None }
    let bank = A.bankOf(f.reps[id])
    let pool = match bank {
        Gp -> gpPool
        Fp -> fpPool
    }
    for n in pool {
        let k = match bank {
            Gp -> "x" + Int.toString(n)
            Fp -> "d" + Int.toString(n)
        }
        let free = !Set.contains(taken, k)
        if free {
            if !Set.contains(forbidden[id], n) {
                of_[id] = n
                Set.add(taken, k)
                return None
            }
        }
    }
    let file = match bank {
        Gp -> "general"
        Fp -> "vector"
    }
    Some("a value with no free register in the " + file + " file")
}

-- The allocatable registers, in the order they are handed out.
--
-- Caller-saved first in the general file, because most values are not live
-- across a call and a callee-saved register costs a save and a restore in the
-- prologue. `x18` is reserved on Darwin, `x29` and `x30` are the frame and
-- link registers, and `x31` is `sp` or `xzr`; none of the four is here.
let gpPool = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17,
              19, 20, 21, 22, 23, 24, 25, 26, 27, 28]
-- The vector file's callee-saved half is `d8`-`d15`, so the caller-saved ones
-- go first here too -- from both ends, since `v0`-`v7` and `v16`-`v31` are the
-- caller-saved halves.
let fpPool = [0, 1, 2, 3, 4, 5, 6, 7, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
              26, 27, 28, 29, 30, 31, 8, 9, 10, 11, 12, 13, 14, 15]

-- For each value, the register numbers it may not take.
--
-- One sweep: at every instruction, whatever physical registers it writes are
-- denied to every value live *across* it, and a call writes all of the
-- caller-saved ones. A value never live across such a point collects an empty
-- set and takes the first register in the pool.
fun forbiddenSets(f : Func A.Inst,
                  liveOut : Array (Set.Set Int)) -> Array (Set.Set Int) {
    let out : Array (Set.Set Int) = []
    for _ in f.reps { Array.push(out, Set.new()) }
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let block = f.blocks[b]
        let after = Ssa.liveAfter(f, Ssa.BlockId(b), liveOut[b])
        for var n = 0; n < len(block.insts); n = n + 1 {
            let clobbered = clobbersOf(block.insts[n])
            if len(clobbered) > 0 {
                for id in Set.elements(after[n]) {
                    let bank = A.bankOf(f.reps[id])
                    for (cb, cn) in clobbered {
                        if sameBank(bank, cb) { Set.add(out[id], cn) }
                    }
                }
            }
        }
    }
    out
}

fun sameBank(a : Bank, b : Bank) -> Bool = match (a, b) {
    (Gp, Gp) -> True
    (Fp, Fp) -> True
    _ -> False
}

-- The physical registers an instruction destroys: the ones it names as a
-- destination, plus -- if it is a call -- every caller-saved register in both
-- files, which the callee is free to use.
fun clobbersOf(i : A.Inst) -> Array (Bank, Int) {
    let out : Array (Bank, Int) = []
    for r in A.destRegs(i.op) { pushPhysical(out, r) }
    if A.isCall(i) {
        for r in A.callerSaved { pushPhysical(out, r) }
        for r in A.fpCallerSaved { pushPhysical(out, r) }
    }
    out
}

fun pushPhysical(out : Array (Bank, Int), r : Reg) -> Unit = match r {
    Physical(bank, n) -> Array.push(out, (bank, n))
    Virtual(_) -> {}
}


-- Whether a colouring is actually a colouring.
--
-- `Ssa.verify` checks the graph and knows nothing about registers, so this is
-- the allocator's half of the same idea: the pass that can silently produce
-- code which assembles, links, runs and computes the wrong answer gets a
-- checker rather than a hope. Two things are wrong and both are invisible
-- downstream:
--
-- * **Two values live at one point sharing a register.** The colouring is
--   worthless if this happens, and nothing later can tell -- the printer will
--   print it, `as` will assemble it, and the program will compute a wrong
--   number.
-- * **A value holding a register that is clobbered while it is live.** The
--   classic form is a value in `x0` across a call, which the callee destroys.
--
-- Both are quadratic in the live set at worst and the live sets are small,
-- which is what makes checking every program point affordable.
fun verifyColouring(f : Func A.Inst, c : Colouring) -> Array String {
    let out : Array String = []
    let (_, liveOut) = Ssa.livenessInOut(f)
    let forbidden = forbiddenSets(f, liveOut)
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let block = f.blocks[b]
        let after = Ssa.liveAfter(f, Ssa.BlockId(b), liveOut[b])
        for var n = 0; n < len(block.insts); n = n + 1 {
            checkPoint(out, f, c, forbidden, after[n], b, n)
        }
        checkPoint(out, f, c, forbidden, liveOut[b], b, -1)
    }
    out
}

fun checkPoint(out : Array String, f : Func A.Inst, c : Colouring,
               forbidden : Array (Set.Set Int), live : Set.Set Int,
               b : Int, n : Int) -> Unit {
    let seen = Map.new()
    for id in Set.elements(live) {
        let r = c.of_[id]
        if r >= 0 {
            let k = key(f, id, r)
            match Map.get(seen, k) {
                Some(other) -> Array.push(out,
                    "values %" + Int.toString(other) + " and %"
                    + Int.toString(id) + " both hold " + k + " at "
                    + where_(b, n))
                None -> Map.put(seen, k, id)
            }
            if Set.contains(forbidden[id], r) {
                Array.push(out, "value %" + Int.toString(id) + " holds " + k
                                + ", which is clobbered while it is live, at "
                                + where_(b, n))
            }
        }
    }
}

fun where_(b : Int, n : Int) -> String =
    if n < 0 { "the end of block b" + Int.toString(b) }
    else { "b" + Int.toString(b) + " instruction " + Int.toString(n) }


-- Pressure counting only the values that have nowhere free to go.
--
-- An untraced value has no root slot, so spilling one needs a real stack slot
-- and therefore a frame -- which does not exist yet. This number is the answer
-- to "what if there are too many untraced values": if it fits in the file, the
-- traced fallback is enough and nothing needs a frame; if it does not, a real
-- spiller does, and this says by how much.
fun untracedPressure(f : Func A.Inst) -> Pressure {
    let liveOut = Ssa.liveness(f)
    let traced = Set.new()
    for var id = 0; id < len(f.reps); id = id + 1 {
        if f.reps[id].traced { Set.add(traced, id) }
    }
    var gp = 0
    var fp = 0
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let at = Ssa.BlockId(b)
        let (og, of_) = countExcept(f, liveOut[b], traced)
        if og > gp { gp = og }
        if of_ > fp { fp = of_ }
        for live in Ssa.liveAfter(f, at, liveOut[b]) {
            let (ig, if_) = countExcept(f, live, traced)
            if ig > gp { gp = ig }
            if if_ > fp { fp = if_ }
        }
    }
    Pressure { gp = gp, fp = fp }
}

-- The most values live across any single call, traced and untraced together.
--
-- `acrossCalls` reports the two categories' maxima independently, which can be
-- at different call sites and so understates the worst one. This is the number
-- the callee-saved budget is actually spent against.
fun worstCall(f : Func A.Inst) -> Int {
    let liveOut = Ssa.liveness(f)
    var worst = 0
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let block = f.blocks[b]
        let after = Ssa.liveAfter(f, Ssa.BlockId(b), liveOut[b])
        for var n = 0; n < len(block.insts); n = n + 1 {
            if A.isCall(block.insts[n]) {
                var here = 0
                for id in Set.elements(after[n]) {
                    match A.bankOf(f.reps[id]) {
                        Gp -> { here = here + 1 }
                        Fp -> {}
                    }
                }
                if here > worst { worst = here }
            }
        }
    }
    worst
}
