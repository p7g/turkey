module Turkey.Constraints (
    oneOf,
    Constraint(..), PredCell(..), LetCell(..), cpredOf,
    Binding(..), Env(..), newEnv, childEnv, define, lookup_,
    Solver(..), newSolver, run, solve, families,
    fresh, adopt, rank,
    reach,
)

-- The constraint language, and the solver that settles it
-- (`turkey/constraints.py`).
--
-- Inference is split the way HM(X) splits it: the generator walks the tree and
-- *builds* a constraint, deciding nothing and never looking a name up. This
-- module solves that constraint, and owns the two things solving needs: the type
-- environment, and the stack of pools that decides what generalizes.
--
--     t1 ~ t2            CEq         equality
--     P t                CPred       a predicate of the domain X
--     C1 and C2          CAnd        conjunction
--     exists a. C        CExists     variables generation invented
--     def x : t in C     CDef        monomorphic binding
--     let x = C1 in C2   CLet        generalize C1, bind the schemes, solve C2
--     x <= t             CInstance   a use site
--     given |- C         CAssume     the obligations a signature already grants
--
-- **Ranks live here, not in the generator.** A variable's rank is the depth of
-- the binder it was created under, and `CLet` is where that depth changes -- so
-- the solver assigns ranks as it descends, and the generator never mentions
-- them. The pools are Remy's: one list of variables per rank, so that leaving a
-- rank is a walk over the variables born there rather than a scan of the
-- environment. Leaving rank r partitions its pool -- variables still at r are
-- quantified, and variables unification has lowered are *adopted* by the parent
-- pool.
--
-- `HasField` is Gaster & Jones's `r \ l` with the rows removed; in that shape it
-- is GHC's `HasField x r a | x r -> a`. Records here are nominal, so entailment
-- is a declaration lookup. See `improve` for what the missing rows cost.
--
-- A class predicate is the fourth form. It is discharged from the instance
-- table, or from the assumptions a `CAssume` has in scope: checking an instance
-- method against its class's signature is the one place a predicate is *granted*
-- rather than proved, because the signature said so.
--
-- An associated family makes an *equation* deferrable, and since SPEC-DELTAS.md
-- 39 it comes back as the equality predicate `~`, which is why there is one
-- queue here rather than two. `OneOf` is the last predicate: the closed set of
-- types a numeric literal could have, settled by three rules -- a singleton set
-- is an equation, two sets over one variable intersect, and a set nothing can
-- narrow further is defaulted.

import Data.Set as Set
import Turkey.Classes as Classes
import Turkey.Classes (ClassTable(..), InstInfo(..), MethodInfo(..))
import Turkey.Decls as Decls
import Turkey.Decls (DeclTable(..))
import Turkey.Diag as Diag
import Turkey.Solved (Scope(..), Abstraction(..), Use(..), dictName, newScope)
import Turkey.Source (Span)
import Turkey.Typed as Typed
import Turkey.Types as Ty
import Turkey.Types (VarCell(..), ConCell(..), Scheme(..), Pred(..),
                     TVar, TCon, TApp, TFam, TFun, TTuple,
                     TSet, TBottom)

let oneOf = "OneOf"

-- -- the tree ----------------------------------------------------------------------

type Constraint
    -- `left ~ right`. The equality fragment of the domain.
    = CEq(Ty.Type, Ty.Type, Option Span, String)
    -- A predicate that must hold. The `String` is the verb to use if it turns
    -- out to be unsatisfiable -- "read" or "mutate" for a `HasField`, which is
    -- the one thing about a field access the predicate itself does not record.
    -- Mutable, because `improve` rewrites a predicate's arguments in place.
    | CPred(PredCell)
    | CAnd(Array Constraint)
    -- `exists vars. body` -- the variables generation invented under a binder.
    -- This is how the generator hands the solver a set of variables without
    -- knowing what rank they belong to. The solver stamps them on the way in.
    | CExists(Array VarCell, Constraint)
    -- Bind names to *types* -- monomorphically -- while solving the body.
    -- Function parameters, `match` arm binders, and the placeholders of a
    -- recursive group. Nothing is generalized, so nothing changes rank.
    | CDef(Array (String, Ty.Type), Constraint, Bool)
    -- Bind names to *schemes* already built. The one binder whose schemes
    -- solving does not discover: a `fun` with a complete annotation states its
    -- own type, so the scheme is in scope from the group's first line, which is
    -- what lets a recursive occurrence *instantiate* it. That is polymorphic
    -- recursion, admissible here because the type was stated rather than
    -- guessed.
    | CBind(Array (String, Scheme), Constraint, Bool)
    -- Solve the definition one rank deeper, generalize, bind the schemes, solve
    -- the body. The single place generalization happens.
    | CLet(LetCell)
    -- Solve the body with `givens` taken as facts. The only source of
    -- assumptions is a *declared* type a body is being checked against, where
    -- the class's own predicate, the instance's context and the method's context
    -- are all facts. This is the local-assumption form and deliberately the
    -- whole of it: an assumption introduced by a *pattern* is what makes GADTs
    -- destroy principal types, and none is introduced here.
    | CAssume(Array (String, Pred), Constraint)
    -- `name <= t`: instantiate the scheme bound to `name` at `t`. The generator
    -- emits this instead of looking the name up, which is what lets it run
    -- without a type environment at all.
    | CInstance(String, Ty.Type, Option Span, Option Use)

