module Turkey.SsaLower (
    Unsupported(..), lowerBind, lowerProgram, tagsOf,
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
import Turkey.LowIr (Op(..), Low(..), BinOp(..), CmpOp(..), Conv(..),
                     Callee(..), Builder(..))
import Turkey.Prelude as P
import Turkey.Ssa as Ssa
import Turkey.Ssa (Value(..), BlockId(..), Rep(..), RepClass(..), Term(..),
                   Func(..), Block(..))
import Turkey.Types as Ty
import Turkey.Types (TCon, TTuple, TFun, TBottom)
import Turkey.Ast as Ast
import Turkey.Ast (Lit(..), Pat(..), PatKind(..), patternVars)

-- What is not lowered yet: the binding, and the form that stopped it.
-- Carried rather than thrown so a driver can report coverage over a corpus --
-- and the *reason* rather than only the count, because a histogram of reasons
-- is what says which form to write next.
type Unsupported = Unsupported { name : String, what : String }

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
    -- The runtime tag of each constructor. A whole-program numbering, so it is
    -- computed once and handed down rather than derived per function.
    tags    : Map String Int,
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
    let tags = tagsOf(decls)
    for bind in Array.append(program.dicts, program.binds) {
        let (f, why) = lowerBind(bind, decls, globals, tags)
        -- A binding with no reason is not a function at all: a global whose
        -- value the module initializer computes, which is its own phase.
        let reason = match why {
            Some(w) -> w
            None -> "not a function"
        }
        match f {
            Some(function) -> Array.push(done, function)
            None -> Array.push(skipped,
                               Unsupported { name = bind.name, what = reason })
        }
    }
    (done, skipped)
}

-- Every constructor's runtime tag: the whole program's constructor names in
-- order, numbered from zero.
--
-- Sorted rather than in declaration order, because declaration order is the
-- order the modules happened to be read in and a tag is written into a heap
-- object that outlives the compilation that made it. Nothing reads a tag by
-- name at run time; what matters is that one compilation agrees with itself,
-- and that the numbering does not move when an unrelated module gains a type.
fun tagsOf(decls : DeclTable) -> Map String Int {
    let names = Array.sort(Map.keys(decls.constructors))
    let out = Map.new()
    for var i = 0; i < len(names); i = i + 1 { Map.put(out, names[i], i) }
    out
}

-- One top-level binding: the function it became, or the form that stopped it.
fun lowerBind(bind : CBind, decls : DeclTable, globals : Set.Set String,
              tags : Map String Int)
        -> (Option (Ssa.Func Low), Option String) {
    -- Not a function: a global whose value the module initializer computes,
    -- which is its own phase. The two `let`s rather than one destructuring
    -- binding are FINDINGS 41 -- a control transfer cannot cross one.
    let shape = spine(bind.value)
    match shape {
        None -> return (None, None)
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
        tags = tags, layouts = bind.layouts, stopped = None,
    }
    let entry = L.block(l.b)
    L.into(l.b, entry)
    for p in params {
        let v = Ssa.freshValue(f, repOf(l, p.ty))
        Array.push(f.params, v)
        Map.put(l.env, p.name, v)
    }
    -- From the binding's own type rather than from the body: a body that
    -- always diverges is typed bottom, and a caller reads the signature.
    f.result = match Ty.prune(resultType(bind.ty)) {
        TBottom -> repOf(l, body.ty)
        other -> repOf(l, other)
    }
    lower(l, body, ToReturn)
    match l.stopped {
        Some(why) -> (None, Some(why))
        None -> (Some(f), None)
    }
}

-- A binding's value as parameters and a body, with type abstraction erased.
fun spine(value : Core) -> Option (Array CParam, Core) = match value.kind {
    CLam(params, body, _) -> Some((params, body))
    CTyLam(_, inner) -> spine(inner)
    _ -> None
}

-- -- scope ------------------------------------------------------------------------

