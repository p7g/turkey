-- A second entry point in `boot/`, whose whole job is to instantiate
-- `Turkey.Ssa`.
--
-- Nothing else imports that module yet -- the lowering into it is the next
-- phase -- and a module nothing imports is not type-checked, which for a
-- compiler under construction means it rots quietly. This instantiates the
-- `Inst` class with a stand-in instruction and exercises the analyses on a
-- graph that has a real dominance question in it: a diamond, whose merge
-- block is dominated by neither arm.
--
-- Deliberately in `boot/` rather than in `tests/programs/`: a program there is
-- part of the conformance corpus and of the `test_boot` differential, and this
-- one exists to check a module rather than to be compiled two ways.
-- `tests/test_ssa.py` runs it.

import Turkey.Ssa
import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..), Term(..),
                   Block(..), Func(..), Inst(..), Effects(..))

-- A stand-in instruction, enough to have uses and defs.
type Toy = Toy {
    name   : String,
    uses_  : Array Value,
    defs_  : Array Value,
    effect : Effects,
}

instance Inst Toy {
    fun uses(t) = t.uses_
    fun defs(t) = t.defs_
    fun effects(t) = t.effect
    fun render(t) {
        let b = String.builder()
        for var i = 0; i < len(t.defs_); i = i + 1 {
            if i > 0 { String.push(b, ", ") }
            String.push(b, "%" + Int.toString(Ssa.valueId(t.defs_[i])))
        }
        if len(t.defs_) > 0 { String.push(b, " = ") }
        String.push(b, t.name)
        for u in t.uses_ {
            String.push(b, " %" + Int.toString(Ssa.valueId(u)))
        }
        String.build(b)
    }
}

fun toy(name : String, uses_ : Array Value, defs_ : Array Value) -> Toy = Toy {
    name = name, uses_ = uses_, defs_ = defs_, effect = Ssa.pureEffect(),
}

fun main() {
    let i64 = Ssa.untraced(I64)
    let i1 = Ssa.untraced(I1)
    let f = Ssa.newFunc("diamond", i64)

    --      b0
    --     /  \
    --    b1  b2
    --     \  /
    --      b3(x)
    -- `b3` is dominated by `b0` and by neither arm.
    for _ in [0, 1, 2, 3] { Array.push(f.blocks, Ssa.newBlock()) }

    let cond = Ssa.freshValue(f, i1)
    let one = Ssa.freshValue(f, i64)
    let two = Ssa.freshValue(f, i64)
    let merged = Ssa.freshValue(f, i64)

    Array.push(f.blocks[0].insts, toy("mkcond", Array.new(0), [cond]))
    f.blocks[0].term = Branch(cond, BlockId(1), BlockId(2))

    Array.push(f.blocks[1].insts, toy("const1", Array.new(0), [one]))
    f.blocks[1].term = Jump(BlockId(3), [one])

    Array.push(f.blocks[2].insts, toy("const2", Array.new(0), [two]))
    f.blocks[2].term = Jump(BlockId(3), [two])

    Array.push(f.blocks[3].params, merged)
    f.blocks[3].term = Ret(merged)

    print(Ssa.showFunc(f))

    let idom = Ssa.dominators(f)
    print("idom: " + String.join(Array.map(idom, Int.toString), " "))
    print("b0 dom b3: " + showBool(Ssa.dominates(idom, BlockId(0), BlockId(3))))
    print("b1 dom b3: " + showBool(Ssa.dominates(idom, BlockId(1), BlockId(3))))
    print("b1 dom b1: " + showBool(Ssa.dominates(idom, BlockId(1), BlockId(1))))

    let clean = Ssa.verify(f)
    print("verify (expect none): " + Int.toString(len(clean)))
    for m in clean { print("  " + m) }

    -- Now break it: use `one`, defined in b1, from b2, which b1 does not
    -- dominate. This is the check the Python backend's block-local rule
    -- cannot make.
    Array.push(f.blocks[2].insts, toy("use", [one], Array.new(0)))
    let broken = Ssa.verify(f)
    print("verify (expect a dominance complaint): " + Int.toString(len(broken)))
    for m in broken { print("  " + m) }
}

fun showBool(b : Bool) -> String = if b { "yes" } else { "no" }