-- A deferred predicate keeps its identity across rounds, and `improve` rewrites
-- its arguments where it stands, so it is a record rather than a variant field.
type PredCell = PredCell {
    pred    : Pred,
    span    : Option Span,
    context : String,
}

fun cpredOf(p : Pred, where : Option Span, context : String) -> Constraint =
    CPred(PredCell { pred = p, span = where, context = context })

type LetCell = LetCell {
    binds    : Array (String, Ty.Type),
    defn     : Constraint,
    body     : Constraint,
    span     : Option Span,
    topLevel : Bool,
    dicts    : Option Abstraction,
    -- Skolems that live exactly as long as the definition's rank -- the
    -- constants a signature's variables became. Carried here for the same reason
    -- ranks are the solver's business at all: this is the one place that knows
    -- how deep the definition sits, so this is where they can be stamped.
    skolems  : Array ConCell,
}

-- -- the environment ---------------------------------------------------------------

type Binding = Binding {
    scheme  : Scheme,
    -- Declared with `var`, so assignable.
    mutable : Bool,
}

-- A chain of scopes. Lookup walks outward; definition always writes here.
type Env = Env {
    parent : Option Env,
    names  : Map String Binding,
}

fun newEnv() -> Env = Env { parent = None, names = Map.new() }

fun childEnv(e : Env) -> Env = Env { parent = Some(e), names = Map.new() }

fun define(e : Env, name : String, b : Binding) -> Unit = Map.put(e.names, name, b)

fun lookup_(e : Env, name : String) -> Option Binding {
    var here = e
    var going = True
    while going {
        match Map.get(here.names, name) {
            Some(b) -> return Some(b)
            None -> match here.parent {
                Some(up) -> { here = up }
                None -> { going = False }
            }
        }
    }
    None
}

-- -- the solver --------------------------------------------------------------------

type Solver = Solver {
    decls      : DeclTable,
    classes    : ClassTable,
    env        : Env,
    -- Predicates a `CAssume` currently grants. A stack in effect, restored on
    -- the way out of each one.
    assumptions : Array Pred,
    -- The dictionary scopes open at the point being solved, innermost last. A
    -- `Use` keeps the records rather than their contents, because what a binder
    -- makes available is only known once its definition is solved.
    scopes     : Array Scope,
    uses       : Array Use,
    -- `pools[r]` holds the variables born at rank r.
    pools      : Array (Array VarCell),
    -- Predicates that could not be settled yet.
    deferred   : Array PredCell,
    -- Schemes of every name a top-level `CLet` bound, for the `types` command.
    topLevel   : Env,
}

fun newSolver(decls : DeclTable, env : Env, classes : ClassTable) -> Solver =
    Solver {
        decls = decls, classes = classes, env = env,
        assumptions = Array.new(4), scopes = Array.new(4), uses = Array.new(16),
        pools = [Array.new(8)], deferred = Array.new(8), topLevel = env,
    }

-- The two operations unification needs from the solver, as the record
-- `Turkey.Types` asks for. A record of closures rather than an interface, which
-- is what keeps the dependency pointing one way.
fun families(s : Solver) -> Option Ty.Families = Some(Ty.Families {
    reduce = fun(t) = reduceFam(s, t),
    defer = fun(a, b, sp, ctx) = defer(s, a, b, sp, ctx),
})

-- -- ranks -------------------------------------------------------------------------

fun rank(s : Solver) -> Int = len(s.pools) - 1

-- A variable at the current rank, recorded in the current pool.
fun fresh(s : Solver) -> VarCell {
    let v = Ty.freshVar(rank(s))
    Array.push(s.pools[len(s.pools) - 1], v)
    v
}

-- Take responsibility for variables generation invented. They were created
-- without a rank -- generation has no idea what rank it is under -- so they are
-- stamped and pooled here.
fun adopt(s : Solver, vars : Array VarCell) -> Unit {
    for v in vars {
        v.level = rank(s)
        Array.push(s.pools[len(s.pools) - 1], v)
    }
}

-- -- families ----------------------------------------------------------------------

-- A family application's definition, from an assumption first and an instance
-- after.
--
-- A *given* equality is a reduction rule for the family it names, and this is
-- where it is used. Discharging `Item c ~ Op` is not enough on its own: the
-- body's `match op { Inc(n) -> ... }` cannot find its constructors unless
-- `Item c` genuinely *becomes* `Op`, and `Item c` over a skolem will never
-- reduce through the instance table. So the assumptions are consulted first, and
-- the rule they supply is applied exactly as an instance's would be.
fun reduceFam(s : Solver, t : Ty.Type) -> Option Ty.Type {
    for p in s.assumptions {
        -- Reflexive rules are skipped, not returned: a rule from a family
        -- application to itself is no rule, and returning it would leave
        -- `Field.tag Auto` unreduced next to the `Int` it is.
        if p.name == Ty.equals && Ty.sameType(p.args[0], t)
                && !Ty.sameType(p.args[1], t) {
            return Some(p.args[1])
        }
    }
    -- Without the wanted rules. This reducer is what `unify` calls, and a
    -- unification is irreversible: reducing `Container.Elem a` to `Int` by an
    -- equation nobody has proved yet *binds* a variable to `Int`, and the
    -- family is then gone from the scheme that was supposed to carry it. The
    -- rules answer whether a predicate can be discharged, which `classRule`
    -- asks explicitly; they do not decide what a type is.
    match t {
        TFam(name, arg, _) -> Classes.settledFam(s.classes, name, arg)
        _ -> None
    }
}