-- What `l.env` says about these names now, so it can be put back.
--
-- Core names are not unique. Monomorphization and inlining copy bodies, so two
-- unrelated bindings called `i` in one function are ordinary, and an
-- environment that only ever grows makes the second one read the first one's
-- value -- a capture the verifier catches as a dominance violation rather than
-- as a wrong answer, which is the argument for running it after every pass.
--
-- Saving the affected names rather than copying the map: a scope binds a
-- handful of names and the map holds every local in the function.
fun saving(l : Lower, names : Array String) -> Array (String, Option Value) {
    let out = []
    for name in names { Array.push(out, (name, Map.get(l.env, name))) }
    out
}

fun restore(l : Lower, saved : Array (String, Option Value)) -> Unit {
    for (name, was) in saved {
        match was {
            Some(v) -> Map.put(l.env, name, v)
            None -> {
                let _ = Map.delete(l.env, name)
            }
        }
    }
}

-- A function type's result, past the type abstraction.
fun resultType(ty : Ty.Type) -> Ty.Type = match Ty.prune(ty) {
    TFun(_, ret) -> ret
    other -> other
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
            let was = saving(l, [name])
            Map.put(l.env, name, v)
            lower(l, body, dest)
            restore(l, was)
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
            let was = saving(l, Array.map(params, fun(p) = p.name))
            for var i = 0; i < len(params) && i < len(entered); i = i + 1 {
                Map.put(l.env, params[i].name, entered[i])
            }
            lower(l, body, dest)
            restore(l, was)
        }
        CJump(name, args) -> {
            let supplied : Array Value = []
            for a in args { Array.push(supplied, value(l, a)) }
            match Map.get(l.joins, name) {
                Some(target) -> {
                    let passed : Array Value = []
                    for var i = 0; i < len(supplied); i = i + 1 {
                        Array.push(passed,
                                   coerce(l, supplied[i], wanted(l, target, i)))
                    }
                    L.terminate(l.b, Jump(target, passed))
                }
                None -> {
                    let _ = stop(l, "a jump to an unknown join")
                }
            }
        }
        -- A match is a chain of tests, and the arms all lower towards the
        -- same destination. This is where destination passing earns the most:
        -- the Python backend materializes the arm's value into a block
        -- parameter and then into a slot, because its SSA is block-local;
        -- here the arm simply jumps to where the value was wanted.
        CMatch(scrutinee, alts) -> {
            let v = value(l, scrutinee)
            let failed = L.block(l.b)
            -- The first test happens where the scrutinee was computed, so
            -- every later test block is reached only through this one and the
            -- scrutinee dominates all of them.
            var test = l.b.at
            for var i = 0; i < len(alts); i = i + 1 {
                let success = L.block(l.b)
                let failure = if i + 1 == len(alts) { failed } else { L.block(l.b) }
                L.into(l.b, test)
                let was = saving(l, patternVars(alts[i].pat))
                pattern(l, alts[i].pat, v, scrutinee.ty, success, failure)
                L.into(l.b, success)
                lower(l, alts[i].body, dest)
                restore(l, was)
                test = failure
            }
            L.into(l.b, failed)
            -- Exhaustiveness has already decided this is unreachable. It is
            -- emitted anyway because the *lowering* must not be the thing that
            -- assumes so: a bug in an earlier pass then stops here with a name
            -- rather than falling off the end of a block.
            let message = L.emit(l.b, ConstString("no match arm applied"),
                                 Ssa.traced(Ptr))
            L.terminate(l.b, Panic(message))
        }
        _ -> finish(l, value(l, e), dest)
    }
}

-- -- patterns ----------------------------------------------------------------------

