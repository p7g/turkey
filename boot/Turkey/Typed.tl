module Turkey.Typed (
    TypeTable(..), newTypeTable, observe,
    record, recordDecl, ofDecl, recordPattern, ofPattern, of_, resolve,
    unresolved, reduceDeep, typeChildren,
)

-- What inference discovered about each expression, kept rather than dropped
-- (`turkey/typed.py`).
--
-- Generation gives every expression a type and then throws it away: the type is
-- a means of building the constraint, and once the constraint is solved nothing
-- asks again. That is fine while an evaluator walks the surface tree. It is not
-- fine for a typed Core, which needs the type of every node it lowers.
--
-- So the generator records what it already computed, into one table per program.
-- Two things make that cheap rather than a second pass:
--
-- * **A type is mutable union-find.** A variable stashed during generation is
--   not a snapshot; solving fills in its cell, and pruning reads the answer out
--   later. Nothing has to be re-derived or copied back.
-- * **Nothing is checked here.** The table is written during generation and read
--   after solving. In between it holds variables that mean nothing yet, which is
--   why `resolve` and not `record` is where a type has to be a type.
--
-- Python keys the table by object identity; here it is the `id` the parser
-- stamps on every node, which is the decision M20 took for exactly this.

import Data.Set as Set
import Turkey.Ast
import Turkey.Diag as Diag
import Turkey.Classes as Classes
import Turkey.Classes (ClassTable(..))
import Turkey.Solved (Abstraction(..), Use(..))
import Turkey.Types as Ty
import Turkey.Types (TVar, TCon, TApp, TFam, TFun, TTuple, TSet, TBottom)

type TypeTable = TypeTable {
    exprs : Map Int Ty.Type,
    decls : Map Int Ty.Type,
    pats  : Map Int (Map String Ty.Type),
    fams  : Option Ty.Families,
    -- The dictionary abstraction of a binding group, and the evidence a use
    -- site turned out to need. Python hangs both on the surface node; the
    -- modules that define their types sit above `Turkey.Ast`, so they live here
    -- -- which is where the *other* thing keyed by node id already lives, and
    -- is shared across modules for the same reason.
    abstractions : Map Int Abstraction,
    uses         : Map Int Use,
}

fun newTypeTable() -> TypeTable = TypeTable {
    exprs = Map.new(), decls = Map.new(), pats = Map.new(), fams = None,
    abstractions = Map.new(), uses = Map.new(),
}

-- Take the family reducer to read recorded types back with.
--
-- Not the solver's. The solver consults the *assumptions* in scope first, and
-- those are per-body and gone by the time anything reads this table -- but so is
-- the need for them: an assumption's rule was applied during solving, in place,
-- on these very type objects. What can still be reduced afterwards is what the
-- class table decides.
--
-- Including the equations the solver stuck on, which it keeps as rewrite rules.
-- That is not optional: if a predicate was discharged by such a rule then the
-- evidence names an instance of the *reduced* type, so a recorded type left
-- unreduced cannot be given that evidence -- the lowering asks for
-- `%Dict.Mul (Field.cap a)` and is handed `Mul Int`. Solving, elaboration and
-- the recorded types have to reduce by the same rules or they disagree.
fun observe(t : TypeTable, classes : ClassTable) -> Unit {
    t.fams = Some(Ty.Families {
        reduce = fun(ty) = match ty {
            TFam(name, arg, _) -> Classes.reduceFam(classes, name, arg)
            _ -> None
        },
        -- Nothing is being solved here, so there is nothing to defer to.
        defer = fun(_, _, _, _) = {},
    })
}

fun record(t : TypeTable, e : Expr, ty : Ty.Type) -> Unit =
    Map.put(t.exprs, e.id, ty)

-- The type a `fun` declaration was bound at. Needed for the *local* case and
-- only there: a top-level name's type is in the environment afterwards, but a
-- local `fun`'s is not -- it is defined into a child scope that is popped. A
-- group that generalizes records its schemes instead; this is the other case.
--
-- A `FunDecl` is not one of the node kinds that carries an id, so it is keyed by
-- the span of the statement that holds it -- unique per declaration, because two
-- declarations cannot start at one place.
fun recordDecl(t : TypeTable, key : Int, ty : Ty.Type) -> Unit =
    Map.put(t.decls, key, ty)

fun ofDecl(t : TypeTable, key : Int) -> Option Ty.Type = match Map.get(t.decls, key) {
    Some(ty) -> Some(resolve(t, ty))
    None -> None
}

