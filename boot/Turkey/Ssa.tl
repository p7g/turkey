module Turkey.Ssa (
    Value(..), valueId, BlockId(..), blockId,
    RepClass(..), Rep(..), traced, untraced,
    Effects(..), reads_, writes, allocates, traps, diverges,
    isSafepoint, deletable,
    Term(..), successors,
    Block(..), newBlock, Func(..), newFunc,
    Inst(..),
    freshValue, repOf, blockAt,
    predecessors, reversePostorder, dominators, dominates,
    verify, showFunc,
)

-- The low IR: an SSA control-flow graph, parameterized over its instructions
-- (`NATIVE-BACKEND.md`, M27 phase 0).
--
-- The parameter is the design. Instruction selection produces a *different*
-- instruction type rather than rewriting this one in place, so that after it a
-- virtual opcode is not representable and every `match` stays exhaustive --
-- and the analyses below, which are the subtle part, are written once for
-- both. QBE and Go put virtual and machine instructions in one enum and pay
-- nothing for it, because neither C nor Go checks a `switch` for
-- exhaustiveness; Hoopl's graph is parameterized over its node type for
-- exactly the reason this one is.
--
-- Three things follow from Turkey rather than from taste:
--
-- * **A value is a dense index**, because there is no object identity
--   (FINDINGS 10) and so nothing to point at. Everything known about a value
--   lives in an array indexed by it. That is also what Cranelift's entity
--   references and Go's `ssa.Value` ids are, so the constraint pushes toward
--   what a backend wants anyway. `Value` and `BlockId` are newtypes and are
--   therefore free -- a single-field immutable wrapper is its payload and is
--   not allocated -- so the two index spaces can be kept apart at no cost.
-- * **Blocks take parameters and jumps carry arguments**, rather than phi
--   nodes. A jump with arguments is a parallel copy at a named point, which is
--   what going out of SSA and allocating registers both need; a phi is a
--   pseudo-instruction that every pass has to special-case.
-- * **A block's terminator is not optional.** Hoopl indexes a block by its
--   shape at entry and exit so that one which must end in a terminator cannot
--   fall through, and needs GADTs to do it. A non-optional field says the same
--   thing, and `Unreachable` is what an unfinished block holds while it is
--   being built.
--
-- SSA here is *function-wide*: a definition dominates its uses. The Python
-- backend's rule is block-local, which forces everything crossing an edge
-- through memory and makes promoting it a precondition for every optimization
-- rather than one of them.

-- No diagnostics: `verify` answers with complaints rather than failing, so
-- this module depends on nothing but the standard library.
import Data.Set as Set

-- -- values and representations ---------------------------------------------------

-- A value, as an index into the function's side tables.
type Value = Value(Int)

fun valueId(Value(n)) -> Int = n

-- A block, as an index into `Func.blocks`. Distinct from `Value` so that the
-- two cannot be confused, which costs nothing: both erase to a machine
-- integer.
type BlockId = BlockId(Int)

fun blockId(BlockId(n)) -> Int = n

instance Eq Value { fun eq(Value(a), Value(b)) = a == b }

instance Eq BlockId { fun eq(BlockId(a), BlockId(b)) = a == b }

-- What a register has to hold. Instruction selection reads this.
-- `Unit` is zero-width and occupies no register. Every instruction defines a
-- value, so an effectful one defines a unit -- and without this that would
-- burn a register per store, per call for effect, per assignment. LLVM's
-- `void` and QBE's absent return type are the same choice.
type RepClass = Unit | I1 | I8 | I32 | I64 | F64 | Ptr

instance Eq RepClass {
    fun eq(x, y) = match (x, y) {
        (Unit, Unit) -> True
        (I1, I1) -> True
        (I8, I8) -> True
        (I32, I32) -> True
        (I64, I64) -> True
        (F64, F64) -> True
        (Ptr, Ptr) -> True
        _ -> False
    }
}

-- A representation is two facts, not one.
--
-- The Python backend spells the second as two members of one enum -- `ptr` and
-- `boxed` -- which works until something asks a register class about a
-- `boxed`. A register class and a collector obligation are different
-- questions and both get asked.
type Rep = Rep {
    class_ : RepClass,
    -- Whether the collector must find this value at a safepoint.
    traced : Bool,
}

