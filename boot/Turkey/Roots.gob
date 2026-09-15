module Turkey.Roots (
    Frame(..), analyze, slotCount, liveAt, isSafepoint,
)

-- Which values the collector must be able to find, and where each one lives
-- (`NATIVE-BACKEND.md`, M28 phase 3.5).
--
-- **An analysis, not a pass, and that is the whole design decision.** The
-- obvious move after FINDINGS 70 was an IR pass that *materializes* root
-- frames, so that both emitters became dumb. Two things argued it down.
--
-- The materialization is not the part that can disagree. Storing a pointer
-- into an array and writing a mask is five lines in any backend, and the two
-- backends would write the same five lines whether or not they shared them.
-- What *can* disagree -- silently, rarely, and only under collection -- is
-- **which values are live across which safepoint**, and that is this module.
-- Extracting the analysis removes the hazard; extracting the emission removes
-- duplication that was never dangerous.
--
-- And a pass would have cost the IR. Materializing roots needs the address of
-- a stack slot, which is an opcode the IR does not have and would gain for one
-- caller. The analysis needs nothing: it reads a `Func Low` and answers a
-- table.
--
-- The same reasoning is why the overflow and division checks stay in each
-- emitter. LLVM wants `llvm.sadd.with.overflow`, arm64 wants `adds` and a
-- flag register the IR cannot name, and there is no shared *rule* between
-- them -- only a shared intention, which is not the thing that goes wrong.
-- Two implementations of one rule is the hazard; two encodings of one check
-- is the job.

import Data.Map as Map
import Data.Set as Set
import Turkey.LowIr as L
import Turkey.LowIr (Op(..), Low(..), Callee(..))
import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..), Func(..))

-- One function's root frame.
type Frame = Frame {
    -- Value id -> its slot. Only the traced values live across some safepoint
    -- are in it.
    slots : Map Int Int,
    -- (block, instruction) -> the value ids to publish there, sorted. Keyed on
    -- a string because a pair is not hashable here; the key is private to this
    -- module and `liveAt` is the only reader.
    at    : Map String (Array Int),
}

fun key(b : Int, n : Int) -> String = Int.toString(b) + ":" + Int.toString(n)

fun slotCount(frame : Frame) -> Int = len(frame.slots)

-- The (slot, value) pairs to publish before the instruction at this position,
-- and nothing if it is not a safepoint.
fun liveAt(frame : Frame, b : Int, n : Int) -> Array (Int, Int) {
    let out = []
    for id in Map.getOr(frame.at, key(b, n), []) {
        match Map.get(frame.slots, id) {
            Some(slot) -> Array.push(out, (slot, id))
            None -> {}
        }
    }
    out
}

-- The whole analysis, once per function.
--
-- Liveness is computed once and every safepoint's set is read out of it, which
-- is what keeps a root set a property of a *program point* rather than of a
-- function -- the distinction `RootFrame.live` exists for, and the one that
-- stops a cold path deciding what a hot one pays (FINDINGS 55).
fun analyze(f : Func Low) -> Frame {
    let live = Ssa.liveness(f)
    let needed = Set.new()
    let at = Map.new()
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let after = Ssa.liveAfter(f, BlockId(b), live[b])
        let insts = Ssa.blockAt(f, BlockId(b)).insts
        for var n = 0; n < len(insts); n = n + 1 {
            if isSafepoint(insts[n].op) {
                let here = Array.sort(Set.elements(across(f, after[n], insts[n])))
                for id in here { Set.add(needed, id) }
                Map.put(at, key(b, n), here)
            }
        }
    }
    -- Sorted, so the slot a value gets does not depend on a set's iteration
    -- order and two runs produce the same output.
    let slots = Map.new()
    let ids = Array.sort(Set.elements(needed))
    for var i = 0; i < len(ids); i = i + 1 { Map.put(slots, ids[i], i) }
    Frame { slots = slots, at = at }
}

-- The traced values live *across* a safepoint, which is not the same as those
-- live after it.
--
-- An argument whose last use is this very call is dead afterwards and is still
-- needed *during* it: the callee holds it, and the collector may run while it
-- does. Rooting only what survives the call freed `Int.toString`'s result
-- while `turkey_string_concat` was reading it, and "circle of 5" came out
-- "circle of c".
--
-- The result goes the other way and must *not* be rooted: it does not exist
-- until the collection is over, so storing it would be a use before its
-- definition -- not merely useless but not SSA, which clang answers with a
-- segfault rather than a diagnosis.
--
-- Both halves are stated in `turkey/llvmgen._safepoint_live`'s comment, which
-- is where this should have been read from rather than reasoned out
-- (FINDINGS 69).
fun across(f : Func Low, live : Set.Set Int, i : Low) -> Set.Set Int {
    let out = Set.new()
    for id in Set.elements(live) {
        if id != Ssa.valueId(i.result) && traced(f, id) { Set.add(out, id) }
    }
    for v in Ssa.uses(i) {
        let id = Ssa.valueId(v)
        if id != Ssa.valueId(i.result) && traced(f, id) { Set.add(out, id) }
    }
    out
}

fun traced(f : Func Low, id : Int) -> Bool =
    if id < 0 || id >= len(f.reps) { False } else { f.reps[id].traced }

-- An operation the collector may run inside.
--
-- Every call, and every allocation. Being wrong in the *inclusive* direction
-- costs a store and a mask write; being wrong the other way frees a live
-- object under the program's feet, so a call that cannot itself allocate is
-- listed anyway -- it may call one that can.
--
-- Matched exhaustively rather than defaulted, because the question this asks
-- of a new opcode -- can the collector run inside it -- is one whose wrong
-- answer is silent, and a `_ ->` arm would answer it by omission.
fun isSafepoint(op : Op) -> Bool = match op {
    Call(_, _) -> True
    ObjectNew(_, _, _, _) -> True
    ArrayNew(_, _, _) -> True
    CellNew(_) -> True
    ClosureNew(_, _, _) -> True
    ClosureCapture(_, _, _) -> True
    Box(_) -> True
    Unbox(_) -> True
    -- A string literal is interned by the runtime rather than allocated per
    -- evaluation, which is what makes it not a safepoint and what makes
    -- `ConstString` a load in the emitters.
    ConstString(_) -> False
    ConstInt(_) -> False
    ConstFloat(_) -> False
    ConstUnit -> False
    Bin(_, _, _) -> False
    Cmp(_, _, _) -> False
    Neg(_) -> False
    Not(_) -> False
    Convert(_, _) -> False
    ObjectGet(_, _) -> False
    ObjectSet(_, _, _) -> False
    ObjectTag(_) -> False
    ArrayGet(_, _) -> False
    ArraySet(_, _, _) -> False
    ArrayLength(_) -> False
    CellLoad(_) -> False
    CellStore(_, _) -> False
    GlobalLoad(_) -> False
    GlobalStore(_, _) -> False
    SlotLoad(_) -> False
    SlotStore(_, _) -> False
}