-- Take an equation unification could not decide.
--
-- It becomes an equality *predicate* rather than joining a queue of its own.
-- That is the whole of SPEC-DELTAS.md 39: a stuck equation and a deferred
-- predicate are waiting on the same thing, so the binder that can carry one can
-- carry the other.
--
-- Before queueing it, ask whether it *could* ever be decided: a family over a
-- constructor no instance covers is stuck for good, and the equation's own span
-- is the best place to say so. A family over a variable is merely waiting.
fun defer(s : Solver, a : Ty.Type, b : Ty.Type, where : Option Span,
          context : String) -> Unit {
    for t in [a, b] {
        match t {
            TFam(name, arg, _) -> match Classes.uncovered(s.classes, name, arg) {
                -- A skolem is a nullary constructor, so a family over one looks
                -- exactly like a family over a type whose instance is missing.
                -- It is not: the declaration assumed that instance, and which
                -- one it will be is the caller's business. Such an equation is
                -- merely stuck, and is reported as stuck.
                Some(missing) -> if !granted(s, missing) {
                    oops("no instance for '" + Ty.showPred(missing) + "', so '"
                         + Ty.show(t) + "' has no definition", where)
                }
                None -> {}
            }
            _ -> {}
        }
    }
    -- Oriented family-first, so a carried equality reads the way a written one
    -- has to be written: `Item a ~ Op`, never `Op ~ Item a`. Nothing depends on
    -- the order, but a scheme is read by people.
    let flip = isFam(Ty.prune(b)) && !isFam(Ty.prune(a))
    let left = if flip { b } else { a }
    let right = if flip { a } else { b }
    Array.push(s.deferred, PredCell {
        pred = Ty.pred(Ty.equals, [left, right]), span = where, context = context,
    })
    -- And it is a rewrite rule from here on. `x.pos` used where an `Int` is
    -- wanted leaves `Field.pos a ~ Int`, and without this every predicate about
    -- the field's type stays stuck and rides into the scheme, while
    -- exhaustiveness reads a scrutinee type the equality would have decided.
    --
    -- Only a rule that makes progress. `Field.pos a ~ Int` says what the family
    -- is; `Field.bucket c ~ Field.next b` -- which `c.bucket = b.next` produces
    -- -- says only that two stuck families agree, and rewriting one to the
    -- other is a step sideways that can step back. A bare variable on the right
    -- teaches nothing either.
    let l = Ty.prune(left)
    let rt = Ty.prune(right)
    var teaches = isFam(l)
    match rt {
        TFam(_, _, _) -> { teaches = False }
        TVar(_) -> { teaches = False }
        _ -> {}
    }
    if teaches { Array.push(s.classes.rules, (l, rt)) }
}

fun isFam(t : Ty.Type) -> Bool = match t {
    TFam(_, _, _) -> True
    _ -> False
}

-- -- solving -----------------------------------------------------------------------

fun run(s : Solver, c : Constraint) -> Unit {
    solve(s, c)
    settle(s)
    -- The outermost boundary: nothing further can narrow what is left, so
    -- anything still open that has a default takes it, and the rest is an error.
    var going = True
    while going && len(s.deferred) > 0 {
        if applyDefaults(s, Array.slice(s.deferred, 0, len(s.deferred))) {
            settle(s)
        } else {
            going = False
        }
    }
    rejectStranded(s, s.deferred)
}

fun solve(s : Solver, c : Constraint) -> Unit = match c {
    CEq(left, right, where, context) ->
        Ty.unify(left, right, where, context, families(s))
    CPred(cell) -> if !entail(s, cell) { Array.push(s.deferred, cell) }
    CAnd(parts) -> for p in parts { solve(s, p) }
    CExists(vars, body) -> {
        adopt(s, vars)
        solve(s, body)
    }
    CAssume(givens, body) -> {
        let saved = Array.slice(s.assumptions, 0, len(s.assumptions))
        for (_, p) in givens { Array.push(s.assumptions, p) }
        let assumed = newScope()
        assumed.givens = givens
        Array.push(s.scopes, assumed)
        solve(s, body)
        s.assumptions = saved
        discard(Array.pop(s.scopes))
    }
    CDef(binds, body, top) -> {
        let saved = s.env
        s.env = childEnv(saved)
        for (name, ty) in binds {
            let b = Binding { scheme = Ty.mono(ty), mutable = top }
            define(s.env, name, b)
            if top { define(s.topLevel, name, b) }
        }
        solve(s, body)
        s.env = saved
    }
    CBind(binds, body, top) -> {
        let saved = s.env
        s.env = childEnv(saved)
        for (name, sch) in binds {
            let b = Binding { scheme = sch, mutable = top }
            define(s.env, name, b)
            if top { define(s.topLevel, name, b) }
        }
        solve(s, body)
        s.env = saved
    }
    CLet(cell) -> solveLet(s, cell)
    CInstance(name, ty, where, use) -> solveInstance(s, name, ty, where, use)
}