instance Show RepClass {
    fun show(c) = match c {
        Unit -> "unit"
        I1 -> "i1"
        I8 -> "i8"
        I32 -> "i32"
        I64 -> "i64"
        F64 -> "f64"
        Ptr -> "ptr"
    }
}

fun traced(c : RepClass) -> Rep = Rep { class_ = c, traced = True }

fun untraced(c : RepClass) -> Rep = Rep { class_ = c, traced = False }

instance Eq Rep {
    fun eq(a, b) = a.class_ == b.class_ && a.traced == b.traced
}

-- A traced pointer prints `ptr*`: the star is the collector's obligation, and
-- the distinction is exactly what the Python backend's `ptr`/`boxed` pair
-- cannot express while also answering a register class.
instance Show Rep {
    fun show(r) = if r.traced { show(r.class_) + "*" } else { show(r.class_) }
}

-- -- effects ------------------------------------------------------------------------

-- What an instruction may do, which is what reordering, elimination and
-- garbage collection all need to ask.
--
-- `allocates` is the load-bearing one: allocation is the only thing that can
-- trigger a collection, so an allocating instruction is a **safepoint**, and a
-- safepoint is where the register allocator has to record which registers hold
-- traced values. Without that bit a backend has no choice but to root every
-- pointer live anywhere in the function, which is what the Python one does and
-- what FINDINGS 55 measures at 481 slots in a single frame.
--
-- It is here from the first commit even though nothing reads it until
-- allocation exists, because retrofitting precise stack maps into a backend
-- that did not plan for them is the rewrite this design is avoiding.
-- `traps` and `diverges` are different questions and the instruction set found
-- the difference. `diverges` is "never returns", which the Python backend
-- already tracks for a call to a bottoming function. `traps` is "may not
-- return" -- an `add` that overflows panics (PRIMITIVES.md 1.1), and a
-- dead-code pass may not delete one on the grounds that its result is unused.
type Effects = Effects {
    reads     : Bool,
    writes    : Bool,
    allocates : Bool,
    traps     : Bool,
    diverges  : Bool,
}

-- The union of two effects, and the effect of doing nothing. An instruction
-- that both reads and traps is `combine(reads_(), traps())`, and a fold over a
-- block's instructions is `Array.fold` with no special case for the empty one,
-- which is what a monoid is for.
instance Semigroup Effects {
    fun combine(a, b) = Effects {
        reads = a.reads || b.reads,
        writes = a.writes || b.writes,
        allocates = a.allocates || b.allocates,
        traps = a.traps || b.traps,
        diverges = a.diverges || b.diverges,
    }
}

instance Monoid Effects {
    fun empty() = Effects {
        reads = False, writes = False, allocates = False, traps = False,
        diverges = False,
    }
}

fun reads_() -> Effects {
    let e = empty()
    e.reads = True
    e
}

fun writes() -> Effects {
    let e = empty()
    e.writes = True
    e
}

fun allocates() -> Effects {
    let e = empty()
    e.allocates = True
    e
}

fun diverges() -> Effects {
    let e = empty()
    e.diverges = True
    e
}

fun traps() -> Effects {
    let e = empty()
    e.traps = True
    e
}

fun isSafepoint(e : Effects) -> Bool = e.allocates

-- May an instruction whose result is unused be deleted?
--
-- Not the same as "pure". A read may be deleted -- reading has no effect on
-- anything else -- and so may an allocation, since a collection is not
-- observable. What may not be deleted is a write, a trap or a divergence,
-- because each is the whole reason the instruction is there.
fun deletable(e : Effects) -> Bool = !e.writes && !e.traps && !e.diverges

-- -- the graph ----------------------------------------------------------------------

-- Terminators are shared by every instantiation, because they are what the
-- control-flow graph is made of: both a virtual and a machine instruction
-- sequence branches and jumps, and every analysis below reads only this.
type Term
    = Ret(Value)
    -- The arguments are the target block's parameters, positionally.
    | Jump(BlockId, Array Value)
    | Branch(Value, BlockId, BlockId)
    | Panic(Value)
    -- What an unfinished block holds while it is built, and what a block after
    -- a diverging call legitimately ends with.
    | Unreachable