fun recordPattern(t : TypeTable, p : Pat, binds : Map String Ty.Type) -> Unit {
    if len(Map.keys(binds)) > 0 {
        let copy = Map.new()
        for (n, ty) in Map.entries(binds) { Map.put(copy, n, ty) }
        Map.put(t.pats, p.id, copy)
    }
}

fun ofPattern(t : TypeTable, p : Pat) -> Map String Ty.Type = match Map.get(t.pats, p.id) {
    None -> Map.new()
    Some(binds) -> {
        let out = Map.new()
        for (n, ty) in Map.entries(binds) { Map.put(out, n, resolve(t, ty)) }
        out
    }
}

-- The resolved type of one expression. Only meaningful after solving.
--
-- A miss is a bug rather than a program's mistake: generation records every
-- expression it visits, in one place so that no case can forget. Answering
-- something plausible instead would push the failure into whatever read it.
fun of_(t : TypeTable, e : Expr) -> Ty.Type = match Map.get(t.exprs, e.id) {
    Some(ty) -> resolve(t, ty)
    None -> Diag.fail(Diag.at("internal error",
        "no type was recorded for this expression", e.span), e.span.file)
}

-- Read a recorded type out, once solving has decided what it is.
--
-- Two things have to be gone by now or the result is not a type in any useful
-- sense, and both are the solver's job rather than this table's: a literal set,
-- which defaulting settles at generalization, and a family application that
-- never reduced. A family under a signature's own context is legitimate and
-- stays: it is a family over a rigid argument, and `Item c` where `c` is bound
-- by the enclosing type abstraction is as much a type as `Int` is.
fun resolve(t : TypeTable, ty : Ty.Type) -> Ty.Type = reduceDeep(ty, t.fams)

-- Every expression whose type is still a decision. The self-check: after
-- solving, this must be empty. A diagnostic rather than an assertion, because
-- what it looks for is exactly what a Core lowering will assume without saying.
fun unresolved(t : TypeTable) -> Array Ty.Type {
    let out = Array.new(2)
    for (_, ty) in Map.entries(t.exprs) {
        let settled = resolve(t, ty)
        if stuck(settled) { Array.push(out, settled) }
    }
    out
}

-- Head reduction applied at every level rather than at the head.
--
-- Head-only is right for unification, which only ever compares heads. It is not
-- enough for anything that reads a type *whole* -- a lowering writing it into an
-- IR, or a checker comparing two of them -- because `Option (Item (Array Int))`
-- and `Option Int` are the same type and only one of them has been reduced.
fun reduceDeep(ty : Ty.Type, fams : Option Ty.Families) -> Ty.Type {
    let here = Ty.normalize(Ty.prune(ty), fams)
    match here {
        TApp(fn, arg, k) -> TApp(reduceDeep(fn, fams), reduceDeep(arg, fams), k)
        TFun(params, ret) -> TFun(
            Array.map(params, fun(p) = reduceDeep(p, fams)),
            reduceDeep(ret, fams))
        TTuple(elems) -> TTuple(Array.map(elems, fun(e) = reduceDeep(e, fams)))
        -- The head stuck, so reduce the argument and ask once more: what blocked
        -- `Item (Elem c)` may have been the `Elem c`, and one retry is all it can
        -- take, since the argument is now reduced.
        TFam(name, arg, k) -> {
            let reduced = reduceDeep(arg, fams)
            let again = Ty.normalize(TFam(name, reduced, k), fams)
            match again {
                TFam(_, _, _) -> TFam(name, reduced, k)
                _ -> again
            }
        }
        _ -> here
    }
}

-- Whether a resolved type still contains an undecided literal set.
fun stuck(ty : Ty.Type) -> Bool {
    match Ty.prune(ty) {
        TSet(_) -> return True
        _ -> {}
    }
    for c in typeChildren(Ty.prune(ty)) {
        if stuck(c) { return True }
    }
    False
}

-- One level of a type's structure. Spelled out rather than reflected: a missing
-- case here would silently under-report, which is the one thing a check for what
-- must not survive cannot afford.
fun typeChildren(ty : Ty.Type) -> Array Ty.Type = match ty {
    TApp(fn, arg, _) -> [fn, arg]
    TFam(_, arg, _) -> [arg]
    TFun(params, ret) -> Array.append(params, [ret])
    TTuple(elems) -> elems
    _ -> Array.new(0)
}
