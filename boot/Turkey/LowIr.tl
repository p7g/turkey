module Turkey.LowIr (
    BinOp(..), CmpOp(..), Conv(..), Callee(..), Op(..), Low(..),
    low, effectsOf, opUses, opResultRep, checkReps,
    Builder(..), builder, into, emit, effect, terminate, block, param,
)

-- The virtual instruction set: what `Turkey.Ssa`'s graph is instantiated with
-- before instruction selection (`NATIVE-BACKEND.md`, M27 phase 1).
--
-- Three decisions, and the first is the one that pays for writing this in
-- Turkey rather than porting `turkey/backend_ir.py`.
--
-- **Operands live in the opcode.** `Bin(Add, x, y)` rather than an opcode and
-- a separate operand list. LLVM and Cranelift keep operands beside the opcode
-- because walking a per-variant operand list uniformly is awkward in C++ and
-- Rust; a `match` makes it natural here, and the arity of every instruction
-- becomes a fact the *type checker* enforces. A verifier that would otherwise
-- have to check "does this opcode have the right number of operands" has
-- nothing left to check, which is a whole class of malformed IR that cannot be
-- built.
--
-- **An opcode is a constructor where a pass must match on it, and a symbol is
-- a string where none does.** Arithmetic, comparison and the heap operations
-- are constructors, because instruction selection is a match over them and
-- must be exhaustive. A call to a runtime function is opaque -- no pass asks
-- *which* runtime function, only that it is one -- so the symbol is a string
-- and the instruction set does not grow a constructor per primitive. That is
-- the line, and it is what keeps this set at two dozen rather than the
-- hundreds Go's `Op` has.
--
-- **The level is the runtime's ABI.** `ObjectGet(p, 3)` and not an address
-- computation, because that is one-to-one with what `turkey_runtime.h`
-- provides and with what the collector understands; the offsets are described
-- by an object's metadata and are not the IR's to invent. Going lower would
-- expose arithmetic no optimization here needs and would put the heap layout
-- in two places. Instruction selection expands these; that is its job.

import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..),
                   Effects(..), Inst(..), Term(..), Block(..), Func(..))

type BinOp = Add | Sub | Mul | Div | Rem | And | Or | Xor | Shl | Shr | ShrU

-- Signed comparison. `Ne`, `Le` and `Gt` are here rather than desugared to a
-- negation because a machine has them and instruction selection would only
-- have to put them back.
type CmpOp = Eq | Ne | Lt | Le | Gt | Ge

type Conv
    = IntToFloat | FloatToInt
    -- Between integer widths. The target width is the result's `Rep`, so the
    -- constructor only has to say whether the value is sign-extended.
    | Widen | Narrow
    -- Between `ptr` and `ptr*`: the same machine value with a different
    -- obligation on the collector. A no-op at run time, and not a no-op to the
    -- verifier, which is the point of `Rep` carrying both facts.
    | Retag

type Callee
    -- A Turkey function, by its mangled symbol.
    = Direct(String)
    -- A runtime entry point. Opaque: nothing matches on which one.
    | Runtime(String)
    -- Through a closure value.
    | Indirect(Value)

type Op
    = ConstInt(Int)
    | ConstFloat(Float)
    | ConstString(String)
    | ConstUnit
    | Bin(BinOp, Value, Value)
    | Cmp(CmpOp, Value, Value)
    | Neg(Value)
    | Not(Value)
    | Convert(Conv, Value)
    -- The uniform representation, for a value whose type is a variable.
    | Box(Value)
    | Unbox(Value)
    -- A heap object: `kind` and `tag` as the runtime understands them,
    -- `count` fields, and the field metadata -- three bits of representation
    -- per field, which is what the collector scans by and what says how wide
    -- each field is. See `Turkey.SsaLower.metadata`.
    | ObjectNew(Int, Int, Int, Int)
    | ObjectGet(Value, Int)
    | ObjectSet(Value, Int, Value)
    | ObjectTag(Value)
    -- Length, initial element, and the element's class, which is what decides
    -- the stride. `Array Byte` is packed because of this and not by name.
    | ArrayNew(Value, Value, RepClass)
    | ArrayGet(Value, Value)
    | ArraySet(Value, Value, Value)
    | ArrayLength(Value)
    -- A `var`'s cell.
    | CellNew(Value)
    | CellLoad(Value)
    | CellStore(Value, Value)
    -- A closure over `code`, with room for `n` captures filled in afterwards.
    | ClosureNew(String, Int)
    | ClosureCapture(Value, Int, Value)
    | Call(Callee, Array Value)
    | GlobalLoad(String)
    | GlobalStore(String, Value)
    -- A mutable local. With function-wide SSA these are only what is genuinely
    -- mutable, not everything that crosses an edge.
    | SlotLoad(Int)
    | SlotStore(Int, Value)