-- Test `p` against `v`, binding what it binds, and terminate the current block
-- into `success` or `failure`.
--
-- No slots and no re-loading. `turkey/backend_lower.py` stores the scrutinee
-- into a slot and loads it again in every test block, because a value there
-- does not survive a branch; here it does, so a sub-pattern reads the value
-- the test above it already has.
fun pattern(l : Lower, p : Pat, v : Value, ty : Ty.Type,
            success : BlockId, failure : BlockId) -> Unit {
    match l.stopped {
        Some(_) -> return
        None -> {}
    }
    match p.kind {
        PAnnot(inner, _) -> pattern(l, inner, v, ty, success, failure)
        PWild -> L.terminate(l.b, Jump(success, []))
        PVar(name) -> {
            Map.put(l.env, name, v)
            L.terminate(l.b, Jump(success, []))
        }
        PLit(lit) -> {
            let test = literalTest(l, lit, v)
            L.terminate(l.b, Branch(test, success, failure))
        }
        PTuple(elems) -> {
            let elemTypes = match Ty.prune(ty) {
                TTuple(ts) -> ts
                _ -> []
            }
            let pieces = []
            for var i = 0; i < len(elems) && i < len(elemTypes); i = i + 1 {
                Array.push(pieces, (i, elems[i], elemTypes[i]))
            }
            destructure(l, v, pieces, success, failure)
        }
        PRecord(name, fields) -> {
            let order = match Decls.con(l.decls, name) {
                Some(info) -> match info.fields {
                    Some(names) -> names
                    None -> []
                }
                None -> []
            }
            let types = match Decls.conFieldTypes(l.decls, ty, name) {
                Some(ts) -> ts
                None -> []
            }
            let pieces = []
            for (label, sub) in fields {
                match Array.indexOf(order, label) {
                    Some(i) -> if i < len(types) {
                        Array.push(pieces, (i, sub, types[i]))
                    }
                    None -> {}
                }
            }
            tested(l, v, name, ty, pieces, success, failure)
        }
        PCon(name, args) -> {
            if name == P.boolFalse || name == P.boolTrue {
                let want = L.emit(l.b, ConstInt(if name == P.boolTrue { 1 } else { 0 }),
                                  Ssa.untraced(I1))
                let test = L.emit(l.b, Cmp(Eq, v, want), Ssa.untraced(I1))
                L.terminate(l.b, Branch(test, success, failure))
                return
            }
            -- The wrapper is not there, so there is no tag to test and nothing
            -- to take out: the value in hand already *is* the payload. See
            -- `Decls.newtypes`.
            match Decls.erasedPayload(l.decls, ty) {
                Some(payload) -> if len(args) == 1 {
                    pattern(l, args[0], v, payload, success, failure)
                    return
                }
                None -> {}
            }
            let types = match Decls.conFieldTypes(l.decls, ty, name) {
                Some(ts) -> ts
                None -> []
            }
            let pieces = []
            for var i = 0; i < len(args) && i < len(types); i = i + 1 {
                Array.push(pieces, (i, args[i], types[i]))
            }
            tested(l, v, name, ty, pieces, success, failure)
        }
    }
}

-- The tag test a constructor pattern needs, and then its contents.
--
-- A type with one constructor has nothing to distinguish, and reading the tag
-- to compare it against the only value it can hold costs a load, a compare and
-- a branch on the hot path -- `Array a = Array(ArrayStorage a)` would pay it
-- on every element access. Exhaustiveness already knows the match is total;
-- this is the lowering agreeing with it.
fun tested(l : Lower, v : Value, name : String, ty : Ty.Type,
           pieces : Array (Int, Pat, Ty.Type),
           success : BlockId, failure : BlockId) -> Unit {
    if Decls.variantCount(l.decls, name) > 1 {
        match Map.get(l.tags, name) {
            Some(tag) -> {
                let seen = L.emit(l.b, ObjectTag(v), Ssa.untraced(I32))
                let want = L.emit(l.b, ConstInt(tag), Ssa.untraced(I32))
                let test = L.emit(l.b, Cmp(Eq, seen, want), Ssa.untraced(I1))
                let contents = L.block(l.b)
                L.terminate(l.b, Branch(test, contents, failure))
                L.into(l.b, contents)
            }
            None -> {
                let _ = stop(l, "the constructor '" + name + "' has no tag")
                return
            }
        }
    }
    destructure(l, v, pieces, success, failure)
}