-- `name <= t`: instantiate, and re-emit the scheme's obligations.
--
-- A scheme's predicates are demands on whoever instantiates it, so they come
-- back as constraints over this site's fresh variables. The verb is "read": a
-- scheme records which field it needs, not whether the access that produced the
-- demand was a read or an assignment.
fun solveInstance(s : Solver, name : String, ty : Ty.Type, where : Option Span,
                  use : Option Use) -> Unit {
    let binding = match lookup_(s.env, name) {
        Some(b) -> b
        None -> oops("generation let '" + name + "' through unbound", where)
    }
    let (preds, instantiated, typeArgs) =
        Ty.instantiateQual(binding.scheme, fun() = fresh(s))
    Ty.unify(instantiated, ty, where, "", families(s))
    match use {
        Some(u) -> {
            -- Only the class predicates: `HasField` and `OneOf` are discharged
            -- by a lookup and a decision, and leave nothing behind to pass.
            let kept = Array.new(len(preds))
            for p in preds {
                if Classes.isClass(s.classes, p.name) { Array.push(kept, p) }
            }
            u.preds = kept
            -- Every type argument, though, and unfiltered: a type application
            -- has to name one per quantified variable or it is not the scheme's
            -- instantiation. They are recorded before unification has run to
            -- completion and are variables at this point, which is correct --
            -- what they turn out to be is read back after solving.
            u.typeArgs = typeArgs
            u.scopes = Array.slice(s.scopes, 0, len(s.scopes))
            Array.push(s.uses, u)
        }
        None -> {}
    }
    for p in preds { solve(s, cpredOf(p, where, "read")) }
}

-- Solve the definition one rank in, generalize, then solve the body.
--
-- The order matters and is the reason this is one function. Predicates are
-- settled *before* the split, so that anything the definition learned has been
-- applied; the split then uses the same test generalization uses for type
-- variables, and the pool is emptied either into the schemes or into the
-- parent's pool.
fun solveLet(s : Solver, c : LetCell) -> Unit {
    Array.push(s.pools, Array.new(8))
    -- Now that the rank exists, the constants that belong to it can say so.
    -- And only while it exists: this is the whole of a skolem's life, so the
    -- stamp is lifted on the way out -- not tidiness, but correctness.
    -- Exhaustiveness unifies constructor types against a solved scrutinee long
    -- afterwards, at no rank at all, and a constant still claiming a rank there
    -- would look like an escape.
    for con in c.skolems { con.level = rank(s) }
    letBody(s, c)
    for con in c.skolems { con.level = Ty.noScope }
}

fun letBody(s : Solver, c : LetCell) -> Unit {
    let scope = newScope()
    Array.push(s.scopes, scope)
    let firstUse = len(s.uses)
    solve(s, c.defn)
    discard(Array.pop(s.scopes))
    settle(s)
    let types = Array.new(len(c.binds))
    for (_, ty) in c.binds { Array.push(types, ty) }
    let retainedPreds = split(s, types)
    -- An equation tied to a variable this binder quantifies can never be decided
    -- by an enclosing one -- nothing outside mentions the variable -- so this is
    -- where it is stuck for good.
    match Array.pop(s.pools) {
        Some(young) -> dischargePool(s, young)
        None -> {}
    }

    -- The class predicates are shared across the group, and the dictionary
    -- parameters with them. One member's body may call another's, so a per-name
    -- context would leave that call needing a dictionary the caller's own
    -- signature never promised. Everything else stays per name: a `HasField` is
    -- erased, so nothing has to agree about it.
    let classPreds = Array.new(4)
    for p in retainedPreds {
        if Classes.isClass(s.classes, p.pred.name) { Array.push(classPreds, p.pred) }
    }
    let shared = Classes.simplify(s.classes, classPreds)
    let givens = Array.new(len(shared))
    for p in shared { Array.push(givens, (dictName(p.name), p)) }
    scope.givens = givens
    -- A call from one member of the group to another -- or to itself -- was
    -- solved against the *monomorphic* placeholder `CDef` bound, so it demanded
    -- nothing and would be handed the undischarged binding at run time. It needs
    -- the group's own dictionaries, which is exactly what the scope now holds.
    let group = Set.new()
    for (name, _) in c.binds { Set.add(group, name) }
    if len(shared) > 0 {
        for var i = firstUse; i < len(s.uses); i = i + 1 {
            let u = s.uses[i]
            if len(u.preds) == 0 && Set.contains(group, u.name)
               && holdsScope(u.scopes, scope) {
                u.preds = shared
            }
        }
    }
    match c.dicts {
        Some(a) -> {
            let params = Array.new(len(givens))
            for (n, _) in givens { Array.push(params, n) }
            a.params = params
            a.preds = shared
        }
        None -> {}
    }

    let saved = s.env
    s.env = childEnv(saved)
    for (name, ty) in c.binds {
        let own = Array.new(4)
        for p in constrain(retainedPreds, ty) {
            if !Classes.isClass(s.classes, p.name) { Array.push(own, p) }
        }
        let preds = Classes.simplify(s.classes, Array.append(own, shared))
        -- Reduced at every level, not just the head. A field access has the
        -- type `Field.n r`, and once `r` is a known record that family *is*
        -- the field's type -- leaving it unreduced would put machinery in a
        -- signature a reader has to read. Head-only reduction is right for
        -- unification and not for anything that reads a type whole, which a
        -- scheme is.
        -- Reduced without the wanted rules. A scheme is an interface: it
        -- carries `Container.Elem a ~ Int` and its body should say
        -- `Container.Elem a`, not the `Int` that equation happens to make it.
        -- The rules are for discharging predicates inside the body, not for
        -- restating the body in terms a caller has not been told yet.
        let was = s.classes.rulesApply
        s.classes.rulesApply = False
        let body = Typed.reduceDeep(ty, families(s))
        s.classes.rulesApply = was
        let b = Binding {
            scheme = Ty.generalize(body, rank(s), preds), mutable = False,
        }
        match c.dicts {
            -- Recorded here because here is the only place it exists: a local
            -- binding's scheme goes into an environment popped as soon as the
            -- body has been solved.
            Some(a) -> Map.put(a.schemes, name, b.scheme)
            None -> {}
        }
        define(s.env, name, b)
        if c.topLevel { define(s.topLevel, name, b) }
    }
    solve(s, c.body)
    s.env = saved
}