-- An instruction is an operation and the value it defines.
--
-- Every instruction defines exactly one value, and an effectful one defines a
-- `unit`. Uniform is cheaper than an `Option` here in both senses: no
-- allocation to ask what an instruction defines, and no case to forget.
type Low = Low {
    result : Value,
    op     : Op,
}

fun low(result : Value, op : Op) -> Low = Low { result = result, op = op }

-- -- building ------------------------------------------------------------------------

-- A cursor into one block of one function.
--
-- The side table `Ssa.Func` keeps -- a value's representation, indexed by its
-- id -- is not optional, and this is what makes it invisible. A value cannot
-- be made without being given a representation, and there is no way to append
-- an instruction that does not go through here, so the table cannot fall out
-- of step with the instructions.
--
-- The alternative is a self-describing value, `Value { id, rep }`, which is
-- what LLVM and Go have -- and both can, because a value there is a *pointer*
-- to one shared object. Turkey records have reference semantics and no
-- identity (FINDINGS 10), so a value carrying its own representation would be
-- *copied* at every mention, and two mentions of one value would be two
-- records that agree only by convention. That is an inconsistent state made
-- representable, to avoid a table. It would also cost: `Value` is a newtype
-- and erases to a machine word, so `uses` answers a packed array of integers
-- rather than an array of pointers, in the hottest query a backend has.
--
-- Cranelift reaches the same shape from the same constraint: a `Value` is an
-- index and `DataFlowGraph::value_type` is the lookup.
type Builder = Builder {
    func : Ssa.Func Low,
    at   : Ssa.BlockId,
}

fun builder(f : Ssa.Func Low, at : Ssa.BlockId) -> Builder =
    Builder { func = f, at = at }

-- Move the cursor. Blocks are built in whatever order the lowering finds
-- them, so this is ordinary rather than exceptional.
fun into(b : Builder, at : Ssa.BlockId) -> Unit { b.at = at }

-- Append an operation and answer the value it defines.
--
-- The representation is given rather than derived, because for `ObjectGet`,
-- `ArrayGet`, `Call`, `SlotLoad` and `Unbox` only the caller knows it. Where
-- the opcode *does* determine it, `checkReps` says so afterwards -- which is
-- the right division: the builder is for convenience and the verifier is for
-- truth, and a builder that panicked here would be a second opinion about
-- what an opcode means.
fun emit(b : Builder, op : Op, r : Rep) -> Value {
    let v = Ssa.freshValue(b.func, r)
    Array.push(Ssa.blockAt(b.func, b.at).insts, low(v, op))
    v
}

-- An operation evaluated for its effect. The value it defines is a `unit`
-- nothing reads, which is what keeps every instruction defining exactly one.
fun effect(b : Builder, op : Op) -> Unit {
    let _ = emit(b, op, Ssa.untraced(Unit))
}

fun terminate(b : Builder, t : Ssa.Term) -> Unit {
    Ssa.blockAt(b.func, b.at).term = t
}

-- A new empty block, and its id.
fun block(b : Builder) -> Ssa.BlockId {
    let id = len(b.func.blocks)
    Array.push(b.func.blocks, Ssa.newBlock())
    Ssa.BlockId(id)
}

