module Turkey.SsaLower (
    Unsupported(..), lowerBind, lowerProgram,
)

-- Core to the low IR (`NATIVE-BACKEND.md`, M27 phase 1).
--
-- The *logic* of `turkey/backend_lower.py` is what carries across -- what a
-- constructor costs, what a join is, where a value is held -- and its IR is
-- not. Two things follow from that IR being different, and both make this
-- shorter than its ancestor.
--
-- **Destination passing, into a block parameter.** An expression is lowered
-- *towards* somewhere: the function's return, or a block that takes its value
-- as a parameter. An `if` then needs no machinery at all -- both arms are
-- lowered towards the same destination and the merge block's parameter is
-- where they meet, which is what a phi would have been.
--
-- **No slots for what merely crosses an edge.** The Python backend's SSA is
-- block-local, so every value that outlives a branch goes through
-- `Function.slots`; here a definition dominates its uses and a slot is only
-- what a `var` genuinely needs.
--
-- Incomplete by construction, and says so: an unhandled Core form raises
-- `Unsupported` and the caller counts it. That is what makes this buildable in
-- slices against a corpus of real programs rather than in one attempt --
-- `boot ssa` reports how much of each program lowered, and the verifier
-- decides whether what did is well formed.

import Data.Map as Map
import Data.Set as Set
import Turkey.Core as Core
import Turkey.Core (Core(..), CoreKind(..), CParam(..), CBind(..), CProgram(..))
import Turkey.Decls as Decls
import Turkey.Decls (DeclTable(..))
import Turkey.Diag as Diag
import Turkey.Layout as Layout
import Turkey.LowIr as L
import Turkey.LowIr (Op(..), Low(..), BinOp(..), CmpOp(..), Callee(..),
                     Builder(..))
import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..), Term(..),
                   Func(..), Block(..))
import Turkey.Types as Ty
import Turkey.Types (TCon)
import Turkey.Ast as Ast
import Turkey.Ast (Lit(..))

-- What is not lowered yet, with the form that stopped it. Carried rather than
-- thrown so a driver can report coverage over a corpus.
type Unsupported = Unsupported { what : String }

type Lower = Lower {
    decls   : DeclTable,
    func    : Ssa.Func Low,
    b       : Builder,
    -- Local term name -> the value holding it.
    env     : Map String Value,
    -- Join name -> the block it became, and the parameters it takes.
    joins   : Map String BlockId,
    -- Top-level names, which are globals rather than locals.
    globals : Set.Set String,
    -- The layout each abstracted variable stands for, from the binding. What
    -- `Turkey.Layout` gave the copy, and what makes a representation knowable
    -- inside one.
    layouts : Map Int String,
    -- The first form that could not be lowered, if any.
    stopped : Option String,
}

-- Where an expression's value goes.
type Dest
    -- The function's result.
    = ToReturn
    -- A block taking the value as its one parameter. This is where a phi would
    -- be, and having it be a jump argument is why there is no phi.
    | ToBlock(BlockId)

-- -- representations ----------------------------------------------------------------

-- The representation a value of this type is held at.
--
-- `Turkey.Layout` already answers this, as the *name* of a layout, because
-- that name goes into a shared copy's name and therefore into the `opt`
-- golden that `test_boot` diffs. So it is not changed to answer a `Rep`; it is
-- read and translated here, which keeps the differential's spelling and this
-- module's type separate on purpose.
fun repOfName(name : String) -> Rep = match name {
    "unit" -> Ssa.untraced(Unit)
    "i1" -> Ssa.untraced(I1)
    "i8" -> Ssa.untraced(I8)
    "i32" -> Ssa.untraced(I32)
    "i64" -> Ssa.untraced(I64)
    "f64" -> Ssa.untraced(F64)
    _ -> Ssa.traced(Ptr)
}

