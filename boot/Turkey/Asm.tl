module Turkey.Asm (
    Asm(..), newAsm, symbolFor, runtimeFor, messageSymbol, literalSlot, globalSlot,
    globalSymbol, rootsSymbol, literalName, messageName,
)

-- What the arm64 module owns: the names, and who gets a root slot
-- (`NATIVE-BACKEND.md`, M28 phase 4).
--
-- This exists before the module emitter that will use it, and deliberately so.
-- Instruction selection cannot emit a string literal, a global or a panic
-- message without naming a symbol, and a name invented at the point of use is
-- a name a second place has to reproduce by convention. That shape is what
-- FINDINGS 60, 66 and 71 all are, so the naming is written down once, here,
-- and the emitter will read it rather than agree with it.
--
-- Nothing here is shared with `Turkey.Llvm`, and that is correct rather than an
-- oversight: the two backends emit separate object files that are never linked
-- together, so their private symbols and their root arrays need not -- and
-- should not -- agree. What must agree is *this* module and the arm64 emitter,
-- which is why they are one file's worth of decisions rather than two.

import Data.Map as Map
import Turkey.LowIr as L
import Turkey.LowIr (Op(..))
import Turkey.Ssa as Ssa
import Turkey.Ssa (Rep(..), Func(..))
import Turkey.SsaLower as SsaLower
import Turkey.SsaLower (Module(..))

type Asm = Asm {
    -- String literal -> its index. Interned per module, so `ConstString` is a
    -- load of an already-decided cache and not an allocation.
    literals : Map String Int,
    -- Panic message -> its index.
    messages : Map String Int,
    -- Traced global -> its slot in the permanent root array.
    globals  : Map String Int,
    -- How many slots that array has: the traced globals, then one cache per
    -- literal.
    roots    : Int,
}

-- A Turkey name as an assembler symbol.
--
-- **Quoted, and it has to be.** A Turkey name contains `#`, which is ARM
-- assembly's immediate prefix -- `bl _Main#main` is "unexpected token in
-- argument list" and not a call. Quoting takes the whole name through to the
-- object file's symbol table intact, which is the same answer `Turkey.Llvm`
-- reached with `@"..."` for a different reason: there, the character LLVM
-- refuses; here, the character the assembler reads as syntax.
--
-- The alternative is mangling, and it is worse for the reason it is always
-- worse: a crash report or an `nm` then shows a name the compiler invented
-- rather than the one the programmer wrote.
fun symbolFor(name : String) -> String = "\"_" + name + "\""

-- A runtime entry point. A C identifier needs no quoting, but gets it anyway:
-- one rule for how a symbol is written beats two and a test for which applies.
-- The leading underscore is Mach-O's, and is the same underscore
-- `symbolFor` adds -- `turkey_print` in the C source is `_turkey_print` in the
-- object file.
fun runtimeFor(name : String) -> String = symbolFor(name)

-- Compiler-private data. Named with the `L` prefix Mach-O treats as local, so
-- these never reach the symbol table.
fun literalName(index : Int) -> String = "Lstr" + Int.toString(index)
fun messageName(index : Int) -> String = "Lmsg" + Int.toString(index)

-- The permanent root array, which the collector scans for the whole run.
let rootsSymbol = "_turkey_module_roots"

-- An untraced global is ordinary storage and gets its own symbol; a traced one
-- lives *in* the root array instead, so that storing to it and rooting it are
-- the same store.
fun globalSymbol(name : String) -> String = "\"_global." + name + "\""

fun globalSlot(a : Asm, name : String) -> Option Int = Map.get(a.globals, name)

-- The message's symbol, interning it if this is the first mention.
fun messageSymbol(a : Asm, text : String) -> String {
    if !Map.contains(a.messages, text) {
        Map.put(a.messages, text, len(a.messages))
    }
    messageName(Map.getOr(a.messages, text, 0))
}

-- The root slot holding this literal's `TurkeyString`.
--
-- Every literal is interned before anything is selected, so this is a lookup
-- and not an assignment: a slot decided while emitting would depend on the
-- order functions were walked in, and two runs would disagree.
fun literalSlot(a : Asm, text : String) -> Int =
    Map.getOr(a.literals, text, 0) + len(a.globals)

-- The module's naming, decided up front.
--
-- The traced globals take the low slots and the literal caches the rest. Both
-- are roots for the same reason and for the whole run: a dictionary is a
-- global nothing holds in a stack frame, and a literal's `TurkeyString` is
-- allocated once by the entry sequence and never again -- a collector that
-- cannot see either frees them the first time it runs.
--
-- A scalar global holds nothing to trace and stays ordinary storage, which
-- also keeps the array dense: its length is the number of things the collector
-- must scan, not the number of globals.
fun newAsm(m : Module) -> Asm {
    let globals = Map.new()
    var roots = 0
    for (name, r) in m.globals {
        if r.traced {
            Map.put(globals, name, roots)
            roots = roots + 1
        }
    }
    let literals = Map.new()
    for f in m.functions {
        for block in f.blocks {
            for i in block.insts {
                match i.op {
                    ConstString(text) -> if !Map.contains(literals, text) {
                        Map.put(literals, text, len(literals))
                    }
                    _ -> {}
                }
            }
        }
    }
    Asm { literals = literals, messages = Map.new(), globals = globals,
          roots = roots + len(literals) }
}