-- A parameter of a block, which is where a phi would be.
fun param(b : Builder, at : Ssa.BlockId, r : Rep) -> Value {
    let v = Ssa.freshValue(b.func, r)
    Array.push(Ssa.blockAt(b.func, at).params, v)
    v
}

-- What each operation may do. The verifier does not read this; the
-- optimizations and, eventually, the register allocator's stack maps do.
fun effectsOf(op : Op) -> Effects = match op {
    ConstInt(_) -> empty()
    ConstFloat(_) -> empty()
    -- A string literal is interned by the runtime rather than allocated per
    -- evaluation, so it is not a safepoint.
    ConstString(_) -> empty()
    ConstUnit -> empty()
    -- Trapping, not pure: `add`, `sub`, `mul` and `neg` panic on overflow and
    -- `div` and `rem` panic at zero (PRIMITIVES.md 1.1), so an unused one may
    -- not be deleted. The bitwise and shift operations do not trap, and
    -- saying so is what lets dead code among them go.
    Bin(o, _, _) -> match o {
        Add -> Ssa.traps()
        Sub -> Ssa.traps()
        Mul -> Ssa.traps()
        Div -> Ssa.traps()
        Rem -> Ssa.traps()
        _ -> empty()
    }
    Cmp(_, _, _) -> empty()
    Neg(_) -> Ssa.traps()
    Not(_) -> empty()
    Convert(_, _) -> empty()
    Box(_) -> Ssa.allocates()
    Unbox(_) -> Ssa.reads_()
    ObjectNew(_, _, _, _) -> Ssa.allocates()
    ObjectGet(_, _) -> Ssa.reads_()
    ObjectSet(_, _, _) -> Ssa.writes()
    ObjectTag(_) -> Ssa.reads_()
    ArrayNew(_, _, _) -> Ssa.allocates()
    -- Reads *and* traps: an index out of range panics.
    ArrayGet(_, _) -> combine(Ssa.reads_(), Ssa.traps())
    ArraySet(_, _, _) -> combine(Ssa.writes(), Ssa.traps())
    ArrayLength(_) -> Ssa.reads_()
    CellNew(_) -> Ssa.allocates()
    CellLoad(_) -> Ssa.reads_()
    CellStore(_, _) -> Ssa.writes()
    ClosureNew(_, _) -> Ssa.allocates()
    ClosureCapture(_, _, _) -> Ssa.writes()
    -- A call may do anything the callee does, which is everything. Knowing
    -- better than this per callee is an interprocedural summary and is not
    -- something this phase has.
    Call(_, _) -> everything()
    GlobalLoad(_) -> Ssa.reads_()
    GlobalStore(_, _) -> Ssa.writes()
    SlotLoad(_) -> Ssa.reads_()
    SlotStore(_, _) -> Ssa.writes()
}

fun everything() -> Effects = Effects {
    reads = True, writes = True, allocates = True, traps = True,
    diverges = False,
}

-- The values an operation reads.
--
-- One `match`, and adding an operation is a compile error here -- which is
-- the whole reason the opcode is a constructor and not a string.
fun opUses(op : Op) -> Array Value = match op {
    ConstInt(_) -> []
    ConstFloat(_) -> []
    ConstString(_) -> []
    ConstUnit -> []
    Bin(_, a, b) -> [a, b]
    Cmp(_, a, b) -> [a, b]
    Neg(a) -> [a]
    Not(a) -> [a]
    Convert(_, a) -> [a]
    Box(a) -> [a]
    Unbox(a) -> [a]
    ObjectNew(_, _, _, _) -> []
    ObjectGet(a, _) -> [a]
    ObjectSet(a, _, b) -> [a, b]
    ObjectTag(a) -> [a]
    ArrayNew(a, b, _) -> [a, b]
    ArrayGet(a, b) -> [a, b]
    ArraySet(a, b, c) -> [a, b, c]
    ArrayLength(a) -> [a]
    CellNew(a) -> [a]
    CellLoad(a) -> [a]
    CellStore(a, b) -> [a, b]
    ClosureNew(_, _) -> []
    ClosureCapture(a, _, b) -> [a, b]
    Call(callee, args) -> {
        let out : Array Value = []
        match callee {
            Indirect(f) -> Array.push(out, f)
            _ -> {}
        }
        for a in args { Array.push(out, a) }
        out
    }
    GlobalLoad(_) -> []
    GlobalStore(_, a) -> [a]
    SlotLoad(_) -> []
    SlotStore(_, a) -> [a]
}