fun successors(t : Term) -> Array BlockId = match t {
    Ret(_) -> []
    Jump(target, _) -> [target]
    Branch(_, yes, no) -> [yes, no]
    Panic(_) -> []
    Unreachable -> []
}

type Block i = Block {
    params : Array Value,
    insts  : Array i,
    term   : Term,
}

fun newBlock() -> Block i =
    Block { params = [], insts = [], term = Unreachable }

type Func i = Func {
    name   : String,
    params : Array Value,
    result : Rep,
    blocks : Array (Block i),
    entry  : BlockId,
    -- Indexed by `valueId`. The side table a dense index buys.
    reps   : Array Rep,
}

fun newFunc(name : String, result : Rep) -> Func i = Func {
    name = name, params = [], result = result,
    blocks = [], entry = BlockId(0), reps = [],
}

-- A value of the given representation, appended to the function's table.
fun freshValue(f : Func i, r : Rep) -> Value {
    let id = len(f.reps)
    Array.push(f.reps, r)
    Value(id)
}

fun repOf(f : Func i, v : Value) -> Rep = f.reps[valueId(v)]

fun blockAt(f : Func i, b : BlockId) -> Block i = f.blocks[blockId(b)]

-- What every analysis needs of an instruction, and nothing more.
--
-- Four methods on purpose. Anything opcode-specific -- what a particular
-- opcode's operands must be, what it means -- belongs to the module that
-- declares the instruction type, not here, because the generic passes cannot
-- use it and adding it would make this class a place where target knowledge
-- accumulates.
-- `Show` is a superclass rather than a `render` method of its own: printing an
-- instruction is not a backend concept, and an instruction that could not be
-- shown would be one nothing could debug.
class Inst i : Show i {
    fun uses(i) -> Array Value
    fun defs(i) -> Array Value
    fun effects(i) -> Effects
}

-- -- traversal and dominance --------------------------------------------------------

fun predecessors(f : Func i) -> Array (Array BlockId) {
    let out : Array (Array BlockId) = []
    for _ in f.blocks { Array.push(out, []) }
    for var b = 0; b < len(f.blocks); b = b + 1 {
        for s in successors(f.blocks[b].term) {
            Array.push(out[blockId(s)], BlockId(b))
        }
    }
    out
}

-- Blocks in reverse postorder, which is the order the dominance solver wants:
-- every block but the entry appears after at least one of its predecessors, so
-- one pass already computes most of the answer.
fun reversePostorder(f : Func i) -> Array BlockId {
    let order : Array BlockId = []
    let seen = Set.new()
    -- An explicit stack of (block, next successor to visit), because the
    -- recursive form is depth-first over a graph the size of a function and
    -- this is a compiler that has already met its own stack (FINDINGS 43).
    let stack = [(f.entry, 0)]
    Set.add(seen, blockId(f.entry))
    while len(stack) > 0 {
        let (b, at) = stack[len(stack) - 1]
        let succs = successors(f.blocks[blockId(b)].term)
        if at >= len(succs) {
            let _ = Array.pop(stack)
            Array.push(order, b)
        } else {
            stack[len(stack) - 1] = (b, at + 1)
            let next = succs[at]
            if !Set.contains(seen, blockId(next)) {
                Set.add(seen, blockId(next))
                Array.push(stack, (next, 0))
            }
        }
    }
    Array.reverse(order)
}