-- Read each field and match its sub-pattern, in a chain: a sub-pattern may
-- branch, so what follows it is a block rather than the next instruction.
fun destructure(l : Lower, object : Value, pieces : Array (Int, Pat, Ty.Type),
                success : BlockId, failure : BlockId) -> Unit {
    if len(pieces) == 0 {
        L.terminate(l.b, Jump(success, []))
        return
    }
    var at = l.b.at
    for var i = 0; i < len(pieces); i = i + 1 {
        let (index, sub, fieldTy) = pieces[i]
        L.into(l.b, at)
        let loaded = L.emit(l.b, ObjectGet(object, index), repOf(l, fieldTy))
        let following = if i + 1 == len(pieces) { success } else { L.block(l.b) }
        pattern(l, sub, loaded, fieldTy, following, failure)
        at = following
    }
}

-- A literal pattern's test. A string is a runtime call and everything else is
-- a machine comparison; a float comparison is the same constructor with `f64`
-- operands, which is what instruction selection reads it off.
fun literalTest(l : Lower, lit : Lit, v : Value) -> Value {
    let rep = Ssa.repOf(l.func, v)
    match lit {
        LString(text) -> {
            let want = L.emit(l.b, ConstString(text), Ssa.traced(Ptr))
            L.emit(l.b, Call(Runtime("Prim.stringEq"), [v, want]),
                   Ssa.untraced(I1))
        }
        _ -> {
            let want = literal(l, lit, rep)
            L.emit(l.b, Cmp(Eq, v, want), Ssa.untraced(I1))
        }
    }
}

fun finish(l : Lower, v : Value, dest : Dest) -> Unit = match dest {
    ToReturn -> L.terminate(l.b, Ret(coerce(l, v, l.func.result)))
    ToBlock(target) -> {
        let want = wanted(l, target, 0)
        L.terminate(l.b, Jump(target, [coerce(l, v, want)]))
    }
}

-- The representation a block's nth parameter is held at.
fun wanted(l : Lower, target : BlockId, index : Int) -> Rep {
    let params = Ssa.blockAt(l.func, target).params
    if index < len(params) { Ssa.repOf(l.func, params[index]) }
    else { Ssa.traced(Ptr) }
}

-- -- representation boundaries -------------------------------------------------------

-- Make `v` usable where `want` is required.
--
-- Parametricity is what creates these. A body that is polymorphic in a type
-- holds its values at the uniform representation and can do nothing with them
-- but pass them on, so a value crossing between a generic context and a
-- concrete one changes representation without changing meaning -- and a value
-- whose type is a variable instantiated to a concrete one arrives here.
--
-- Every conversion is explicit in the IR rather than implied by a `Rep`
-- mismatch that later passes must remember to notice, which is the same
-- argument `Rep` itself makes for carrying the collector's obligation.
fun coerce(l : Lower, v : Value, want : Rep) -> Value {
    let have = Ssa.repOf(l.func, v)
    if have == want { return v }
    -- Zero width: there is nothing to convert, and nothing reads it.
    if want.class_ == Unit { return L.emit(l.b, ConstUnit, want) }
    if have.class_ == Unit { return zero(l, want) }
    if have.class_ != Ptr && want.class_ == Ptr {
        return retagged(l, L.emit(l.b, Box(v), Ssa.traced(Ptr)), want)
    }
    if have.class_ == Ptr && want.class_ != Ptr {
        return L.emit(l.b, Unbox(v), want)
    }
    if have.class_ == Ptr && want.class_ == Ptr { return retagged(l, v, want) }
    -- Two integer widths, or an integer and a float. Which of the two it is,
    -- the constructor says; the target width is the result's own `Rep`.
    if have.class_ == F64 { return L.emit(l.b, Convert(FloatToInt, v), want) }
    if want.class_ == F64 { return L.emit(l.b, Convert(IntToFloat, v), want) }
    let grows = width(want) >= width(have)
    L.emit(l.b, Convert(if grows { Widen } else { Narrow }, v), want)
}