-- What a body *holds* a value of this type at.
--
-- A type the layout pass cannot answer for is held at the uniform
-- representation, which is a traced pointer. That is the same answer
-- `held_at` gives and for the same reason -- parametricity leaves a generic
-- body able to hold a value and pass it on, and nothing else.
fun repOf(l : Lower, ty : Ty.Type) -> Rep =
    match Layout.layoutOf(l.decls, ty, l.layouts) {
        Some(name) -> repOfName(name)
        None -> Ssa.traced(Ptr)
    }

-- -- lowering -----------------------------------------------------------------------

fun lowerProgram(program : CProgram, decls : DeclTable)
        -> (Array (Ssa.Func Low), Array Unsupported) {
    let done : Array (Ssa.Func Low) = []
    let skipped : Array Unsupported = []
    let globals = Set.new()
    for bind in Array.append(program.dicts, program.binds) {
        Set.add(globals, bind.name)
    }
    for bind in Array.append(program.dicts, program.binds) {
        match lowerBind(bind, decls, globals) {
            Some(f) -> Array.push(done, f)
            None -> Array.push(skipped, Unsupported { what = bind.name })
        }
    }
    (done, skipped)
}

-- One top-level binding, or `None` if it holds a form this slice cannot lower.
fun lowerBind(bind : CBind, decls : DeclTable,
              globals : Set.Set String) -> Option (Ssa.Func Low) {
    -- Not a function: a global whose value the module initializer computes,
    -- which is its own phase. The two `let`s rather than one destructuring
    -- binding are FINDINGS 41 -- a control transfer cannot cross one.
    let shape = spine(bind.value)
    match shape {
        None -> return None
        Some(_) -> {}
    }
    let params = match shape {
        Some((ps, _)) -> ps
        None -> []
    }
    let body = match shape {
        Some((_, inner)) -> inner
        None -> bind.value
    }
    let f = Ssa.newFunc(bind.name, Ssa.traced(Ptr))
    let l = Lower {
        decls = decls, func = f, b = L.builder(f, BlockId(0)),
        env = Map.new(), joins = Map.new(), globals = globals,
        layouts = bind.layouts, stopped = None,
    }
    let entry = L.block(l.b)
    L.into(l.b, entry)
    for p in params {
        let v = Ssa.freshValue(f, repOf(l, p.ty))
        Array.push(f.params, v)
        Map.put(l.env, p.name, v)
    }
    f.result = repOf(l, body.ty)
    lower(l, body, ToReturn)
    match l.stopped {
        Some(_) -> None
        None -> Some(f)
    }
}

-- A binding's value as parameters and a body, with type abstraction erased.
fun spine(value : Core) -> Option (Array CParam, Core) = match value.kind {
    CLam(params, body, _) -> Some((params, body))
    CTyLam(_, inner) -> spine(inner)
    _ -> None
}

fun stop(l : Lower, what : String) -> Value {
    match l.stopped {
        None -> { l.stopped = Some(what) }
        Some(_) -> {}
    }
    -- A value of the right shape so the walk can finish without a second
    -- failure mode; nothing reads it, because the caller discards the whole
    -- function.
    Ssa.freshValue(l.func, Ssa.untraced(Unit))
}

