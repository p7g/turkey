module Turkey.Promote (run)

-- Promote compiler-private local slots before either emitter. Backward slot
-- liveness adds block parameters only where a slot is needed on entry. Stores
-- define its current SSA value; loads become aliases. Branch edges carrying
-- values are split because Branch itself has no argument arrays.
import Turkey.Diag as Diag
import Data.Map as Map
import Data.Set as Set
import Turkey.LowIr as L
import Turkey.LowIr (Op(..), Low(..))
import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Term(..), Rep(..))

fun run(input : Ssa.Func Low) -> Ssa.Func Low {
    let f = Ssa.pruneUnreachable(input)
    let reps = Map.new()
    let needed : Array (Set.Set Int) = []
    let written : Array (Set.Set Int) = []
    for block in f.blocks {
        let use = Set.new()
        let defs = Set.new()
        for inst in block.insts {
            match inst.op {
                SlotLoad(slot) -> {
                    if !Set.contains(defs, slot) { Set.add(use, slot) }
                    Map.put(reps, slot, Ssa.repOf(f, inst.result))
                }
                SlotStore(slot, v) -> {
                    Set.add(defs, slot)
                    Map.put(reps, slot, Ssa.repOf(f, v))
                }
                _ -> {}
            }
        }
        Array.push(needed, use)
        Array.push(written, defs)
    }
    if len(reps) == 0 { return f }
    var changed = True
    while changed {
        changed = False
        for var b = len(f.blocks) - 1; b >= 0; b = b - 1 {
            for target in Ssa.successors(f.blocks[b].term) {
                for slot in needed[Ssa.blockId(target)] {
                    if !Set.contains(written[b], slot) && !Set.contains(needed[b], slot) {
                        Set.add(needed[b], slot)
                        changed = True
                    }
                }
            }
        }
    }
    if len(needed[Ssa.blockId(f.entry)]) > 0 {
        panic("slot promotion: uninitialized local in " + f.name)
    }
    let slots = Array.map(needed, fun(s) = Set.elements(s))
    let incoming : Array (Map Int Value) = []
    let original : Array Int = []
    for var b = 0; b < len(f.blocks); b = b + 1 {
        let values = Map.new()
        Array.push(original, len(f.blocks[b].params))
        for slot in slots[b] {
            let r = match Map.get(reps, slot) {
                Some(r) -> r
                None -> panic("slot promotion: missing representation")
            }
            let v = Ssa.freshValue(f, r)
            Array.push(f.blocks[b].params, v)
            Map.put(values, slot, v)
        }
        Array.push(incoming, values)
    }
    let aliases = Map.new()
    let outgoing : Array (Map Int Value) = []
    let count = len(f.blocks)
    for var b = 0; b < count; b = b + 1 {
        let values = incoming[b]
        let kept = []
        for inst in f.blocks[b].insts {
            match inst.op {
                SlotLoad(slot) -> Map.put(aliases, Ssa.valueId(inst.result), get(values, slot))
                SlotStore(slot, v) -> Map.put(values, slot, v)
                _ -> Array.push(kept, inst)
            }
        }
        f.blocks[b].insts = kept
        Array.push(outgoing, values)
    }
    for var b = 0; b < count; b = b + 1 {
        let block = f.blocks[b]
        block.term = match block.term {
            Jump(target, args) -> Jump(target, Array.append(args,
                edgeArgs(slots, outgoing[b], target)))
            Branch(c, yes, no) -> Branch(c,
                edge(f, yes, edgeArgs(slots, outgoing[b], yes)),
                edge(f, no, edgeArgs(slots, outgoing[b], no)))
            other -> other
        }
    }
    -- Most live-in parameters simply carry one dominating value across an
    -- edge. Remove those, including loop parameters whose only other input
    -- is themselves, so native selection need not pay for redundant copies.
    let preds = Ssa.predecessors(f)
    changed = True
    while changed {
        changed = False
        for var b = 0; b < count; b = b + 1 {
            for var i = original[b]; i < len(f.blocks[b].params); i = i + 1 {
                let p = f.blocks[b].params[i]
                if Map.contains(aliases, Ssa.valueId(p)) { continue }
                var unique : Option Value = None
                var multiple = False
                for pred in preds[b] {
                    match Ssa.blockAt(f, pred).term {
                        Jump(_, args) -> {
                            let v = resolve(aliases, args[i])
                            if Ssa.valueId(v) == Ssa.valueId(p) { continue }
                            match unique {
                                None -> { unique = Some(v) }
                                Some(other) -> if Ssa.valueId(v) != Ssa.valueId(other) {
                                    multiple = True
                                }
                            }
                        }
                        _ -> panic("slot promotion: unsplit parameter edge")
                    }
                }
                match unique {
                    Some(v) -> if !multiple {
                        Map.put(aliases, Ssa.valueId(p), v)
                        changed = True
                    }
                    None -> {}
                }
            }
        }
    }
    let keep = Array.map(f.blocks, fun(block) = Array.map(block.params,
        fun(v) = !Map.contains(aliases, Ssa.valueId(v))))
    for block in f.blocks {
        block.params = Array.filter(block.params,
            fun(v) = !Map.contains(aliases, Ssa.valueId(v)))
        for inst in block.insts {
            inst.op = L.mapUses(inst.op, fun(v) = resolve(aliases, v))
        }
        block.term = match block.term {
            Ret(v) -> Ret(resolve(aliases, v))
            Panic(v) -> Panic(resolve(aliases, v))
            Branch(c, yes, no) -> Branch(resolve(aliases, c), yes, no)
            Jump(target, args) -> {
                let kept = []
                for var i = 0; i < len(args); i = i + 1 {
                    if keep[Ssa.blockId(target)][i] {
                        Array.push(kept, resolve(aliases, args[i]))
                    }
                }
                Jump(target, kept)
            }
            Unreachable -> Unreachable
        }
    }
    Ssa.pruneUnreachable(f)
}

fun get(values : Map Int Value, slot : Int) -> Value = match Map.get(values, slot) {
    Some(v) -> v
    None -> panic("slot promotion: missing incoming value")
}

fun resolve(aliases : Map Int Value, value : Value) -> Value {
    var v = value
    while True {
        match Map.get(aliases, Ssa.valueId(v)) {
            Some(next) -> { v = next }
            None -> return v
        }
    }
    v
}

fun edgeArgs(slots : Array (Array Int), values : Map Int Value,
             target : BlockId) -> Array Value =
    Array.map(slots[Ssa.blockId(target)], fun(slot) = get(values, slot))

fun edge(f : Ssa.Func Low, target : BlockId, args : Array Value) -> BlockId {
    if len(args) == 0 { return target }
    let b : Ssa.Block Low = Ssa.newBlock()
    b.term = Jump(target, args)
    let id = BlockId(len(f.blocks))
    Array.push(f.blocks, b)
    id
}

fun panic(message : String) -> a =
    Diag.fail(Diag.whole("internal error", message), "")
