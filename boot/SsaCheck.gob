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
import Turkey.SsaLower as SsaLower

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
    -- Built through `L.Builder`, which is what keeps the representation table
    -- in step: a value cannot be made without being given one, and an
    -- instruction cannot be appended except through a cursor that does.
    let b = L.builder(f, BlockId(0))
    let b0 = L.block(b)
    let b1 = L.block(b)
    let b2 = L.block(b)
    let b3 = L.block(b)
    L.into(b, b0)

    let zero = L.emit(b, ConstInt(0), i64)
    let cond = L.emit(b, Cmp(Eq, zero, zero), i1)
    L.terminate(b, Branch(cond, b1, b2))

    L.into(b, b1)
    let one = L.emit(b, ConstInt(1), i64)
    L.terminate(b, Jump(b3, [one]))

    L.into(b, b2)
    let two = L.emit(b, Bin(Add, zero, zero), i64)
    L.terminate(b, Jump(b3, [two]))

    let merged = L.param(b, b3, i64)
    L.into(b, b3)
    L.terminate(b, Ret(merged))

    print(Ssa.showFunc(f))

    let idom = Ssa.dominators(f)
    print("idom: " + String.join(Array.map(idom, showIdom), " "))
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
    L.into(b, b1)
    let _ = L.emit(b, Neg(two), i64)
    L.into(b, b0)
    let _ = L.emit(b, Cmp(Lt, zero, cond), i1)
    report("verify after breaking it", Ssa.verify(f))
    report("reps after breaking it", repComplaints(f))

    -- A block nothing branches to has no immediate dominator, which is what
    -- `None` says and what a sentinel index could not say apart from a real
    -- block number.
    let orphan = L.block(b)
    L.into(b, orphan)
    L.terminate(b, Ret(zero))
    print("orphan idom: " + showIdom(Ssa.dominators(f)[Ssa.blockId(orphan)]))
    print("b0 dom orphan: "
          + showBool(Ssa.dominates(Ssa.dominators(f), b0, orphan)))
}

-- The two verifiers are separate on purpose: `Ssa.verify` owns the shape of
-- the graph and dominance, and knows nothing about opcodes; `L.checkReps`
-- owns what an operation requires of its operands. Composing them is the
-- caller's job, which is what keeps target knowledge out of the generic layer.
fun repComplaints(f : Ssa.Func Low) -> Array String {
    let out : Array String = []
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

fun showBool(x : Bool) -> String = if x { "yes" } else { "no" }

-- `None` is a block the entry does not reach, which is a different fact from
-- any block number and is why the solver answers an `Option`.
fun showIdom(d : Option Int) -> String = match d {
    Some(n) -> Int.toString(n)
    None -> "-"
}