-- The same machine value with a different obligation on the collector.
fun retagged(l : Lower, v : Value, want : Rep) -> Value =
    if Ssa.repOf(l.func, v) == want { v }
    else { L.emit(l.b, Convert(Retag, v), want) }

fun width(r : Rep) -> Int = match r.class_ {
    Unit -> 0
    I1 -> 1
    I8 -> 1
    I32 -> 4
    _ -> 8
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
            } else if isPrim(name) {
                -- Referred to but not called: it needs a closure around it,
                -- which is closure conversion's job and not this slice's.
                stop(l, "the primitive '" + name + "' used as a value")
            } else {
                stop(l, "an unbound name '" + name + "'")
            }
        }
        CTyApp(inner, _) -> value(l, inner)
        CTyLam(_, inner) -> value(l, inner)
        CRef(inner) -> L.emit(l.b, CellNew(value(l, inner)), Ssa.traced(Ptr))
        CDeref(inner) -> L.emit(l.b, CellLoad(value(l, inner)), rep)
        -- An erased wrapper has nothing to read out of: the value in hand
        -- already is the payload, the way a pattern on one binds it directly.
        CProject(target, index) -> match Decls.erasedPayload(l.decls, target.ty) {
            Some(_) -> value(l, target)
            None -> L.emit(l.b, ObjectGet(value(l, target), index), rep)
        }
        CField(target, name) -> match Decls.erasedPayload(l.decls, target.ty) {
            Some(_) -> value(l, target)
            None -> {
                let object = value(l, target)
                match fieldIndex(l, target.ty, name) {
                    Some(index) -> L.emit(l.b, ObjectGet(object, index), rep)
                    None -> stop(l, "a field of a type with no known layout")
                }
            }
        }
        CIndex(target, index) -> {
            let array = value(l, target)
            L.emit(l.b, ArrayGet(array, value(l, index)), rep)
        }
        -- Core assignment evaluates the value before its target, and the
        -- target decides which of three stores it is: all three are assignment
        -- in the surface language and all three are reference semantics.
        CAssign(target, assigned) -> {
            let x = value(l, assigned)
            store(l, target, x)
            L.emit(l.b, ConstUnit, Ssa.untraced(Unit))
        }
        CTuple(elems) -> aggregate(l, elems, 0, -1)
        CRecord(con, fields) -> {
            let order = match Decls.con(l.decls, con) {
                Some(info) -> match info.fields {
                    Some(names) -> names
                    None -> []
                }
                None -> []
            }
            -- Declaration order, not the order they were written in: the
            -- object's shape belongs to the type.
            let sorted = []
            for name in order {
                for (label, x) in fields {
                    if label == name { Array.push(sorted, x) }
                }
            }
            let items = if len(sorted) == len(fields) { sorted }
                        else { Array.map(fields, fun(f) = f.1) }
            aggregate(l, items, 1, Map.getOr(l.tags, con, -1))
        }
        CArray(elems) -> arrayLiteral(l, e, elems)
        CCon(name) -> constructor(l, e, name, [], rep)
        -- A call whose result type is bottom does not come back, so what
        -- follows it is unreachable rather than merely dead. Saying so keeps
        -- a `ret` or a jump from being built out of a value that never
        -- arrives -- which is what the verifier was reporting as a
        -- representation disagreement.
        CApp(fn, args) -> {
            let called = call(l, e, fn, args, rep)
            match Ty.prune(e.ty) {
                TBottom -> {
                    L.terminate(l.b, Unreachable)
                    L.into(l.b, L.block(l.b))
                    zero(l, rep)
                }
                _ -> called
            }
        }
        -- A form that is not a value in itself: it is control flow that
        -- *arrives* at one. Giving it a block whose parameter is where its
        -- value lands is the whole of it -- the same mechanism `if` already
        -- uses for its two arms, applied one level up.
        CIf(_, _, _) -> control(l, e, rep)
        CMatch(_, _) -> control(l, e, rep)
        CLet(_, _, _, _, _) -> control(l, e, rep)
        CJoin(_, _, _, _, _) -> control(l, e, rep)
        _ -> stop(l, formOf(e))
    }
}

