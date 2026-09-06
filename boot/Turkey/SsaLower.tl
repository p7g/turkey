module Turkey.SsaLower (
    Unsupported(..), Signature(..), Lifting(..), Module(..),
    lowerBind, lowerProgram, tagsOf, signatures, signatureOf, adapterSymbol,
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
import Turkey.Core (Core(..), CoreKind(..), CParam(..), CBind(..), CProgram(..),
                    Mapper(..))
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

-- What a *direct* call passes and receives.
--
-- The closure ABI's leading environment is deliberately absent: this is the
-- natural signature, and the uniform one is derived from it where it is
-- needed. `turkey/backend_lower.py` asks the same question with
-- `held_at(expected, callee, decls)` -- the *callee's* abstracted layouts and
-- not the caller's, which is the whole point. A layout-shared copy holds its
-- parameters at the layouts its key names, so a caller that read them off its
-- own scheme would hand a boxed value to a body expecting a bare one; see
-- FINDINGS 53 and 54, which are two instances of exactly that.
type Signature = Signature {
    params : Array Rep,
    result : Rep,
}

-- The state one lowering run shares across every function it makes.
--
-- Records have reference semantics (FINDINGS 10), so one of these passed to a
-- parent and to the nested `Lower`s its lambdas make is one object -- which is
-- what keeps the symbols unique and the adapters memoized. That is the same
-- property FINDINGS 10 records the *downside* of, used deliberately here.
type Lifting = Lifting {
    -- Names one lifted lambda apart from the next. Program-wide rather than
    -- per binding, so two lambdas in two bindings cannot collide.
    next     : Int,
    -- The top-level functions someone used as a value, and so needs a boxing
    -- adapter for. A set: `MkBox(identity)` in two places is one adapter, and
    -- two would be one duplicate symbol.
    adapters : Set.Set String,
}

-- A whole program's low IR: the functions, the storage the module initializer
-- writes, and the initializer's own name.
--
-- Globals are here rather than implied, because a global's *representation* is
-- not derivable from its name at the use site and both the initializer's store
-- and every `GlobalLoad` need it. It is also what the collector's permanent
-- root array is built from -- a pointer global is rooted for the life of the
-- program, so storing to it and rooting it are the same store.
type Module = Module {
    functions : Array (Ssa.Func Low),
    globals   : Array (String, Rep),
    -- The function that computes every global, in the order the program
    -- requires. Called once before `main`.
    initial   : String,
}

let initializerName = "%module.initialize"

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
    -- Constructor name -> its fields, in the order the object holds them.
    fields  : Map String (Array String),
    -- What a direct call must coerce to, per callee. Program-wide, and read
    -- rather than derived: the caller and the callee agree because they read
    -- one table, not because they compute the same thing twice.
    sigs    : Map String Signature,
    -- The layout each abstracted variable stands for, from the binding. What
    -- `Turkey.Layout` gave the copy, and what makes a representation knowable
    -- inside one.
    layouts : Map Int String,
    -- The functions lifted out of *this* top-level binding. Shared by
    -- reference with the nested `Lower`s its lambdas make, and discarded whole
    -- if the binding stops -- a lifted body whose parent did not lower is a
    -- symbol nothing calls.
    lifted  : Array (Ssa.Func Low),
    -- What belongs to the program rather than to one binding.
    shared  : Lifting,
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

-- What a body *holds* a value of this type at, under these abstracted layouts.
--
-- A type the layout pass cannot answer for is held at the uniform
-- representation, which is a traced pointer. That is the same answer
-- `held_at` gives and for the same reason -- parametricity leaves a generic
-- body able to hold a value and pass it on, and nothing else.
--
-- Split out of `repOf` because the signature pre-pass asks this before any
-- `Lower` exists, and asks it under a *different* binding's layouts than the
-- one being lowered. That is the whole point of `Signature`.
fun repIn(decls : DeclTable, layouts : Map Int String, ty : Ty.Type) -> Rep =
    match Layout.layoutOf(decls, ty, layouts) {
        Some(name) -> repOfName(name)
        None -> Ssa.traced(Ptr)
    }

fun repOf(l : Lower, ty : Ty.Type) -> Rep = repIn(l.decls, l.layouts, ty)

-- -- lowering -----------------------------------------------------------------------

fun lowerProgram(program : CProgram, decls : DeclTable)
        -> (Module, Array Unsupported) {
    let done : Array (Ssa.Func Low) = []
    let skipped : Array Unsupported = []
    let globals = Set.new()
    for bind in Array.append(program.dicts, program.binds) {
        Set.add(globals, bind.name)
    }
    let fields = recordLayouts(program, decls)
    let tags = tagsOf(decls, fields)
    let sigs = signatures(program, decls)
    let shared = Lifting { next = 0, adapters = Set.new() }
    for bind in Array.append(program.dicts, program.binds) {
        let (f, lifted, why) = lowerBind(bind, decls, globals, tags, fields,
                                         sigs, shared)
        match f {
            Some(function) -> {
                -- The parent first, so a dump reads outside in.
                Array.push(done, function)
                for g in lifted { Array.push(done, g) }
            }
            -- No reason means it is not a function at all: a global, which the
            -- initializer computes below. That is not a skip and is not
            -- counted as one -- the histogram is what says how much of the
            -- language is still unwritten, so a form that *is* handled, by
            -- another phase, does not belong in it.
            None -> match why {
                Some(w) -> Array.push(skipped,
                                      Unsupported { name = bind.name, what = w })
                None -> {}
            }
        }
    }
    -- The initializer before the adapters, because it lowers Core too: a
    -- global's value may mention a top-level function, and the adapter for it
    -- would then be requested after the loop that builds them. Its own lifted
    -- lambdas come back the same way a binding's do.
    let (init, lifted, storage) = initializer(program, decls, globals, tags,
                                              fields, sigs, shared)
    Array.push(done, init)
    for g in lifted { Array.push(done, g) }
    -- The adapters last, and sorted, for the reason `tagsOf` sorts: what a run
    -- produces should not depend on which binding happened to ask first. A
    -- binding that stopped may have left a request behind, and the adapter is
    -- then a symbol nothing calls -- cheaper than tracking requests per
    -- binding to merge them on success.
    for target in Array.sort(Set.elements(shared.adapters)) {
        match Map.get(sigs, target) {
            Some(sig) -> Array.push(done, adapter(decls, target, sig, globals,
                                                  tags, fields, sigs, shared))
            None -> {}
        }
    }
    (Module { functions = done, globals = storage,
              initial = initializerName }, skipped)
}

-- Where each global's value is kept, and at what representation.
--
-- A binding that is not a function, which is exactly the complement of
-- `signatures`. Its representation comes from the binding's *own* layouts for
-- the same reason a signature does.
fun storageOf(program : CProgram, decls : DeclTable) -> Array (String, Rep) {
    let out = []
    for bind in Array.append(program.dicts, program.binds) {
        match signatureOf(decls, bind) {
            Some(_) -> {}
            None -> Array.push(out, (bind.name,
                                     repIn(decls, bind.layouts, bind.ty)))
        }
    }
    out
}

-- What every top-level function is called at.
--
-- A pre-pass, because a call site is lowered before the callee's body may have
-- been: `lowerProgram` walks the bindings in whatever order the program lists
-- them, and a mutually recursive pair is ordinary. A binding that is not a
-- function has no entry, and that absence is what tells a call site it is
-- reaching a global that holds a closure rather than a symbol.
fun signatures(program : CProgram, decls : DeclTable) -> Map String Signature {
    let out = Map.new()
    for bind in Array.append(program.dicts, program.binds) {
        match signatureOf(decls, bind) {
            Some(sig) -> {
                -- Top-level names are module-qualified, so a collision is a
                -- compiler bug and not a program that shadows.
                if Map.contains(out, bind.name) {
                    oops("two top-level bindings named '" + bind.name + "'")
                }
                Map.put(out, bind.name, sig)
            }
            None -> {}
        }
    }
    out
}

-- One binding's signature, keyed on *that binding's* layouts.
fun signatureOf(decls : DeclTable, bind : CBind) -> Option Signature {
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
    let reps = Array.map(params, fun(p) = repIn(decls, bind.layouts, p.ty))
    -- From the binding's own type rather than from the body: a body that
    -- always diverges is typed bottom, and a caller reads the signature.
    let result = match Ty.prune(resultType(bind.ty)) {
        TBottom -> repIn(decls, bind.layouts, body.ty)
        other -> repIn(decls, bind.layouts, other)
    }
    Some(Signature { params = reps, result = result })
}

-- Every constructor's runtime tag: the whole program's constructor names in
-- order, numbered from zero.
--
-- Sorted rather than in declaration order, because declaration order is the
-- order the modules happened to be read in and a tag is written into a heap
-- object that outlives the compilation that made it. Nothing reads a tag by
-- name at run time; what matters is that one compilation agrees with itself,
-- and that the numbering does not move when an unrelated module gains a type.
fun tagsOf(decls : DeclTable,
           fields : Map String (Array String)) -> Map String Int {
    -- The dictionaries too, which are constructors nothing declared. Their
    -- tag is never tested -- a dictionary type has one shape -- but it is
    -- written into the object, and a numbering that omitted them would give
    -- two different dictionaries the same `-1`.
    let every = Set.new()
    for name in Map.keys(decls.constructors) { Set.add(every, name) }
    for name in Map.keys(fields) { Set.add(every, name) }
    let names = Array.sort(Set.elements(every))
    let out = Map.new()
    for var i = 0; i < len(names); i = i + 1 { Map.put(out, names[i], i) }
    out
}

-- One top-level binding: the function it became, the functions lifted out of
-- it, and the form that stopped it.
fun lowerBind(bind : CBind, decls : DeclTable, globals : Set.Set String,
              tags : Map String Int, fields : Map String (Array String),
              sigs : Map String Signature, shared : Lifting)
        -> (Option (Ssa.Func Low), Array (Ssa.Func Low), Option String) {
    -- Not a function: a global whose value the module initializer computes,
    -- which is its own phase. The two `let`s rather than one destructuring
    -- binding are FINDINGS 41 -- a control transfer cannot cross one.
    let shape = spine(bind.value)
    match shape {
        None -> return (None, [], None)
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
    -- Read back rather than recomputed. The caller coerces to what `sigs`
    -- says, so the callee must be built from the same entry -- two derivations
    -- of one convention is exactly the disagreement this table exists to
    -- prevent, and a compiler that computed it twice would be right until one
    -- of the two moved.
    let sig = match Map.get(sigs, bind.name) {
        Some(found) -> found
        None -> Signature { params = [], result = Ssa.traced(Ptr) }
    }
    let f = Ssa.newFunc(bind.name, sig.result)
    let l = newLower(decls, f, globals, tags, fields, sigs, bind.layouts, [],
                     shared)
    let entry = L.block(l.b)
    L.into(l.b, entry)
    for var i = 0; i < len(params); i = i + 1 {
        let held = if i < len(sig.params) { sig.params[i] }
                   else { repOf(l, params[i].ty) }
        let v = Ssa.freshValue(f, held)
        Array.push(f.params, v)
        Map.put(l.env, params[i].name, v)
    }
    lower(l, body, ToReturn)
    match l.stopped {
        Some(why) -> (None, [], Some(why))
        None -> (Some(f), l.lifted, None)
    }
}

-- The three places a `Lower` is made: a top-level binding, a lifted lambda,
-- and a synthesized adapter. One constructor, so a field that is meant to be
-- shared cannot be shared in two of them and copied in the third.
fun newLower(decls : DeclTable, f : Ssa.Func Low, globals : Set.Set String,
             tags : Map String Int, fields : Map String (Array String),
             sigs : Map String Signature, layouts : Map Int String,
             lifted : Array (Ssa.Func Low), shared : Lifting) -> Lower = Lower {
    decls = decls, func = f, b = L.builder(f, BlockId(0)),
    env = Map.new(), joins = Map.new(), globals = globals, tags = tags,
    fields = fields, sigs = sigs, layouts = layouts, lifted = lifted,
    shared = shared, stopped = None,
}

-- Every constructor's fields, in the order its object holds them.
--
-- `Turkey.Decls` knows the ones a program *declared*, and a dictionary is not
-- one of them: its type and its constructor are synthesized when evidence
-- becomes a value, so nothing registers them. What does know is the program
-- itself -- every dictionary is a `CRecord` somewhere in it -- so the records
-- are collected by walking it, which is what `turkey/backend_lower.py`'s
-- `_record_layouts` does and for the same reason.
--
-- The traversal is `Core.mapKind` with an identity rebuild, used for its
-- totality rather than its result: a form this misses is a dictionary whose
-- fields are read at the wrong offsets, and exhaustiveness is what stops a
-- later node from being forgotten here.
fun recordLayouts(program : CProgram,
                  decls : DeclTable) -> Map String (Array String) {
    let out = Map.new()
    for (_, info) in Map.entries(decls.constructors) {
        match info.fields {
            Some(names) -> Map.put(out, info.name, names)
            None -> {}
        }
    }
    for bind in Array.append(program.dicts, program.binds) {
        collectRecords(bind.value, out)
    }
    out
}

fun collectRecords(e : Core, out : Map String (Array String)) -> Unit {
    match e.kind {
        CRecord(con, fs) -> if !Map.contains(out, con) {
            Map.put(out, con, Array.map(fs, fun(f) = f.0))
        }
        _ -> {}
    }
    let _ = Core.mapKind(e.kind, Mapper {
        expr = fun(inner) = {
            collectRecords(inner, out)
            inner
        },
        type_ = fun(t) = t,
        bind = fun(b) = {
            collectRecords(b.value, out)
            b
        },
    })
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

-- The same, for a join label. One name rather than an array, because a
-- `CJoin` binds exactly one.
fun savingJoin(l : Lower, name : String) -> Option BlockId =
    Map.get(l.joins, name)

fun restore1Join(l : Lower, name : String, was : Option BlockId) -> Unit =
    match was {
        Some(b) -> Map.put(l.joins, name, b)
        None -> {
            let _ = Map.delete(l.joins, name)
        }
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
        CJoin(name, params, body, rest, recursive) -> {
            let target = L.block(l.b)
            let entered : Array Value = []
            for p in params {
                Array.push(entered, L.param(l.b, target, repOf(l, p.ty)))
            }
            -- Join names are no more unique than term names are, and for the
            -- same reason: inlining copies bodies, so a function inlined into
            -- itself has two joins called `loop`. An unscoped map sends the
            -- outer copy's jump to the inner copy's block, which is not a
            -- wrong value but a wrong *edge* -- and the verifier catches it as
            -- a definition that no longer dominates its use, exactly the way
            -- it caught the same mistake in `env` (FINDINGS 56).
            let hidden = savingJoin(l, name)
            Map.put(l.joins, name, target)
            -- `rest` first: it is what jumps, and the parameters have to exist
            -- before it can.
            lower(l, rest, dest)
            L.into(l.b, target)
            -- The join is in scope in its own body only if it is recursive --
            -- a loop compiled as a label. A non-recursive one is the
            -- case-of-case duplication and its body cannot reach it.
            if !recursive { restore1Join(l, name, hidden) }
            let was = saving(l, Array.map(params, fun(p) = p.name))
            for var i = 0; i < len(params) && i < len(entered); i = i + 1 {
                Map.put(l.env, params[i].name, entered[i])
            }
            lower(l, body, dest)
            restore(l, was)
            if recursive { restore1Join(l, name, hidden) }
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
        CLetRec(binds, body) -> letrec(l, binds, body, dest)
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
    -- Two scalar classes, and this is the one case that is *refused*.
    --
    -- Boxing is a representation boundary and parametricity creates it, so it
    -- happens implicitly. `i32` reaching a context that wants `i64` is not a
    -- boundary -- it is two disagreeing answers about one value's type, and
    -- widening it here would make the disagreement compile. A real conversion
    -- is written in the source (`Prim.byteToInt`) and reaches `Convert`
    -- through `primitive`. `turkey/backend_lower.py`'s `coerce` refuses this
    -- too, and this module used to convert silently.
    stop(l, "an implicit conversion from " + show(have) + " to " + show(want))
}

-- The same machine value with a different obligation on the collector.
fun retagged(l : Lower, v : Value, want : Rep) -> Value =
    if Ssa.repOf(l.func, v) == want { v }
    else { L.emit(l.b, Convert(Retag, v), want) }

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
            -- A top-level *function* mentioned rather than called.
            -- `GlobalLoad` would load the symbol, which is a code address and
            -- not something anything can call; what is wanted is a closure
            -- around it. See `adapter`.
            None -> match Map.get(l.sigs, name) {
                Some(_) -> {
                    Set.add(l.shared.adapters, name)
                    L.emit(l.b, ClosureNew(adapterSymbol(name), 0, 0),
                           Ssa.traced(Ptr))
                }
                None -> if Set.contains(l.globals, name) {
                    L.emit(l.b, GlobalLoad(name), rep)
                } else if isPrim(name) {
                    -- Referred to but not called. Closure conversion does not
                    -- reach this: a primitive has no `CBind`, so there is no
                    -- signature to adapt from, and the fix is a per-primitive
                    -- adapter that `primitive` would generate. The Python
                    -- backend refuses it too.
                    stop(l, "the primitive '" + name + "' used as a value")
                } else {
                    stop(l, "an unbound name '" + name + "'")
                }
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
                    None -> stop(l, "the field '" + name + "' of "
                                    + Ty.typeKey(target.ty))
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
            -- The one table, so a dictionary is *built* in the order it is
            -- later *read* in. Two sources for that order is how a field ends
            -- up written at one offset and read at another.
            let order = Map.getOr(l.fields, con, [])
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
        CLam(params, body, _) -> lambda(l, params, body)
        CIf(_, _, _) -> control(l, e, rep)
        CMatch(_, _) -> control(l, e, rep)
        CLet(_, _, _, _, _) -> control(l, e, rep)
        CJoin(_, _, _, _, _) -> control(l, e, rep)
        CLetRec(_, _) -> control(l, e, rep)
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

-- -- closures ----------------------------------------------------------------------

-- One bit per capture, set where the collector must follow the slot.
--
-- Not `metadata`, and the difference is not cosmetic. The runtime reads a
-- kind-0 or kind-1 object's word as three bits of representation per field and
-- every other kind's -- including the environment's, which is kind 4 -- as one
-- bit per slot (`mark_children`). One C field, two encodings, and the object's
-- kind is what says which. Confusing them is silent: the collector follows an
-- integer, or fails to follow a pointer.
--
-- The test is `traced` rather than "is a pointer", which is the distinction
-- `Rep` carries and the Python backend's `PTR`/`BOXED` pair cannot make: a
-- code address is a pointer the collector must *not* follow.
fun bitmap(reps : Array Rep) -> Int {
    var out = 0
    for var i = 0; i < len(reps); i = i + 1 {
        if reps[i].traced { out = out + Int.shl(1, i) }
    }
    out
}

-- The captures a lambda needs: the names its body mentions that this body
-- holds, and what each is held at.
--
-- The *representation* and not the type. The parent writes the capture and the
-- child reads it back, so passing a type would have them each derive a
-- representation from it separately -- two derivations of one fact, and the
-- disagreement would be silent, because the graph still verifies and only the
-- collector notices.
--
-- `namesOf` collects every `CVar` in the body, including names bound inside it
-- and including those under nested lambdas -- which is what makes a transitive
-- capture work with no second traversal. Intersected with what this body holds
-- it over-approximates the free variables: a name bound inside the body that
-- also exists outside is captured needlessly, and the child's own
-- `saving`/`restore` shadows it, so the cost is a wasted slot. What it cannot
-- do is *miss* a free name, because a free mention is a `CVar` in the body.
--
-- Not the whole environment, which is what `turkey/backend_lower.py:1062`
-- captures. Two reasons it cannot be copied here. `turkey_object_new` refuses
-- more than 63 slots, and `l.env` in a body the size of `Turkey.Opt#expr`
-- holds far more than 63 names -- so capture-everything is not a slower
-- correct answer, it is a runtime panic on this compiler's own source. And
-- there an environment maps a name to a *slot*, which always exists; here it
-- maps a name to an SSA value, so every capture pins a live range from the
-- closure's creation to the end of the function, which the register allocator
-- would pay for.
--
-- `extra` is `CLetRec`'s siblings: names that will hold closures but do not
-- yet, so their representation is known before their value is.
fun capturesOf(l : Lower, body : Core,
               extra : Set.Set String) -> Array (String, Rep) {
    let mentioned = Core.namesOf(body)
    let out = []
    -- Sorted, so that what a lowering produces does not depend on a map's
    -- iteration order.
    for name in Array.sort(Set.elements(mentioned)) {
        if Set.contains(extra, name) {
            Array.push(out, (name, Ssa.traced(Ptr)))
        } else {
            match Map.get(l.env, name) {
                -- A `unit` holds nothing and the child can make its own; the
                -- 63-slot cap makes that worth doing rather than merely tidy.
                Some(v) -> if Ssa.repOf(l.func, v).class_ != Unit {
                    Array.push(out, (name, Ssa.repOf(l.func, v)))
                }
                None -> {}
            }
        }
    }
    out
}

-- Lift a lambda into a function of its own, and answer its symbol.
--
-- The child is an ordinary `Lower` over a fresh `Ssa.Func`: this record holds
-- its function and its builder directly, so a nested one is a second record
-- rather than a saved and restored cursor. What it *shares* is the lifted
-- array and the counter, which is what makes the symbols unique and the
-- results collectable.
fun lift(l : Lower, params : Array CParam, body : Core,
         captures : Array (String, Rep)) -> Option String {
    if len(captures) > 63 {
        let _ = stop(l, "a lambda with more than 63 captures")
        return None
    }
    -- `%` is the compiler's own character: the lexer will not accept one in an
    -- identifier, so no Turkey name mangles onto this. The same argument
    -- `turkey/backend_lower.py`'s `_25_` escape makes, and the reason a
    -- top-level name here already carries `#` and `@`.
    let symbol = l.func.name + "%lambda" + Int.toString(l.shared.next)
    l.shared.next = l.shared.next + 1
    -- Boxed, unconditionally: one code pointer is reached from call sites at
    -- many types.
    let g = Ssa.newFunc(symbol, Ssa.traced(Ptr))
    -- The *parent's* layouts. A closure inside a layout-shared copy holds the
    -- copy's variables at the copy's layouts, and a synthetic binding that
    -- forgot them would read its captures at the boxed width -- FINDINGS 54,
    -- and `turkey/backend_lower.py:1084` carries the same comment.
    --
    -- An empty `joins`, because a lambda's body is not a tail position: a
    -- closure outlives the frame that binds the join, so no label is in scope
    -- and sharing the parent's map would be a jump into a dead frame.
    -- `Turkey.Core`'s note on tail positions says exactly this.
    let child = newLower(l.decls, g, l.globals, l.tags, l.fields, l.sigs,
                         l.layouts, l.lifted, l.shared)
    let entry = L.block(child.b)
    L.into(child.b, entry)
    -- The environment comes first and is not the closure: the callee is handed
    -- what the closure's second slot holds, which is why a capture is read
    -- with an ordinary `ObjectGet` and written with `ClosureCapture`. The
    -- asymmetry is the runtime's -- the parent has the closure and the child
    -- has the environment -- and not this IR's.
    let environment = Ssa.freshValue(g, Ssa.traced(Ptr))
    Array.push(g.params, environment)
    let taken : Array Value = []
    for p in params {
        let v = Ssa.freshValue(g, Ssa.traced(Ptr))
        Array.push(g.params, v)
        Array.push(taken, v)
    }
    for var i = 0; i < len(captures); i = i + 1 {
        let (name, r) = captures[i]
        Map.put(child.env, name, L.emit(child.b, ObjectGet(environment, i), r))
    }
    for var i = 0; i < len(params) && i < len(taken); i = i + 1 {
        let held = repIn(l.decls, l.layouts, params[i].ty)
        Map.put(child.env, params[i].name, coerce(child, taken[i], held))
    }
    lower(child, body, ToReturn)
    match child.stopped {
        Some(why) -> {
            let _ = stop(l, why)
            None
        }
        None -> {
            Array.push(l.lifted, g)
            Some(symbol)
        }
    }
}

-- The closure itself: the shell, then the captures written into it.
--
-- No slot for the shell and no reload before the captures, which is what
-- `turkey/backend_lower.py` needs because its SSA is block-local. Here the
-- shell dominates every store that follows it in the same block.
fun closureFor(l : Lower, symbol : String,
               captures : Array (String, Rep)) -> Value {
    let reps = Array.map(captures, fun(c) = c.1)
    let closure = L.emit(l.b, ClosureNew(symbol, len(captures), bitmap(reps)),
                         Ssa.traced(Ptr))
    for var i = 0; i < len(captures); i = i + 1 {
        match Map.get(l.env, captures[i].0) {
            Some(v) -> L.effect(l.b, ClosureCapture(closure, i, v))
            None -> {
                let _ = stop(l, "a capture of the unbound name '"
                                + captures[i].0 + "'")
            }
        }
    }
    closure
}

-- A lambda in value position: lift it, then build the closure.
fun lambda(l : Lower, params : Array CParam, body : Core) -> Value {
    let captures = capturesOf(l, body, Set.new())
    match lift(l, params, body, captures) {
        Some(symbol) -> closureFor(l, symbol, captures)
        None -> Ssa.freshValue(l.func, Ssa.untraced(Unit))
    }
}

-- Recursive closures, shells first.
--
-- Each lambda may mention its siblings and itself, and the closure it would
-- capture does not exist while it is being lifted. `backend_lower.py` breaks
-- that with a slot per binding, put into the environment before any lifting:
-- the slot exists and the closure fills it later. Here no slot is needed --
-- what has to be known before the lift is a capture's *representation*, and a
-- closure is a traced pointer whether or not it has been allocated yet. So the
-- shells are made first, the names are bound to them, and the captures are
-- written afterwards, when every sibling is a value that dominates its store.
fun letrec(l : Lower, binds : Array CBind, body : Core, dest : Dest) -> Unit {
    let siblings = Set.new()
    for bind in binds {
        match spine(bind.value) {
            Some(_) -> Set.add(siblings, bind.name)
            None -> {
                -- Up front, so nothing is half emitted.
                let _ = stop(l, "a recursive binding that is not a function")
                return
            }
        }
    }
    let plans = []
    for bind in binds {
        let shape = spine(bind.value)
        let params = match shape {
            Some((ps, _)) -> ps
            None -> []
        }
        let inner = match shape {
            Some((_, b)) -> b
            None -> bind.value
        }
        let captures = capturesOf(l, inner, siblings)
        match lift(l, params, inner, captures) {
            Some(symbol) -> Array.push(plans, (bind.name, symbol, captures))
            None -> return
        }
    }
    -- Every shell exists before any capture is read.
    let shells = []
    for (_, symbol, captures) in plans {
        let reps = Array.map(captures, fun(c) = c.1)
        Array.push(shells, L.emit(l.b, ClosureNew(symbol, len(captures),
                                                  bitmap(reps)),
                                  Ssa.traced(Ptr)))
    }
    let was = saving(l, Array.map(plans, fun(p) = p.0))
    for var i = 0; i < len(plans) && i < len(shells); i = i + 1 {
        Map.put(l.env, plans[i].0, shells[i])
    }
    for var i = 0; i < len(plans) && i < len(shells); i = i + 1 {
        let (_, _, captures) = plans[i]
        for var j = 0; j < len(captures); j = j + 1 {
            match Map.get(l.env, captures[j].0) {
                Some(v) -> L.effect(l.b, ClosureCapture(shells[i], j, v))
                None -> {
                    let _ = stop(l, "a capture of the unbound name '"
                                    + captures[j].0 + "'")
                }
            }
        }
    }
    lower(l, body, dest)
    restore(l, was)
}

-- The symbol a top-level function's boxing adapter has.
--
-- Derived from the target rather than counted, so that two mentions of one
-- function are one adapter.
fun adapterSymbol(target : String) -> String = target + "%closure"

-- A top-level function used as a *value*, rather than called.
--
-- A closure's code is called at the uniform representation and a top-level
-- function's parameters are natural, so something has to convert. That is what
-- `turkey/llvmgen.py` generates a thunk per symbol for; here the thunk is
-- written in this IR instead: a function whose parameters are boxed, which
-- unboxes each to the callee's signature, calls it directly, and boxes the
-- result. It is then an ordinary lifted function with no captures, and
-- `ClosureNew(symbol, 0, 0)` is the value.
--
-- Nothing else in the compiler learns anything, and that is the argument.
-- `NATIVE-BACKEND.md`'s rule is that nothing in the low IR is shaped to suit
-- LLVM, and a `FunctionClosure` opcode would be exactly that shape: an
-- instruction with no machine meaning that every emitter expands for itself,
-- by generating a function body from inside a pass whose job is something
-- else. Making it here costs one `coerce` per parameter and buys the opcode
-- set staying at two dozen, which is what the exhaustive `match` design is
-- paid for with.
fun adapter(decls : DeclTable, target : String, sig : Signature,
            globals : Set.Set String, tags : Map String Int,
            fields : Map String (Array String), sigs : Map String Signature,
            shared : Lifting) -> Ssa.Func Low {
    let g = Ssa.newFunc(adapterSymbol(target), Ssa.traced(Ptr))
    let l = newLower(decls, g, globals, tags, fields, sigs, Map.new(), [], shared)
    let entry = L.block(l.b)
    L.into(l.b, entry)
    -- The environment, which an adapter has no use for: it captures nothing.
    -- It is a parameter anyway, because the closure ABI is one convention and
    -- a call site cannot know which side of it a code pointer came from.
    Array.push(g.params, Ssa.freshValue(g, Ssa.traced(Ptr)))
    let supplied : Array Value = []
    for r in sig.params {
        let v = Ssa.freshValue(g, Ssa.traced(Ptr))
        Array.push(g.params, v)
        Array.push(supplied, coerce(l, v, r))
    }
    let called = L.emit(l.b, Call(Direct(target), supplied), sig.result)
    L.terminate(l.b, Ret(coerce(l, called, Ssa.traced(Ptr))))
    g
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
    -- An integer literal at a float representation is an integer literal
    -- whose *type* is `Float` -- `1` where `1.0` was meant, which the surface
    -- language accepts. The constant has to become a float here, because
    -- `ConstInt` at `f64` is not a thing a machine has and every backend
    -- below would have to invent the same conversion.
    LInt(n) -> if rep.class_ == F64 {
        L.emit(l.b, ConstFloat(Float.fromInt(n)), rep)
    } else {
        L.emit(l.b, ConstInt(n), rep)
    }
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
        } else {
            -- The signature table and not `l.globals` is the test. A global
            -- may hold a *closure* -- the initializer computed it -- and
            -- calling it as a symbol would jump to a heap address. Having an
            -- entry is what makes a name a function.
            match Map.get(l.sigs, name) {
                Some(sig) -> if len(args) == len(sig.params) {
                    directCall(l, name, sig, args, rep)
                } else {
                    -- Core saturates its calls, so this is a partial
                    -- application and wants a closure that supplies the rest.
                    -- Nothing builds one, and saying so beats calling with the
                    -- wrong arity.
                    stop(l, "a partial application of '" + name + "'")
                }
                None -> closureCall(l, fn, args, rep)
            }
        }
        CCon(name) -> constructor(l, e, name, args, rep)
        CTyApp(_, _) -> call(l, e, erased(fn), args, rep)
        CTyLam(_, _) -> call(l, e, erased(fn), args, rep)
        -- Anything else evaluates to a function *value*, which is a closure:
        -- a method out of a dictionary (`CField`), the result of another call,
        -- a field of a record. That is the common shape in this language --
        -- every method call is one -- and there is nothing to decide, because
        -- a value that is not a known symbol is reached the only way a value
        -- can be.
        _ -> closureCall(l, fn, args, rep)
    }
}

-- A call to a known top-level function, at *its* representations.
--
-- The arguments are coerced to the callee's signature and the result back from
-- it. `turkey/backend_lower.py` does the same with the callee's own abstracted
-- layouts, and its comment is the reason: a layout-shared copy holds its
-- parameters at the layouts its key names, so a caller reading them off its
-- own scheme would hand a boxed value to a body expecting a bare one.
fun directCall(l : Lower, name : String, sig : Signature, args : Array Core,
               rep : Rep) -> Value {
    let supplied : Array Value = []
    for var i = 0; i < len(args); i = i + 1 {
        Array.push(supplied, coerce(l, value(l, args[i]), sig.params[i]))
    }
    coerce(l, L.emit(l.b, Call(Direct(name), supplied), sig.result), rep)
}

-- A call through a closure, at the uniform representation.
--
-- Everything is boxed and the result is boxed, because the callee is a
-- *lifted* function and a lifted function's parameters and result are boxed
-- whatever the expression's type says: one code pointer is reached from call
-- sites at many types, and the uniform representation is what makes that one
-- function rather than one per type.
--
-- The environment argument and the two slot loads are deliberately absent.
-- `Indirect` carries the closure and instruction selection expands it, the
-- same way `ClosureNew` expands into two allocations -- the level of this IR
-- is the runtime's ABI, not the machine's.
fun closureCall(l : Lower, fn : Core, args : Array Core, rep : Rep) -> Value {
    -- The callee before the arguments: Core evaluates it first, and this is
    -- the phase where that stops being implicit.
    let closure = value(l, fn)
    let supplied : Array Value = []
    for a in args {
        Array.push(supplied, coerce(l, value(l, a), Ssa.traced(Ptr)))
    }
    coerce(l, L.emit(l.b, Call(Indirect(closure), supplied), Ssa.traced(Ptr)),
           rep)
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
-- A compiler bug rather than a program that is wrong: top-level names are
-- module-qualified, so nothing a user writes can collide.
fun oops(message : String) -> a =
    Diag.fail(Diag.whole("internal error", message), "")

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

-- Where a named field sits in its object.
--
-- `Decls.recordFields` answers only for a *mutable* record, which is the
-- surface language's record and not the only thing with named fields: a
-- dictionary is a record of methods, an instance's superclasses are fields of
-- it, and neither is mutable. So the variants are searched too, which is what
-- `turkey/backend_lower.py`'s `field_index` does -- and a method selection
-- being an ordinary field read is the point of the milestone that made
-- evidence a value.
fun fieldIndex(l : Lower, ty : Ty.Type, name : String) -> Option Int {
    let (h, _) = Ty.spine(Ty.prune(ty))
    match h {
        TCon(c) -> {
            -- The program's own record shapes first: a dictionary's type and
            -- its constructor share a name, and nothing else knows its fields.
            match Map.get(l.fields, c.name) {
                Some(names) -> match Array.indexOf(names, name) {
                    Some(i) -> return Some(i)
                    None -> {}
                }
                None -> {}
            }
            match Decls.recordFields(l.decls, c.name) {
                Some(names) -> match Array.indexOf(names, name) {
                    Some(i) -> return Some(i)
                    None -> {}
                }
                None -> {}
            }
            match Map.get(l.decls.tycons, c.name) {
                Some(info) -> {
                    for con in info.variants {
                        match con.fields {
                            Some(names) -> match Array.indexOf(names, name) {
                                Some(i) -> return Some(i)
                                None -> {}
                            }
                            None -> {}
                        }
                    }
                    None
                }
                None -> None
            }
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

-- -- the module initializer -----------------------------------------------------------

-- One global's contribution to the initializer.
type Action
    -- Allocate a dictionary's record and publish it *before* its fields are
    -- computed, so a field that mentions the dictionary reaches a real
    -- address.
    = Placeholder(String, String, Array (String, Core))
    -- One field of a placeheld dictionary, once its object exists.
    | Field(String, Int, Core)
    -- An ordinary global: compute the value, store it.
    | Store(String, Core)

-- Every global, computed in one function.
--
-- Three phases, and the first two exist for one reason: a dictionary's fields
-- are the instance's methods, and a method mentions the dictionary it belongs
-- to. Building the record in one step would need its own address before it had
-- one. So every record-shaped dictionary is *allocated and published* first,
-- and its fields are stored afterwards -- the same shell-first discipline
-- `letrec` uses, for the same reason, one level up.
--
-- Each action's value is lowered under the *owning binding's* layouts and not
-- the initializer's. `Turkey.Layout` gives a copy its layouts, and the copy of
-- a dictionary is a record built here, so an initializer that kept one empty
-- map would lift the copy's methods with nothing known and read an array of
-- `i1` at the boxed width. That is FINDINGS 54, and it is the reason `Lower`
-- carries `layouts` per binding rather than per program.
fun initializer(program : CProgram, decls : DeclTable,
                globals : Set.Set String, tags : Map String Int,
                fields : Map String (Array String),
                sigs : Map String Signature, shared : Lifting)
        -> (Ssa.Func Low, Array (Ssa.Func Low), Array (String, Rep)) {
    let storage = storageOf(program, decls)
    let held = Map.new()
    for (name, r) in storage { Map.put(held, name, r) }
    let layouts = Map.new()
    for bind in Array.append(program.dicts, program.binds) {
        Map.put(layouts, bind.name, bind.layouts)
    }

    let actions : Array Action = []
    let deferred = []
    for bind in program.dicts {
        if !Map.contains(held, bind.name) { continue }
        match erased(bind.value).kind {
            CRecord(con, fields) -> {
                Array.push(actions, Placeholder(bind.name, con, fields))
                Array.push(deferred, (bind.name, fields))
            }
            _ -> Array.push(actions, Store(bind.name, bind.value))
        }
    }
    for (name, fields) in deferred {
        for var i = 0; i < len(fields); i = i + 1 {
            Array.push(actions, Field(name, i, fields[i].1))
        }
    }
    for bind in program.binds {
        if Map.contains(held, bind.name) {
            Array.push(actions, Store(bind.name, bind.value))
        }
    }

    let f = Ssa.newFunc(initializerName, Ssa.untraced(Unit))
    let l = newLower(decls, f, globals, tags, fields, sigs, Map.new(), [], shared)
    L.into(l.b, L.block(l.b))
    for a in actions {
        let owner = ownerOf(a)
        -- The owning binding's layouts, restored per action.
        l.layouts = Map.getOr(layouts, owner, Map.new())
        l.env = Map.new()
        l.joins = Map.new()
        l.stopped = None
        act(l, a, held)
        -- A hard failure, unlike a *function* that cannot be lowered.
        --
        -- The difference is what the failure costs. A skipped function leaves
        -- a symbol its callers still name, and `LowIr.checkCalls` says so; a
        -- skipped global leaves an initializer that runs to completion and
        -- quietly did not initialize something, which nothing downstream can
        -- notice. Reporting the second the way the first is reported would be
        -- a coverage number standing in for a program that cannot run.
        --
        -- This was a scaffold while the histogram was being worked down --
        -- skip the global, count it, carry on -- and it is deleted now that
        -- every program in the corpus lowers with nothing skipped.
        match l.stopped {
            None -> {}
            Some(why) -> oops("the global '" + owner + "' cannot be lowered: "
                              + why)
        }
    }
    L.terminate(l.b, Ret(L.emit(l.b, ConstUnit, Ssa.untraced(Unit))))
    (f, l.lifted, storage)
}

fun ownerOf(a : Action) -> String = match a {
    Placeholder(name, _, _) -> name
    Field(name, _, _) -> name
    Store(name, _) -> name
}

-- One action, appended to wherever the cursor is.
--
-- A `Store` and a `Field` both have to *compute* a value, and computing one
-- may branch -- a global initialized by a `match` is ordinary. So the value
-- goes to a block whose parameter receives it, and the store happens there,
-- which leaves the cursor in the right place for the action after it. This is
-- the same destination passing the rest of the module uses, and it is why the
-- initializer needs no reverse chaining: `turkey/backend_lower.py` builds its
-- blocks backwards because it must know a successor before the block that
-- jumps to it, and here the successor is simply where the cursor ends up.
fun act(l : Lower, a : Action, held : Map String Rep) -> Unit = match a {
    Placeholder(name, con, fields) -> {
        let reps = []
        for (_, v) in fields { Array.push(reps, repOf(l, v.ty)) }
        let made = L.emit(l.b, ObjectNew(1, Map.getOr(l.tags, con, -1),
                                         len(fields), metadata(reps)),
                          Ssa.traced(Ptr))
        L.effect(l.b, GlobalStore(name, made))
    }
    Field(name, index, v) -> {
        let after = L.block(l.b)
        let arrived = L.param(l.b, after, repOf(l, v.ty))
        lower(l, v, ToBlock(after))
        L.into(l.b, after)
        let record = L.emit(l.b, GlobalLoad(name), Ssa.traced(Ptr))
        L.effect(l.b, ObjectSet(record, index, arrived))
    }
    Store(name, v) -> {
        let want = Map.getOr(held, name, Ssa.traced(Ptr))
        let after = L.block(l.b)
        let arrived = L.param(l.b, after, want)
        lower(l, v, ToBlock(after))
        L.into(l.b, after)
        L.effect(l.b, GlobalStore(name, arrived))
    }
}