-- Lower `e`, sending its value to `dest`.
fun lower(l : Lower, e : Core, dest : Dest) -> Unit {
    match l.stopped {
        Some(_) -> return
        None -> {}
    }
    match e.kind {
        CIf(cond, then_, otherwise) -> {
            let test = value(l, cond)
            let yes = L.block(l.b)
            let no = L.block(l.b)
            L.terminate(l.b, Branch(test, yes, no))
            L.into(l.b, yes)
            lower(l, then_, dest)
            L.into(l.b, no)
            match otherwise {
                Some(other) -> lower(l, other, dest)
                -- A one-armed `if` is the statement form and answers `Unit`
                -- whatever its branch does (design.md 6.7).
                None -> finish(l, L.emit(l.b, ConstUnit, Ssa.untraced(Unit)), dest)
            }
        }
        CLet(name, ty, bound, body, _) -> {
            let v = value(l, bound)
            Map.put(l.env, name, v)
            lower(l, body, dest)
        }
        -- A join is a block, and a jump to it is a jump. This is the case the
        -- whole IR is shaped for.
        CJoin(name, params, body, rest, _) -> {
            let target = L.block(l.b)
            Map.put(l.joins, name, target)
            let entered : Array Value = []
            for p in params {
                Array.push(entered, L.param(l.b, target, repOf(l, p.ty)))
            }
            -- `rest` first: it is what jumps, and the parameters have to exist
            -- before it can.
            lower(l, rest, dest)
            L.into(l.b, target)
            for var i = 0; i < len(params) && i < len(entered); i = i + 1 {
                Map.put(l.env, params[i].name, entered[i])
            }
            lower(l, body, dest)
        }
        CJump(name, args) -> {
            let supplied : Array Value = []
            for a in args { Array.push(supplied, value(l, a)) }
            match Map.get(l.joins, name) {
                Some(target) -> L.terminate(l.b, Jump(target, supplied))
                None -> {
                    let _ = stop(l, "a jump to an unknown join")
                }
            }
        }
        _ -> finish(l, value(l, e), dest)
    }
}

fun finish(l : Lower, v : Value, dest : Dest) -> Unit = match dest {
    ToReturn -> L.terminate(l.b, Ret(v))
    ToBlock(target) -> L.terminate(l.b, Jump(target, [v]))
}

-- Lower an expression that yields a value in the current block.
fun value(l : Lower, e : Core) -> Value {
    match l.stopped {
        Some(_) -> return Ssa.freshValue(l.func, Ssa.untraced(Unit))
        None -> {}
    }
    let rep = repOf(l, e.ty)
    match e.kind {
        CUnit -> L.emit(l.b, ConstUnit, Ssa.untraced(Unit))
        CLit(lit) -> literal(l, lit, rep)
        CVar(name) -> match Map.get(l.env, name) {
            Some(v) -> v
            None -> if Set.contains(l.globals, name) {
                L.emit(l.b, GlobalLoad(name), rep)
            } else {
                stop(l, "an unbound name '" + name + "'")
            }
        }
        CTyApp(inner, _) -> value(l, inner)
        CTyLam(_, inner) -> value(l, inner)
        CRef(inner) -> L.emit(l.b, CellNew(value(l, inner)), Ssa.traced(Ptr))
        CDeref(inner) -> L.emit(l.b, CellLoad(value(l, inner)), rep)
        CProject(target, index) ->
            L.emit(l.b, ObjectGet(value(l, target), index), rep)
        CField(target, name) -> {
            let object = value(l, target)
            match fieldIndex(l, target.ty, name) {
                Some(index) -> L.emit(l.b, ObjectGet(object, index), rep)
                None -> stop(l, "a field of a type with no known layout")
            }
        }
        CApp(fn, args) -> call(l, e, fn, args, rep)
        _ -> stop(l, formOf(e))
    }
}

fun literal(l : Lower, lit : Lit, rep : Rep) -> Value = match lit {
    LInt(n) -> L.emit(l.b, ConstInt(n), rep)
    LFloat(x) -> L.emit(l.b, ConstFloat(x), rep)
    LString(s) -> L.emit(l.b, ConstString(s), Ssa.traced(Ptr))
    LChar(c) -> L.emit(l.b, ConstInt(Char.toInt(c)), rep)
}

-- A call. The callee decides which of three shapes it is, and the arguments
-- are lowered left to right in every one, because a call's arguments are
-- evaluated in that order and this is the phase where that stops being
-- implicit.
fun call(l : Lower, e : Core, fn : Core, args : Array Core, rep : Rep) -> Value {
    let supplied : Array Value = []
    match fn.kind {
        CPrim(name) -> {
            for a in args { Array.push(supplied, value(l, a)) }
            primitive(l, name, supplied, rep)
        }
        CVar(name) -> if Set.contains(l.globals, name) {
            for a in args { Array.push(supplied, value(l, a)) }
            L.emit(l.b, Call(Direct(name), supplied), rep)
        } else {
            let closure = value(l, fn)
            for a in args { Array.push(supplied, value(l, a)) }
            L.emit(l.b, Call(Indirect(closure), supplied), rep)
        }
        _ -> stop(l, "a call to " + formOf(fn))
    }
}