-- An expression that is control flow, used where a value was wanted.
--
-- `lower` already sends a value to a destination; this makes a destination out
-- of a fresh block and reads the value back out of its parameter. Nothing else
-- is needed, and in particular no slot: the parameter is the value.
fun control(l : Lower, e : Core, rep : Rep) -> Value {
    let merge = L.block(l.b)
    let arrived = L.param(l.b, merge, rep)
    lower(l, e, ToBlock(merge))
    L.into(l.b, merge)
    arrived
}

-- The three shapes of assignment target.
fun store(l : Lower, target : Core, x : Value) -> Unit = match target.kind {
    CIndex(array, index) -> {
        let a = value(l, array)
        L.effect(l.b, ArraySet(a, value(l, index), x))
    }
    CField(receiver, name) -> {
        let object = value(l, receiver)
        match fieldIndex(l, receiver.ty, name) {
            Some(index) -> L.effect(l.b, ObjectSet(object, index, x))
            None -> {
                let _ = stop(l, "an assignment to a field with no known layout")
            }
        }
    }
    -- Anything else is a reference cell: a `var`, whose mentions are already
    -- `CDeref` and whose assignment is a store through the cell itself.
    _ -> L.effect(l.b, CellStore(value(l, target), x))
}

-- A heap object with `count` fields, built and then filled.
--
-- `kind` is 0 for a tuple and 1 for a constructed value, which is what the
-- runtime's own object header distinguishes.
fun aggregate(l : Lower, items : Array Core, kind : Int, tag : Int) -> Value {
    let values = []
    for x in items { Array.push(values, value(l, x)) }
    let reps = Array.map(values, fun(x) = Ssa.repOf(l.func, x))
    let made = L.emit(l.b, ObjectNew(kind, tag, len(values), metadata(reps)),
                      Ssa.traced(Ptr))
    for var i = 0; i < len(values); i = i + 1 {
        L.effect(l.b, ObjectSet(made, i, values[i]))
    }
    made
}

-- How the collector reads an object's fields: three bits of representation
-- each, least significant field first. Not a pointer bitmap -- a width and a
-- register class are needed too, and one code answers all three.
fun metadata(reps : Array Rep) -> Int {
    var out = 0
    for var i = 0; i < len(reps); i = i + 1 {
        out = out + Int.shl(repCode(reps[i]), 3 * i)
    }
    out
}

fun repCode(r : Rep) -> Int = match r.class_ {
    Unit -> 0
    I1 -> 1
    I8 -> 2
    I32 -> 3
    I64 -> 4
    F64 -> 5
    Ptr -> if r.traced { 7 } else { 6 }
}

-- An array literal. The element representation decides the stride, so it comes
-- from the type rather than from what happens to be in the first slot.
fun arrayLiteral(l : Lower, e : Core, elems : Array Core) -> Value {
    let (_, args) = Ty.spine(Ty.prune(e.ty))
    let element = if len(args) > 0 { repOf(l, args[0]) } else { Ssa.traced(Ptr) }
    let values = []
    for x in elems { Array.push(values, value(l, x)) }
    let length = L.emit(l.b, ConstInt(len(values)), Ssa.untraced(I64))
    -- `ArrayNew` wants something to fill with. An array with elements is
    -- filled over anyway, and an empty one has no slot to read, so a zero of
    -- the element's own representation is enough in both cases.
    let initial = if len(values) > 0 { values[0] }
                  else { zero(l, element) }
    let made = L.emit(l.b, ArrayNew(length, initial, element.class_),
                      Ssa.traced(Ptr))
    for var i = 1; i < len(values); i = i + 1 {
        let at = L.emit(l.b, ConstInt(i), Ssa.untraced(I64))
        L.effect(l.b, ArraySet(made, at, values[i]))
    }
    made
}