instance Inst Low {
    fun uses(i) = opUses(i.op)
    fun defs(i) = [i.result]
    fun effects(i) = effectsOf(i.op)
}

-- The representation an operation's result must have, where the operation
-- decides it. `None` means the operation is representation-polymorphic and the
-- result's declared representation stands: `ObjectGet` answers whatever the
-- field holds, and only the object's own layout knows that.
--
-- Arity is not checked here and cannot be: it is enforced by the constructor.
fun opResultRep(op : Op) -> Option Rep = match op {
    ConstFloat(_) -> Some(Ssa.untraced(F64))
    ConstString(_) -> Some(Ssa.traced(Ptr))
    ConstUnit -> Some(Ssa.untraced(Unit))
    Cmp(_, _, _) -> Some(Ssa.untraced(I1))
    Box(_) -> Some(Ssa.traced(Ptr))
    ObjectNew(_, _, _, _) -> Some(Ssa.traced(Ptr))
    ObjectTag(_) -> Some(Ssa.untraced(I32))
    ArrayNew(_, _, _) -> Some(Ssa.traced(Ptr))
    ArrayLength(_) -> Some(Ssa.untraced(I64))
    CellNew(_) -> Some(Ssa.traced(Ptr))
    ClosureNew(_, _) -> Some(Ssa.traced(Ptr))
    ObjectSet(_, _, _) -> Some(Ssa.untraced(Unit))
    ArraySet(_, _, _) -> Some(Ssa.untraced(Unit))
    CellStore(_, _) -> Some(Ssa.untraced(Unit))
    ClosureCapture(_, _, _) -> Some(Ssa.untraced(Unit))
    GlobalStore(_, _) -> Some(Ssa.untraced(Unit))
    SlotStore(_, _) -> Some(Ssa.untraced(Unit))
    _ -> None
}

-- The representation checks the constructor cannot make.
--
-- Answers complaints rather than failing, the way `Ssa.verify` does, and is
-- meant to be composed with it: the generic verifier owns dominance and the
-- shape of the graph, and this owns what an operation requires of its
-- operands. Keeping them apart is what stops the generic layer from
-- accumulating target knowledge.
fun checkReps(f : Ssa.Func Low, i : Low) -> Array String {
    let out = []
    match opResultRep(i.op) {
        Some(want) -> if Ssa.repOf(f, i.result) != want {
            Array.push(out, "result is " + show(Ssa.repOf(f, i.result))
                            + " where " + show(want) + " was required")
        }
        None -> {}
    }
    match i.op {
        Bin(_, a, b) -> sameRep(f, out, a, b, "the operands of a binary operation")
        Cmp(_, a, b) -> sameRep(f, out, a, b, "the operands of a comparison")
        Not(a) -> wantClass(f, out, a, I1, "the operand of a logical not")
        ArrayGet(_, index) -> wantClass(f, out, index, I64, "an array index")
        ArraySet(_, index, _) -> wantClass(f, out, index, I64, "an array index")
        ArrayNew(length, _, _) -> wantClass(f, out, length, I64, "an array length")
        Unbox(a) -> wantClass(f, out, a, Ptr, "the operand of an unbox")
        _ -> {}
    }
    out
}

fun sameRep(f : Ssa.Func Low, out : Array String, a : Value, b : Value,
            what : String) -> Unit {
    if Ssa.repOf(f, a) != Ssa.repOf(f, b) {
        Array.push(out, what + " are " + show(Ssa.repOf(f, a)) + " and "
                        + show(Ssa.repOf(f, b)))
    }
}