-- The immediate dominator of each block, or `None` for one the entry does not
-- reach. `None` rather than a sentinel index: an unreachable block and block
-- number -1 are different facts, and a language with `Option` should not need
-- to be told them apart by convention.
--
-- Cooper, Harvey and Kennedy's iterative algorithm ("A Simple, Fast Dominance
-- Algorithm", 2001), which is the one to write and not merely the one that is
-- easy. Its headline claim -- 2.5x faster than Lengauer-Tarjan -- does not
-- hold up; a more careful implementation of Lengauer-Tarjan later reversed it.
-- What holds up is that on the graphs a compiler actually meets, every
-- published algorithm lands within a small factor of a breadth-first search
-- and all of them are measured in microseconds, so the difference does not
-- matter and simplicity is the only axis left. This needs no spanning tree and
-- no union-find; LLVM's Semi-NCA and GCC's Lengauer-Tarjan need both.
fun dominators(f : Func i) -> Array (Option Int) {
    let order = reversePostorder(f)
    -- Position in reverse postorder, which is what `intersect` walks by. Only
    -- reachable blocks have one, and only reachable blocks are ever asked.
    let rank = Array.filled(len(f.blocks), 0)
    for var i = 0; i < len(order); i = i + 1 { rank[blockId(order[i])] = i }
    let idom : Array (Option Int) = Array.filled(len(f.blocks), None)
    idom[blockId(f.entry)] = Some(blockId(f.entry))
    let preds = predecessors(f)

    var changed = True
    while changed {
        changed = False
        for b in order {
            let here = blockId(b)
            if here == blockId(f.entry) { continue }
            var new : Option Int = None
            for p in preds[here] {
                let there = blockId(p)
                -- Only predecessors already given an answer; an unreachable
                -- one never gets one and is skipped for ever.
                -- The braces are FINDINGS 22: an assignment cannot be a
                -- match arm's body.
                match idom[there] {
                    None -> {}
                    Some(_) -> {
                        new = match new {
                            None -> Some(there)
                            Some(sofar) -> Some(intersect(idom, rank, there, sofar))
                        }
                    }
                }
            }
            match new {
                None -> {}
                Some(found) -> if idom[here] != Some(found) {
                    idom[here] = Some(found)
                    changed = True
                }
            }
        }
    }
    idom
}

-- The nearest common dominator of two blocks, walked by reverse-postorder
-- rank: the deeper of the two climbs until they meet.
fun intersect(idom : Array (Option Int), rank : Array Int, a : Int,
              b : Int) -> Int {
    var x = a
    var y = b
    while x != y {
        while rank[x] > rank[y] { x = up(idom, x) }
        while rank[y] > rank[x] { y = up(idom, y) }
    }
    x
}

-- The immediate dominator of a block already known to have one. Only reached
-- for a block `intersect` arrived at through the answers already computed, so
-- an absence here is this module's bug rather than a malformed program.
fun up(idom : Array (Option Int), b : Int) -> Int = match idom[b] {
    Some(n) -> n
    None -> b
}

-- Does `a` dominate `b`? Walks the dominator tree, which is the form the
-- verifier and the optimizations want and is fast enough at this size; a
-- depth-plus-interval numbering is the answer if it ever is not.
fun dominates(idom : Array (Option Int), a : BlockId, b : BlockId) -> Bool {
    var here = blockId(b)
    let want = blockId(a)
    while True {
        if here == want { return True }
        match idom[here] {
            None -> return False
            Some(next) -> {
                -- The entry is its own immediate dominator, which is where the
                -- walk stops.
                if next == here { return False }
                here = next
            }
        }
    }
    False
}

-- -- verification --------------------------------------------------------------------