fun zero(l : Lower, r : Rep) -> Value = match r.class_ {
    Unit -> L.emit(l.b, ConstUnit, r)
    F64 -> L.emit(l.b, ConstFloat(0.0), r)
    Ptr -> L.emit(l.b, Box(L.emit(l.b, ConstInt(0), Ssa.untraced(I64))), r)
    _ -> L.emit(l.b, ConstInt(0), r)
}

-- A saturated constructor. Nullary is an object with no fields rather than a
-- shared constant, which is what the Python backend builds too.
fun constructor(l : Lower, e : Core, name : String, args : Array Core,
                rep : Rep) -> Value {
    if name == P.boolFalse || name == P.boolTrue {
        return L.emit(l.b, ConstInt(if name == P.boolTrue { 1 } else { 0 }),
                      Ssa.untraced(I1))
    }
    match Decls.erasedPayload(l.decls, e.ty) {
        Some(_) -> if len(args) == 1 { return value(l, args[0]) }
        None -> {}
    }
    match Map.get(l.tags, name) {
        Some(tag) -> aggregate(l, args, 1, tag)
        None -> stop(l, "the constructor '" + name + "' has no tag")
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
            primitive(l, e, name, supplied, rep)
        }
        -- A primitive reaches Core as a `CVar` as often as a `CPrim`: the
        -- module scope binds every `Prim.` name to itself, so an ordinary
        -- variable reference is what the resolver produces. `Prim` is the
        -- builtin module and its prefix is reserved, so the name is the test.
        CVar(name) -> if isPrim(name) {
            for a in args { Array.push(supplied, value(l, a)) }
            primitive(l, e, name, supplied, rep)
        } else if Set.contains(l.globals, name) {
            for a in args { Array.push(supplied, value(l, a)) }
            L.emit(l.b, Call(Direct(name), supplied), rep)
        } else {
            let closure = value(l, fn)
            for a in args { Array.push(supplied, value(l, a)) }
            L.emit(l.b, Call(Indirect(closure), supplied), rep)
        }
        CCon(name) -> constructor(l, e, name, args, rep)
        CTyApp(_, _) -> call(l, e, erased(fn), args, rep)
        CTyLam(_, _) -> call(l, e, erased(fn), args, rep)
        _ -> stop(l, "a call to " + formOf(fn))
    }
}

-- Past the type abstraction and application, which have no run-time content.
fun erased(e : Core) -> Core = match e.kind {
    CTyApp(inner, _) -> erased(inner)
    CTyLam(_, inner) -> erased(inner)
    _ -> e
}

-- A primitive whose machine operation this instruction set has, or a runtime
-- call. The split is the one `NATIVE-BACKEND.md` argues for: a constructor
-- where instruction selection must match, and a symbol where it need not.
fun isPrim(name : String) -> Bool = String.startsWith(name, "Prim.")