-- Python asks `any(s is scope for s in use.scopes)`. There is no identity to
-- compare here, so a scope carries one.
fun holdsScope(scopes : Array Scope, wanted : Scope) -> Bool {
    for sc in scopes {
        if sc.id == wanted.id { return True }
    }
    False
}

-- The predicates the schemes about to be built will carry.
--
-- Defaulting sits between splitting and reporting, because ambiguity is
-- precisely the condition that licenses it: a predicate no scheme can carry
-- mentions a variable that appears in no type being generalized, so no use site
-- will ever pin it. Choosing for one can unblock another, so the split is redone
-- until it stops moving.
fun split(s : Solver, types : Array Ty.Type) -> Array PredCell {
    var going = True
    var out = Array.new(0)
    while going {
        let retainedPreds = retained(s, rank(s) - 1)
        let stranded = unattributed(retainedPreds, types)
        if len(stranded) == 0 || !applyDefaults(s, stranded) {
            rejectStranded(s, stranded)
            out = retainedPreds
            going = False
        } else {
            -- Something was decided. Put the split back and settle again, so the
            -- choice is visible to everything that was waiting on it.
            for p in retainedPreds { Array.push(s.deferred, p) }
            settle(s)
        }
    }
    out
}

-- Hand every variable born at the rank just left to its new owner.
--
-- One still at that rank belongs to the scheme about to be built, and
-- `generalize` will quantify it. One unification has lowered escaped into an
-- enclosing binding, and the parent pool adopts it so that the *next* binder
-- sees it as its own. Doing this structurally is the point of the pools: there
-- is no place left to forget it.
fun dischargePool(s : Solver, young : Array VarCell) -> Unit {
    let left = rank(s) + 1
    for v in young {
        match Ty.prune(TVar(v)) {
            -- Bound during solving; unification adjusted what it points at.
            TVar(pruned) -> if pruned.level < left {
                Array.push(s.pools[len(s.pools) - 1], pruned)
            }
            _ -> {}
        }
    }
}

-- -- predicates --------------------------------------------------------------------

-- Retry deferred predicates until no further one can be discharged.
--
-- Each round re-solves what is left; a predicate that is still stuck goes
-- straight back on the queue. Discharging one can bind variables another was
-- waiting on, which is why this iterates -- and why it may stop only when a
-- round discharges nothing.
--
-- Equalities go first within a round: a family that has become reducible decides
-- a type, and a predicate waiting on that type can then settle in the same
-- round.
fun settle(s : Solver) -> Unit {
    while len(s.deferred) > 0 {
        improve(s)
        let pending = Array.sortBy(s.deferred, fun(a, b) = {
            let ka = if a.pred.name == Ty.equals { 0 } else { 1 }
            let kb = if b.pred.name == Ty.equals { 0 } else { 1 }
            Int.compare(ka, kb)
        })
        s.deferred = Array.new(8)
        for c in pending { solve(s, CPred(c)) }
        if len(s.deferred) >= len(pending) { return {} }
    }
}

-- Improvement: what the deferred set decides between two of its own members.
--
-- There used to be a third rule here, equating the results of two demands for
-- one field of one receiver -- `HasField l r a` and `HasField l r b` forcing
-- `a ~ b` -- because a record type has exactly one type for a given field and
-- with the field type in a third argument nothing else said so. In Gaster &
-- Jones the rule does not exist, since with rows the field's type sits inside
-- the receiver's and two accesses agree by ordinary unification.
--
-- It is gone for the same reason: `r.f` has the type `Field.f r`, so two reads
-- are the same type expression rather than two variables, and unification
-- equates them without being told to.
fun improve(s : Solver) -> Unit {
    improveNumeric(s)
    improveFamilies(s)
}

-- Equate the answers of two equalities on one family application.
--
-- The same rule as `improve`'s, and for the same reason: a family is a
-- *function* of its argument, so `Item c ~ Op` and `Item c ~ Char` cannot both
-- hold. Neither is discharged while `Item c` is stuck, so without this they
-- would both be carried and the contradiction would never be reported at all.
fun improveFamilies(s : Solver) -> Unit {
    let seen = Map.new()
    for c in s.deferred {
        if c.pred.name == Ty.equals {
            let left = c.pred.args[0]
            if isFam(Ty.prune(left)) {
                let key = Ty.typeKey(left)
                match Map.get(seen, key) {
                    Some((first, at)) -> Ty.unify(first, c.pred.args[1],
                        firstSpan(c.span, at),
                        "the answers given for '" + Ty.show(left) + "'",
                        families(s))
                    None -> Map.put(seen, key, (c.pred.args[1], c.span))
                }
            }
        }
    }
}