-- Reject a malformed function, as a list of complaints.
--
-- Run after every pass. A miscompile then becomes a rejected program at the
-- pass that caused it rather than a wrong answer at the end, which is the
-- property that makes an optimizer safe to build incrementally -- and it
-- matters more here than in the Python backend, because the oracle below Core
-- is differential *execution* and localizes a fault to a program rather than
-- to a line.
fun verify[Inst i](f : Func i) -> Array String {
    let out = []
    let count = len(f.blocks)
    if count == 0 {
        Array.push(out, f.name + ": no blocks")
        return out
    }
    if blockId(f.entry) < 0 || blockId(f.entry) >= count {
        Array.push(out, f.name + ": entry is not a block")
        return out
    }
    -- The entry takes the function's parameters, so it must declare none of
    -- its own: there is no edge into it to supply them.
    if len(f.blocks[blockId(f.entry)].params) > 0 {
        Array.push(out, f.name + ": the entry block takes parameters")
    }
    -- Only a pointer may be traced. Today every `Ptr` is traced and the field
    -- looks redundant; it is not, and this is what keeps it honest -- a code
    -- address is a pointer the collector must *not* follow, and instruction
    -- selection is where the first one appears.
    for var i = 0; i < len(f.reps); i = i + 1 {
        if f.reps[i].traced && f.reps[i].class_ != Ptr {
            Array.push(out, f.name + ": value " + Int.toString(i) + " is "
                            + show(f.reps[i]) + ", which is not a pointer")
        }
    }

    -- Where each value is defined. `defAt` is a position within a block and
    -- -1 is a real one -- a parameter, before every instruction -- so it stays
    -- an `Int`; `defBlock` is a presence and is an `Option`.
    let defBlock : Array (Option Int) = Array.filled(len(f.reps), None)
    let defAt = Array.filled(len(f.reps), -1)
    for v in f.params { define(out, f, defBlock, defAt, v, blockId(f.entry), -1) }
    for var b = 0; b < count; b = b + 1 {
        for v in f.blocks[b].params { define(out, f, defBlock, defAt, v, b, -1) }
        for var n = 0; n < len(f.blocks[b].insts); n = n + 1 {
            for v in defs(f.blocks[b].insts[n]) {
                define(out, f, defBlock, defAt, v, b, n)
            }
        }
    }

    -- Only reachable code is checked. Dominance is a statement about paths
    -- from the entry, so it says nothing about a block no path reaches, and
    -- asking anyway would report a use as undominated when what is true is
    -- that it is dead. Unreachable blocks are ordinary here rather than
    -- exceptional: an expression whose every arm diverges leaves the merge
    -- block its context asked for with no edge into it. LLVM's verifier draws
    -- the same line for the same reason.
    let live = Set.new()
    for b in reversePostorder(f) { Set.add(live, blockId(b)) }
    let idom = dominators(f)
    for var b = 0; b < count; b = b + 1 {
        if !Set.contains(live, b) { continue }
        let block = f.blocks[b]
        for var n = 0; n < len(block.insts); n = n + 1 {
            for v in uses(block.insts[n]) {
                checkUse(out, f, idom, defBlock, defAt, v, b, n)
            }
        }
        checkTerm(out, f, idom, defBlock, defAt, b, block.term)
    }
    out
}

fun define(out : Array String, f : Func i, defBlock : Array (Option Int),
           defAt : Array Int, v : Value, b : Int, n : Int) -> Unit {
    let id = valueId(v)
    if id < 0 || id >= len(f.reps) {
        Array.push(out, f.name + ": value " + Int.toString(id) + " has no representation")
        return
    }
    match defBlock[id] {
        Some(_) -> {
            Array.push(out, f.name + ": value " + Int.toString(id)
                            + " is defined twice")
            return
        }
        None -> {}
    }
    defBlock[id] = Some(b)
    defAt[id] = n
}

-- Every use is dominated by its definition. Within one block that is textual
-- order; across blocks it is the dominator tree.
fun checkUse(out : Array String, f : Func i, idom : Array (Option Int),
             defBlock : Array (Option Int), defAt : Array Int, v : Value,
             b : Int, n : Int) -> Unit {
    let id = valueId(v)
    if id < 0 || id >= len(f.reps) {
        Array.push(out, f.name + ": use of value " + Int.toString(id)
                        + " which has no representation")
        return
    }
    let where = match defBlock[id] {
        Some(n) -> n
        None -> {
            Array.push(out, f.name + ": use of undefined value " + Int.toString(id))
            return
        }
    }
    if where == b {
        if defAt[id] >= n {
            Array.push(out, f.name + ": value " + Int.toString(id)
                            + " is used before it is defined")
        }
        return
    }
    if !dominates(idom, BlockId(where), BlockId(b)) {
        Array.push(out, f.name + ": value " + Int.toString(id)
                        + " is used outside the blocks its definition dominates")
    }
}