fun primitive(l : Lower, e : Core, name : String, args : Array Value,
              rep : Rep) -> Value {
    -- `error` does not answer; it leaves. Terminating here and then moving
    -- into a fresh block keeps the walk uniform -- whatever the caller emits
    -- next is well formed and unreachable, and the verifier says nothing
    -- about unreachable code on purpose.
    if name == "Prim.error" && len(args) == 1 {
        L.terminate(l.b, Panic(args[0]))
        L.into(l.b, L.block(l.b))
        return zero(l, rep)
    }
    -- The array operations are instructions rather than runtime calls: the
    -- element's representation decides the stride, and that is a fact
    -- instruction selection must see rather than one buried in a callee.
    if name == "Prim.arrayLength" && len(args) == 1 {
        return L.emit(l.b, ArrayLength(args[0]), Ssa.untraced(I64))
    }
    if name == "Prim.arrayGet" && len(args) == 2 {
        return L.emit(l.b, ArrayGet(args[0], args[1]), rep)
    }
    if name == "Prim.arraySet" && len(args) == 3 {
        L.effect(l.b, ArraySet(args[0], args[1], args[2]))
        return L.emit(l.b, ConstUnit, Ssa.untraced(Unit))
    }
    if name == "Prim.arrayNew" && len(args) == 2 {
        return L.emit(l.b, ArrayNew(args[0], args[1],
                                    Ssa.repOf(l.func, args[1]).class_),
                      Ssa.traced(Ptr))
    }
    if name == "Prim.arrayNewUninit" && len(args) == 1 {
        -- Uninitialized in the source language and not in the heap: the
        -- collector may walk this array before anything writes to it, so a
        -- traced slot must hold something it can follow.
        let element = elementRep(l, e.ty)
        return L.emit(l.b, ArrayNew(args[0], zero(l, element), element.class_),
                      Ssa.traced(Ptr))
    }
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
    if (name == "Prim.intNeg" || name == "Prim.intNegWrapping"
            || name == "Prim.floatNeg") && len(args) == 1 {
        return L.emit(l.b, Neg(args[0]), rep)
    }
    match conversion(name) {
        Some(c) -> if len(args) == 1 {
            return L.emit(l.b, Convert(c, args[0]), rep)
        }
        None -> {}
    }
    if name == "Prim.not" && len(args) == 1 {
        return L.emit(l.b, Not(args[0]), rep)
    }
    L.emit(l.b, Call(Runtime(name), args), rep)
}

-- The element representation of an array type.
fun elementRep(l : Lower, ty : Ty.Type) -> Rep {
    let (_, args) = Ty.spine(Ty.prune(ty))
    if len(args) > 0 { repOf(l, args[0]) } else { Ssa.traced(Ptr) }
}

fun conversion(name : String) -> Option Conv = match name {
    "Prim.intToFloat" -> Some(IntToFloat)
    "Prim.floatTruncate" -> Some(FloatToInt)
    "Prim.byteToInt" -> Some(Widen)
    "Prim.byteFromInt" -> Some(Narrow)
    "Prim.charToInt" -> Some(Widen)
    "Prim.charFromInt" -> Some(Narrow)
    _ -> None
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
    -- The wrapping forms are the same machine instruction; what differs is
    -- the overflow check the checked ones carry, and that is `Effects.traps`.
    "Prim.intAddWrapping" -> Some(Add)
    "Prim.intSubWrapping" -> Some(Sub)
    "Prim.intMulWrapping" -> Some(Mul)
    "Prim.floatAdd" -> Some(Add)
    "Prim.floatSub" -> Some(Sub)
    "Prim.floatMul" -> Some(Mul)
    "Prim.floatDiv" -> Some(Div)
    _ -> None
}

fun cmpOp(name : String) -> Option CmpOp = match name {
    "Prim.intEq" -> Some(Eq)
    "Prim.intLt" -> Some(Lt)
    "Prim.byteEq" -> Some(Eq)
    "Prim.byteLt" -> Some(Lt)
    "Prim.charEq" -> Some(Eq)
    "Prim.charLt" -> Some(Lt)
    "Prim.boolEq" -> Some(Eq)
    "Prim.boolLt" -> Some(Lt)
    "Prim.floatEq" -> Some(Eq)
    "Prim.floatLt" -> Some(Lt)
    "Prim.floatLte" -> Some(Le)
    "Prim.floatGt" -> Some(Gt)
    "Prim.floatGte" -> Some(Ge)
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