-- A primitive whose machine operation this instruction set has, or a runtime
-- call. The split is the one `NATIVE-BACKEND.md` argues for: a constructor
-- where instruction selection must match, and a symbol where it need not.
fun primitive(l : Lower, name : String, args : Array Value, rep : Rep) -> Value {
    match binOp(name) {
        Some(o) -> if len(args) == 2 {
            return L.emit(l.b, Bin(o, args[0], args[1]), rep)
        }
        None -> {}
    }
    match cmpOp(name) {
        Some(o) -> if len(args) == 2 {
            return L.emit(l.b, Cmp(o, args[0], args[1]), Ssa.untraced(I1))
        }
        None -> {}
    }
    if name == "Prim.intNeg" && len(args) == 1 {
        return L.emit(l.b, Neg(args[0]), rep)
    }
    if name == "Prim.not" && len(args) == 1 {
        return L.emit(l.b, Not(args[0]), rep)
    }
    L.emit(l.b, Call(Runtime(name), args), rep)
}

fun binOp(name : String) -> Option BinOp = match name {
    "Prim.intAdd" -> Some(Add)
    "Prim.intSub" -> Some(Sub)
    "Prim.intMul" -> Some(Mul)
    "Prim.intDiv" -> Some(Div)
    "Prim.intRem" -> Some(Rem)
    "Prim.intAnd" -> Some(And)
    "Prim.intOr" -> Some(Or)
    "Prim.intXor" -> Some(Xor)
    "Prim.intShl" -> Some(Shl)
    "Prim.intShr" -> Some(Shr)
    _ -> None
}

fun cmpOp(name : String) -> Option CmpOp = match name {
    "Prim.intEq" -> Some(Eq)
    "Prim.intLt" -> Some(Lt)
    "Prim.byteEq" -> Some(Eq)
    "Prim.byteLt" -> Some(Lt)
    "Prim.charEq" -> Some(Eq)
    "Prim.charLt" -> Some(Lt)
    _ -> None
}

fun fieldIndex(l : Lower, ty : Ty.Type, name : String) -> Option Int {
    let (h, _) = Ty.spine(Ty.prune(ty))
    match h {
        TCon(c) -> match Decls.recordFields(l.decls, c.name) {
            Some(names) -> Array.indexOf(names, name)
            None -> None
        }
        _ -> None
    }
}

-- The name of a Core form, for reporting what is not lowered yet. Not
-- `Core.showExpr`, which wants a namer and an alias table and would print a
-- whole subtree where a word is wanted.
fun formOf(e : Core) -> String = match e.kind {
    CLit(_) -> "a literal"
    CUnit -> "unit"
    CVar(_) -> "a variable"
    CCon(name) -> "the constructor '" + name + "'"
    CPrim(name) -> "the primitive '" + name + "'"
    CTuple(_) -> "a tuple"
    CArray(_) -> "an array literal"
    CRecord(_, _) -> "a record"
    CField(_, _) -> "a field"
    CProject(_, _) -> "a projection"
    CIndex(_, _) -> "an index"
    CLam(_, _, _) -> "a lambda"
    CApp(_, _) -> "an application"
    CTyLam(_, _) -> "a type abstraction"
    CTyApp(_, _) -> "a type application"
    CLet(_, _, _, _, _) -> "a let"
    CLetRec(_, _) -> "a letrec"
    CJoin(_, _, _, _, _) -> "a join"
    CJump(_, _) -> "a jump"
    CRef(_) -> "a ref"
    CDeref(_) -> "a deref"
    CAssign(_, _) -> "an assignment"
    CIf(_, _, _) -> "an if"
    CMatch(_, _) -> "a match"
}