fun firstSpan(a : Option Span, b : Option Span) -> Option Span = match a {
    Some(_) -> a
    None -> b
}

-- Try to discharge one predicate. `False` means "not yet" -- defer it. An
-- unsatisfiable predicate reports; only genuine lack of information answers
-- `False`, so nothing can be quietly dropped.
fun entail(s : Solver, c : PredCell) -> Bool {
    if c.pred.name == Ty.equals { return equals(s, c) }
    if c.pred.name == oneOf { return oneOfRule(s, c) }
    if Classes.isClass(s.classes, c.pred.name) { return classRule(s, c) }
    oops("no rule for predicate '" + c.pred.name + "'", c.span)
}

-- `s ~ t`: retry the equation now that something may have moved.
--
-- Normalizing is what makes progress: reduction consults the assumptions and
-- then the instance table, so a side that has become reducible reduces here.
-- Two identical family applications are equal by reflexivity, and a side that is
-- *still* a stuck family means the equation is not yet decidable.
fun equals(s : Solver, c : PredCell) -> Bool {
    -- Without the wanted rules: an equation proved by a rule it supplied is
    -- proved by itself. See `Classes.settled`.
    let left = Classes.settled(s.classes, c.pred.args[0])
    let right = Classes.settled(s.classes, c.pred.args[1])
    c.pred = Ty.pred(c.pred.name, [left, right])
    if Ty.sameType(left, right) { return True }
    if isFam(left) || isFam(right) { return False }
    Ty.unify(left, right, c.span, c.context, families(s))
    True
}

-- Is this a fact the enclosing declaration already assumed?
--
-- Both sides are normalized before they are compared, since an assumption is
-- written down and a demand is discovered, and the two may name the same type by
-- different routes.
fun granted(s : Solver, p : Pred) -> Bool {
    let key = Ty.predKey(Ty.pred(p.name,
        [Classes.normalize(s.classes, p.args[0])]))
    for a in s.assumptions {
        for q in Classes.bySuper(s.classes, a) {
            let qk = Ty.predKey(Ty.pred(q.name,
                [Classes.normalize(s.classes, q.args[0])]))
            if qk == key { return True }
        }
    }
    False
}

-- `C t`: an assumption grants it, or an instance covers it.
--
-- A predicate whose argument is still headed by a variable defers -- a later
-- unification may yet decide which instance applies. One headed by anything
-- rigid is decided here and now, which is what keeps a missing instance a local
-- error naming the type that lacks one.
fun classRule(s : Solver, c : PredCell) -> Bool {
    let t = Classes.normalize(s.classes, c.pred.args[0])
    match t {
        -- Absorbed; there is no value to find a method for.
        TBottom -> return True
        _ -> {}
    }
    let p = Ty.pred(c.pred.name, [t])
    -- Assumptions are consulted before the shape of the argument is, and that
    -- order matters: a given may be *about* a family application, as
    -- `[Container c, Show (Elem c)]` is, and `Elem c` over a skolem never
    -- reduces. Testing the head first would defer such a predicate for ever.
    if granted(s, p) { return True }
    let (h, _) = Ty.spine(t)
    match h {
        TVar(_) -> return False
        TFam(_, _, _) -> return False
        _ -> {}
    }
    match Classes.byInst(s.classes, p) {
        None -> {
            if Classes.isGenerated(p.name) { noField(s, p, t, c) }
            oops("no instance for '" + Ty.showPred(p) + "'", c.span)
        }
        Some(obligations) -> {
            -- The instance's own context becomes this site's, over the types the
            -- match supplied: `Eq (Array a)` leaves `Eq a` behind.
            for q in obligations { solve(s, cpredOf(q, c.span, c.context)) }
            True
        }
    }
}

-- Why a generated field or projection class has no instance.
--
-- Ordinary instance resolution would say "no instance for `%HasField.cap Int`",
-- which names a class the programmer cannot see and did not write. These are
-- the messages the structural discharge used to produce, kept word for word:
-- the class is an implementation of field access, so its failures have to read
-- as field errors.
fun noField(s : Solver, p : Pred, receiver : Ty.Type, c : PredCell) -> Unit {
    let (h, _) = Ty.spine(receiver)
    match Classes.generatedLabel(p.name) {
        Some(label) -> {
            match h {
                TCon(con) -> match Decls.recordFields(s.decls, con.name) {
                    Some(names) -> if !Array.contains(names, label) {
                        oops("type '" + con.name + "' has no field '" + label
                             + "' (it has: " + String.join(names, ", ") + ")",
                             c.span)
                    }
                    None -> {}
                }
                _ -> {}
            }
            oops("cannot " + c.context + " field '" + label + "': '"
                 + Ty.show(receiver) + "' is not a single-variant record type. "
                 + "Multi-variant types are immutable and are taken apart with "
                 + "'match'.", c.span)
        }
        None -> {}
    }
    let index = match Classes.generatedIndex(p.name) {
        Some(found) -> found
        None -> return
    }
    let choices = match receiver {
        TTuple(elems) -> Some(elems)
        _ -> Decls.projectionTypes(s.decls, receiver)
    }
    match choices {
        None -> oops("cannot project position " + Int.toString(index) + " from '"
                     + Ty.show(receiver) + "': numeric projection requires a "
                     + "tuple or an immutable type with exactly one positional "
                     + "constructor", c.span)
        Some(elems) -> oops("projection index " + Int.toString(index)
                            + " is out of bounds for '" + Ty.show(receiver)
                            + "', which has " + Int.toString(len(elems))
                            + " element(s)", c.span)
    }
}

