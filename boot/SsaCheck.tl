-- A second entry point in `boot/`, whose whole job is to instantiate
-- `Turkey.Ssa` with `Turkey.LowIr` and exercise what they provide.
--
-- Nothing else imports either module yet -- the lowering into them is the next
-- phase -- and a module nothing imports is not type-checked, which for a
-- compiler under construction means it rots quietly. So this builds a graph
-- with a real dominance question in it, a diamond whose merge block is
-- dominated by neither arm, and then breaks it.
--
-- Deliberately in `boot/` rather than in `tests/programs/`: a program there is
-- part of the conformance corpus and of the `test_boot` differential, and this
-- one exists to check a module rather than to be compiled two ways.
-- `tests/test_ssa.py` runs it.

import Turkey.Ssa
import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..), Term(..),
                   Block(..), Func(..), Inst(..), Effects(..))
import Turkey.LowIr as L
import Turkey.LowIr (Op(..), Low(..), BinOp(..), CmpOp(..), Callee(..))

fun main() {
    let i64 = Ssa.untraced(I64)
    let i1 = Ssa.untraced(I1)
    let f = Ssa.newFunc("diamond", i64)

    --      b0
    --     /  \
    --    b1  b2
    --     \  /
    --      b3(x)
    -- The merge is dominated by the split and by neither arm, which is the
    -- smallest graph where the answer is not obvious from the block order.
    for _ in [0, 1, 2, 3] { Array.push(f.blocks, Ssa.newBlock()) }

    let zero = Ssa.freshValue(f, i64)
    let cond = Ssa.freshValue(f, i1)
    let one = Ssa.freshValue(f, i64)
    let two = Ssa.freshValue(f, i64)
    let merged = Ssa.freshValue(f, i64)

    Array.push(f.blocks[0].insts, L.low(zero, ConstInt(0)))
    Array.push(f.blocks[0].insts, L.low(cond, Cmp(Eq, zero, zero)))
    f.blocks[0].term = Branch(cond, BlockId(1), BlockId(2))

    Array.push(f.blocks[1].insts, L.low(one, ConstInt(1)))
    f.blocks[1].term = Jump(BlockId(3), [one])

    Array.push(f.blocks[2].insts, L.low(two, Bin(Add, zero, zero)))
    f.blocks[2].term = Jump(BlockId(3), [two])

    Array.push(f.blocks[3].params, merged)
    f.blocks[3].term = Ret(merged)

    print(Ssa.showFunc(f))

    let idom = Ssa.dominators(f)
    print("idom: " + String.join(Array.map(idom, Int.toString), " "))
    print("b0 dom b3: " + showBool(Ssa.dominates(idom, BlockId(0), BlockId(3))))
    print("b1 dom b3: " + showBool(Ssa.dominates(idom, BlockId(1), BlockId(3))))
    print("b1 dom b1: " + showBool(Ssa.dominates(idom, BlockId(1), BlockId(1))))

    report("verify", Ssa.verify(f))
    report("reps", repComplaints(f))

    -- What an unused instruction may cost. `add` traps on overflow, so it
    -- stays even when nothing reads it; `and` does not, so it may go.
    print("add deletable: " + showBool(Ssa.deletable(L.effectsOf(Bin(Add, zero, zero)))))
    print("and deletable: " + showBool(Ssa.deletable(L.effectsOf(Bin(And, zero, zero)))))
    print("object.new is a safepoint: "
          + showBool(Ssa.isSafepoint(L.effectsOf(ObjectNew(1, 0, 2, 0)))))
    print("array.get is a safepoint: "
          + showBool(Ssa.isSafepoint(L.effectsOf(ArrayGet(zero, zero)))))

    -- Break it two ways. `two` is defined in b2, which does not dominate b1;
    -- and a comparison's operands must agree in representation.
    Array.push(f.blocks[1].insts, L.low(Ssa.freshValue(f, i64), Neg(two)))
    Array.push(f.blocks[0].insts, L.low(Ssa.freshValue(f, i1), Cmp(Lt, zero, cond)))
    report("verify after breaking it", Ssa.verify(f))
    report("reps after breaking it", repComplaints(f))
}

-- The two verifiers are separate on purpose: `Ssa.verify` owns the shape of
-- the graph and dominance, and knows nothing about opcodes; `L.checkReps`
-- owns what an operation requires of its operands. Composing them is the
-- caller's job, which is what keeps target knowledge out of the generic layer.
fun repComplaints(f : Ssa.Func Low) -> Array String {
    let out = Array.new(2)
    for block in f.blocks {
        for i in block.insts {
            for m in L.checkReps(f, i) { Array.push(out, m) }
        }
    }
    out
}

fun report(what : String, messages : Array String) -> Unit {
    print(what + ": " + Int.toString(len(messages)))
    for m in messages { print("  " + m) }
}

fun showBool(b : Bool) -> String = if b { "yes" } else { "no" }