fun checkTerm(out : Array String, f : Func i, idom : Array (Option Int),
              defBlock : Array (Option Int), defAt : Array Int, b : Int,
              t : Term) -> Unit {
    let last = len(f.blocks[b].insts)
    match t {
        Ret(v) -> {
            checkUse(out, f, idom, defBlock, defAt, v, b, last)
            if repOf(f, v) != f.result {
                Array.push(out, f.name + ": returns " + show(repOf(f, v))
                                + " where " + show(f.result) + " was declared")
            }
        }
        Jump(target, args) -> checkEdge(out, f, idom, defBlock, defAt, b, target, args)
        Branch(cond, yes, no) -> {
            checkUse(out, f, idom, defBlock, defAt, cond, b, last)
            if repOf(f, cond).class_ != I1 {
                Array.push(out, f.name + ": branches on "
                                + show(repOf(f, cond)) + " rather than i1")
            }
            checkEdge(out, f, idom, defBlock, defAt, b, yes, [])
            checkEdge(out, f, idom, defBlock, defAt, b, no, [])
        }
        Panic(v) -> checkUse(out, f, idom, defBlock, defAt, v, b, last)
        Unreachable -> {}
    }
}

-- A jump's arguments are the target's parameters, so they have to agree in
-- number and in representation. `Branch` carries none, which is why a block
-- reached by a branch may not take any.
fun checkEdge(out : Array String, f : Func i, idom : Array (Option Int),
              defBlock : Array (Option Int), defAt : Array Int, b : Int,
              target : BlockId, args : Array Value) -> Unit {
    let to = blockId(target)
    if to < 0 || to >= len(f.blocks) {
        Array.push(out, f.name + ": jump to block " + Int.toString(to)
                        + ", which does not exist")
        return
    }
    let params = f.blocks[to].params
    if len(params) != len(args) {
        Array.push(out, f.name + ": jump to block " + Int.toString(to) + " passes "
                        + Int.toString(len(args)) + " arguments to "
                        + Int.toString(len(params)) + " parameters")
        return
    }
    let last = len(f.blocks[b].insts)
    for var i = 0; i < len(args); i = i + 1 {
        checkUse(out, f, idom, defBlock, defAt, args[i], b, last)
        if repOf(f, args[i]) != repOf(f, params[i]) {
            Array.push(out, f.name + ": jump to block " + Int.toString(to)
                            + " passes " + show(repOf(f, args[i]))
                            + " where " + show(repOf(f, params[i]))
                            + " was declared")
        }
    }
}

-- -- printing -----------------------------------------------------------------------

fun showFunc[Inst i](f : Func i) -> String {
    let b = String.builder()
    String.push(b, "fun @" + f.name + "(")
    for var i = 0; i < len(f.params); i = i + 1 {
        if i > 0 { String.push(b, ", ") }
        String.push(b, showValue(f, f.params[i]))
    }
    String.push(b, ") -> " + show(f.result) + " {\n")
    for var n = 0; n < len(f.blocks); n = n + 1 {
        let block = f.blocks[n]
        String.push(b, "  b" + Int.toString(n))
        if len(block.params) > 0 {
            String.push(b, "(")
            for var i = 0; i < len(block.params); i = i + 1 {
                if i > 0 { String.push(b, ", ") }
                String.push(b, showValue(f, block.params[i]))
            }
            String.push(b, ")")
        }
        if n == blockId(f.entry) { String.push(b, "  ; entry") }
        String.push(b, ":\n")
        for inst in block.insts { String.push(b, "    " + show(inst) + "\n") }
        String.push(b, "    " + showTerm(f, block.term) + "\n")
    }
    String.push(b, "}\n")
    String.build(b)
}

fun showValue(f : Func i, v : Value) -> String =
    "%" + Int.toString(valueId(v)) + ":" + show(repOf(f, v))

fun showTerm(f : Func i, t : Term) -> String = match t {
    Ret(v) -> "ret " + showValue(f, v)
    Jump(target, args) -> {
        let b = String.builder()
        String.push(b, "jump b" + Int.toString(blockId(target)) + "(")
        for var i = 0; i < len(args); i = i + 1 {
            if i > 0 { String.push(b, ", ") }
            String.push(b, showValue(f, args[i]))
        }
        String.push(b, ")")
        String.build(b)
    }
    Branch(cond, yes, no) ->
        "branch " + showValue(f, cond) + ", b" + Int.toString(blockId(yes))
        + ", b" + Int.toString(blockId(no))
    Panic(v) -> "panic " + showValue(f, v)
    Unreachable -> "unreachable"
}