fun wantClass(f : Ssa.Func Low, out : Array String, v : Value, c : RepClass,
              what : String) -> Unit {
    if Ssa.repOf(f, v).class_ != c {
        Array.push(out, what + " is " + show(Ssa.repOf(f, v)))
    }
}

-- -- printing -----------------------------------------------------------------------

instance Show Low {
    fun show(i) = "%" + Int.toString(Ssa.valueId(i.result)) + " = " + show(i.op)
}

fun v(x : Value) -> String = "%" + Int.toString(Ssa.valueId(x))

instance Show Op {
  fun show(op) = match op {
    ConstInt(n) -> "const " + Int.toString(n)
    ConstFloat(x) -> "const " + Float.toString(x)
    ConstString(s) -> "const \"" + s + "\""
    ConstUnit -> "const ()"
    Bin(o, a, b) -> show(o) + " " + v(a) + ", " + v(b)
    Cmp(o, a, b) -> show(o) + " " + v(a) + ", " + v(b)
    Neg(a) -> "neg " + v(a)
    Not(a) -> "not " + v(a)
    Convert(c, a) -> show(c) + " " + v(a)
    Box(a) -> "box " + v(a)
    Unbox(a) -> "unbox " + v(a)
    ObjectNew(kind, tag, count, bitmap) ->
        "object.new kind=" + Int.toString(kind) + " tag=" + Int.toString(tag)
        + " count=" + Int.toString(count) + " bitmap=" + Int.toString(bitmap)
    ObjectGet(a, i) -> "object.get " + v(a) + ", " + Int.toString(i)
    ObjectSet(a, i, b) -> "object.set " + v(a) + ", " + Int.toString(i) + ", " + v(b)
    ObjectTag(a) -> "object.tag " + v(a)
    ArrayNew(n, init, c) ->
        "array.new " + v(n) + ", " + v(init) + ", " + show(c)
    ArrayGet(a, i) -> "array.get " + v(a) + ", " + v(i)
    ArraySet(a, i, x) -> "array.set " + v(a) + ", " + v(i) + ", " + v(x)
    ArrayLength(a) -> "array.length " + v(a)
    CellNew(a) -> "cell.new " + v(a)
    CellLoad(a) -> "cell.load " + v(a)
    CellStore(a, b) -> "cell.store " + v(a) + ", " + v(b)
    ClosureNew(code, n) -> "closure.new @" + code + ", " + Int.toString(n)
    ClosureCapture(a, i, b) ->
        "closure.capture " + v(a) + ", " + Int.toString(i) + ", " + v(b)
    Call(callee, args) -> {
        let b = String.builder()
        String.push(b, "call " + show(callee))
        for a in args { String.push(b, " " + v(a)) }
        String.build(b)
    }
    GlobalLoad(name) -> "global.load $" + name
    GlobalStore(name, a) -> "global.store $" + name + ", " + v(a)
    SlotLoad(i) -> "slot.load " + Int.toString(i)
    SlotStore(i, a) -> "slot.store " + Int.toString(i) + ", " + v(a)
  }
}

instance Show Callee {
  fun show(c) = match c {
    Direct(name) -> "@" + name
    Runtime(name) -> "%" + name
    Indirect(f) -> v(f)
  }
}

instance Show BinOp {
  fun show(o) = match o {
    Add -> "add"
    Sub -> "sub"
    Mul -> "mul"
    Div -> "div"
    Rem -> "rem"
    And -> "and"
    Or -> "or"
    Xor -> "xor"
    Shl -> "shl"
    Shr -> "shr"
    ShrU -> "shru"
  }
}

instance Show CmpOp {
  fun show(o) = match o {
    Eq -> "eq"
    Ne -> "ne"
    Lt -> "lt"
    Le -> "le"
    Gt -> "gt"
    Ge -> "ge"
  }
}

instance Show Conv {
  fun show(c) = match c {
    IntToFloat -> "itof"
    FloatToInt -> "ftoi"
    Widen -> "widen"
    Narrow -> "narrow"
    Retag -> "retag"
  }
}