-- `OneOf t {...}`: `t` must be one of a closed set of built-in types.
--
-- A **singleton set is an equation** and is discharged as one, right here, at
-- the moment the constraint is reached. That is not an optimization: deferring
-- it would let a later unification bind `t` to something else first, and the
-- mismatch would then be reported against the literal rather than against
-- whatever disagreed with it.
fun oneOfRule(s : Solver, c : PredCell) -> Bool {
    let names = match c.pred.args[1] {
        TSet(ns) -> ns
        _ -> oops("a numeric demand's second argument is not a set", c.span)
    }
    if len(names) == 0 {
        oops("this numeric literal is not representable in any numeric type",
             c.span)
    }
    if len(names) == 1 {
        -- Same argument order as the equation this stands in for, so a mismatch
        -- reads the way it did before literals had sets.
        Ty.unify(Ty.prune(c.pred.args[0]), Ty.numericType(names[0]), c.span,
                 c.context, families(s))
        return True
    }
    let t = Classes.normalize(s.classes, c.pred.args[0])
    match t {
        -- Absorbed; there is no value to represent.
        TBottom -> return True
        -- Still open -- improvement or defaulting will decide.
        TVar(_) -> return False
        TFam(_, _, _) -> return False
        TCon(con) -> if Array.contains(names, con.name) { return True }
        _ -> {}
    }
    -- The span is always the literal's, so name it: "expected one of ..." alone
    -- reads as though something at this position asked for a numeric type, when
    -- in fact this position *is* the numeral.
    oops("a numeric literal cannot have type '" + Ty.show(t) + "'; it must be "
         + "one of " + String.join(Ty.sortNumeric(names), ", "), c.span)
}

-- Intersect `OneOf` sets that constrain the same variable.
--
-- Two demands on one variable are one demand on the intersection, and an empty
-- intersection is an error rather than a deferral -- nothing later can widen a
-- closed set. Narrowing to a singleton is what lets the next round discharge it
-- as an equation.
fun improveNumeric(s : Solver) -> Unit {
    let merged = Map.new()
    let out = Array.new(len(s.deferred))
    for c in s.deferred {
        if c.pred.name != oneOf {
            Array.push(out, c)
            continue
        }
        let key = Ty.typeKey(c.pred.args[0])
        match Map.get(merged, key) {
            None -> {
                Map.put(merged, key, c)
                Array.push(out, c)
            }
            Some(first) -> {
                let names = intersect(setNames(first.pred.args[1], first.span),
                                      setNames(c.pred.args[1], c.span))
                if len(names) == 0 {
                    oops("no numeric type is both "
                         + Ty.show(first.pred.args[1]) + " and "
                         + Ty.show(c.pred.args[1]), c.span)
                }
                first.pred = Ty.pred(first.pred.name,
                                     [first.pred.args[0], TSet(names)])
            }
        }
    }
    s.deferred = out
}

fun setNames(t : Ty.Type, where : Option Span) -> Array String = match t {
    TSet(ns) -> ns
    _ -> oops("a numeric demand's second argument is not a set", where)
}

fun intersect(a : Array String, b : Array String) -> Array String {
    let out = Array.new(len(a))
    for x in a {
        if Array.contains(b, x) { Array.push(out, x) }
    }
    out
}

-- Resolve `OneOf`s that nothing will ever narrow, by picking a member.
--
-- Haskell's defaulting, for the same reason: the variable is ambiguous -- it
-- appears in no type being generalized, so no use site can pin it and no scheme
-- can carry it. The choice is the first member of the set in tower order.
--
-- Only `OneOf` defaults. A stranded `HasField` is a genuine error: there is no
-- preferred record type to guess.
fun applyDefaults(s : Solver, stuck : Array PredCell) -> Bool {
    var progress = False
    for c in stuck {
        if c.pred.name == oneOf {
            let names = setNames(c.pred.args[1], c.span)
            var chosen = False
            for n in Ty.numericOrder() {
                if Array.contains(names, n) && !chosen {
                    Ty.unify(Ty.prune(c.pred.args[0]), Ty.numericType(n), c.span,
                             c.context, families(s))
                    chosen = True
                    progress = True
                }
            }
        }
    }
    progress
}

-- Deferred predicates general enough to be quantified at `level`.
--
-- The test is the one generalization applies to type variables: a predicate tied
-- to a variable created deeper than the binder travels in the scheme; one tied
-- to an outer variable stays behind for an enclosing binding to settle.
fun retained(s : Solver, level : Int) -> Array PredCell {
    let keep = Array.new(len(s.deferred))
    let out = Array.new(len(s.deferred))
    let seen = Set.new()
    for c in s.deferred {
        if Ty.predLevel(c.pred) <= level {
            Array.push(keep, c)
        } else {
            -- Two demands for the same field of the same type are one
            -- obligation; the context should not repeat itself.
            let key = Ty.predKey(c.pred)
            if !Set.contains(seen, key) {
                Set.add(seen, key)
                Array.push(out, c)
            }
        }
    }
    s.deferred = keep
    out
}

-- Report a predicate that can be neither discharged nor quantified.
fun rejectStranded(s : Solver, stranded : Array PredCell) -> Unit {
    if len(stranded) == 0 { return {} }
    let c = stranded[0]
    if c.pred.name == Ty.equals {
        -- An equality that reaches here is not wrong, it is undecidable, and the
        -- blame goes on the family rather than on what it was compared with:
        -- what is missing is the knowledge of which instance defines it. A
        -- scheme would have carried it happily, so getting this far means no
        -- type being generalized mentions the family's argument.
        let left = c.pred.args[0]
        let right = c.pred.args[1]
        let leftIsFam = isFam(Ty.prune(left))
        let fam = if leftIsFam { left } else { right }
        let other = if leftIsFam { right } else { left }
        -- A generated field family is machinery, not something a context can
        -- state: a reader wrote `r.n` and cannot write `Field.n r ~ Int`. So it
        -- keeps the message the stranded `HasField` predicate used to give,
        -- which names what was written.
        let owner = match fam {
            TFam(name, _, _) -> Classes.fieldFamilyOwner(name)
            _ -> "?"
        }
        match Classes.generatedLabel(owner) {
            Some(label) -> oops("cannot determine the type of the value whose "
                + "field '" + label + "' is being accessed. Add a type "
                + "annotation.", c.span)
            None -> {}
        }
        match Classes.generatedIndex(owner) {
            Some(index) -> oops("cannot determine the type of the value being "
                + "projected at position " + Int.toString(index)
                + ". Add a type annotation.", c.span)
            None -> {}
        }
        let cls = match fam {
            TFam(name, _, _) -> match Map.get(s.decls.families, name) {
                Some(fi) -> Ty.shortName(fi.cls)
                None -> name
            }
            _ -> "?"
        }
        let where = if c.context == "" { "" } else { " in " + c.context }
        -- Name the remedy: since SPEC-DELTAS.md 39 it is no longer "add a type
        -- annotation", because an equality is something a *context* may state.
        let equality = Ty.show(fam) + " ~ " + Ty.show(other)
        oops("cannot reduce '" + Ty.show(fam) + "' to '" + Ty.show(other) + "'"
             + where + ": nothing says which '" + cls + "' instance defines it. "
             + "Add '" + equality + "' to the context.", c.span)
    }
    match Classes.generatedLabel(c.pred.name) {
        Some(label) -> oops("cannot determine the type of the value whose field '"
             + label + "' is being accessed. Add a type annotation.", c.span)
        None -> {}
    }
    match Classes.generatedIndex(c.pred.name) {
        Some(index) -> oops("cannot determine the type of the value being "
             + "projected at position " + Int.toString(index)
             + ". Add a type annotation.", c.span)
        None -> {}
    }
    oops("cannot determine a type satisfying '"
         + Ty.showPredWith(c.pred, Ty.namer("")) + "'. Add a type annotation.",
         c.span)
}

-- -- context splitting -------------------------------------------------------------

-- The variables `types` pin, following predicates to a fixed point.
--
-- Membership is transitive because a `HasField` is a *function* of its receiver:
-- fix the record and the field type follows. So a variable that appears nowhere
-- in the type is still determined, as long as some chain of predicates connects
-- it to one that is. That functional reading is also why the usual ambiguity
-- objection to a variable appearing only in the context does not apply here.
fun reach(preds : Array PredCell, types : Array Ty.Type) -> Set.Set Int {
    let ids = Set.new()
    for v in Ty.varsOf(types) { Set.add(ids, v.id) }
    var changed = True
    while changed {
        changed = False
        for c in preds {
            let reachable = Ty.varsOf(c.pred.args)
            var touches = False
            for v in reachable {
                if Set.contains(ids, v.id) { touches = True }
            }
            if touches {
                for v in reachable {
                    if !Set.contains(ids, v.id) {
                        Set.add(ids, v.id)
                        changed = True
                    }
                }
            }
        }
    }
    ids
}

-- The retained predicates that constrain one type.
fun constrain(preds : Array PredCell, t : Ty.Type) -> Array Pred {
    let ids = reach(preds, [t])
    let out = Array.new(len(preds))
    for c in preds {
        if touches(c, ids) { Array.push(out, c.pred) }
    }
    out
}

-- The retained predicates no scheme in the group will carry.
fun unattributed(preds : Array PredCell, types : Array Ty.Type) -> Array PredCell {
    let ids = reach(preds, types)
    let out = Array.new(len(preds))
    for c in preds {
        if !touches(c, ids) { Array.push(out, c) }
    }
    out
}

fun touches(c : PredCell, ids : Set.Set Int) -> Bool {
    for v in Ty.varsOf(c.pred.args) {
        if Set.contains(ids, v.id) { return True }
    }
    False
}

fun oops(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("type error", message), "")
    Some(s) -> Diag.fail(Diag.at("type error", message, s), s.file)
}

fun discard(x : a) -> Unit = {}
