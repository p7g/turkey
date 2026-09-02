module Turkey.Types (
    Kind(..), KCell(..),
    kstar, kvar, kprune, unifyKinds, defaultKind, kindArrow, showKind,
    VarCell(..), Type(..), Fresh,
    noScope, ground, equals,
    newVar, freshVar, tvar,
    shortName, qualify, qualifyAll, clearQualified, display,
    tInt, tByte, tFloat, tString, tChar, tBool, tUnit,
    primitives, isPrimitive,
    intMin, intMax, byteMin, byteMax, charMax, isScalarValue,
    tArray, tRawArray, arrayOf, rawArrayOf,
    kindOf, applyTo, spine, headCon,
    numericOrder, intLiteralSet, floatLiteralSet, numericType, sortNumeric,
    Pred(..), pred, predKey, predLevel,
    Scheme(..), mono, scheme,
    Families(..),
    prune, normalize, occursAndAdjust, escaping,
    unify, join, generalize, instantiate, instantiateQual,
    typeKey, subterms, varsOf, freeVars,
    Namer(..), namer, namerFrom, show, showAt, showScheme, showPred,
)

-- Semantic types and unification (design.md 4, `turkey/types.py`).
--
-- Unification is Algorithm J's: type variables are mutable cells, so it updates
-- types in place and there is no substitution to thread around. Each variable
-- carries a `level` -- its rank, in Remy's sense -- recording the binder depth
-- it was created under, which makes generalization a cheap comparison rather
-- than a scan of the whole environment. Ranks are assigned by the solver as it
-- descends; nothing here decides what a variable's rank should be.
--
-- The one departure from textbook Hindley-Milner is the bottom type. Section
-- 4.3 says bottom is absorbed by whatever it meets, so unification treats it as
-- a no-op. That is not enough on its own -- see `join`.
--
-- Type application is *curried* even though the function type is not: `Array
-- Int` is `TApp(TCon "Array", Int)`, so a variable can stand in head position
-- and `Functor f` has something to quantify over. `TFun` stays a separate
-- uncurried node because it is the language's fixed-arity function type, not a
-- constructor that happens to take two arguments.
--
-- The port's one structural change: Python's mutable cells are objects with an
-- identity, and Turkey has none, so a variable is a single-variant *record*
-- (mutable, with an `id`) carried inside the immutable variant -- the same
-- shape `Turkey.Ast` uses for the same reason. Wherever the Python writes
-- `a is b` this compares ids, or structure via `typeKey`.

import Data.Set as Set
import Turkey.Diag as Diag
import Turkey.Source (Span)

-- -- kinds --------------------------------------------------------------------------
--
-- A kind is the type of a type. `Int :: *`, `Array :: * -> *`, and `Functor f`
-- demands `f :: * -> *`. Kinds exist for exactly one reason: with application
-- curried, arity is no longer syntactic, and something has to say that `Array`
-- alone is not a type while `Array Int` is.
--
-- Kind variables are inferred the way type variables are -- mutable cells,
-- union-find, occurs check -- because a type declaration's parameter kinds are
-- not written down and have to be discovered from the bodies. Kinds are not
-- polymorphic: whatever is unresolved once the declarations are read defaults
-- to `*`, which is Haskell 98's rule and keeps a kind a first-order term.

type KCell = KCell {
    id  : Int,
    ref : Option Kind,
}

type Kind
    -- `*`, the kind of types that classify values.
    = KStar
    -- `k1 -> k2`, the kind of a constructor awaiting an argument.
    | KFun(Kind, Kind)
    -- A kind not yet decided.
    | KVar(KCell)

fun kstar() -> Kind = KStar

fun kvar() -> Kind {
    counters.kind = counters.kind + 1
    KVar(KCell { id = counters.kind, ref = None })
}

fun kprune(k : Kind) -> Kind = match k {
    KVar(cell) -> match cell.ref {
        None -> k
        Some(inner) -> {
            let settled = kprune(inner)
            cell.ref = Some(settled)
            settled
        }
    }
    _ -> k
}

fun koccurs(slot : KCell, k : Kind) -> Bool = match kprune(k) {
    KVar(cell) -> cell.id == slot.id
    KFun(arg, res) -> koccurs(slot, arg) || koccurs(slot, res)
    KStar -> False
}

-- Match two kinds, binding variables. `False` on mismatch -- the caller has the
-- span and the words, so nothing is reported from here.
fun unifyKinds(a : Kind, b : Kind) -> Bool {
    let x = kprune(a)
    let y = kprune(b)
    match x {
        KVar(cell) -> match y {
            KVar(other) -> if cell.id == other.id { return True }
            _ -> {}
        }
        _ -> {}
    }
    match x {
        KVar(cell) -> {
            if koccurs(cell, y) { return False }
            cell.ref = Some(y)
            True
        }
        KStar -> match y {
            KVar(_) -> unifyKinds(y, x)
            KStar -> True
            KFun(_, _) -> False
        }
        KFun(xa, xr) -> match y {
            KVar(_) -> unifyKinds(y, x)
            KFun(ya, yr) -> unifyKinds(xa, ya) && unifyKinds(xr, yr)
            KStar -> False
        }
    }
}

-- Bind every variable still unresolved in `k` to `*`, and answer it.
fun defaultKind(k : Kind) -> Kind = match kprune(k) {
    KVar(cell) -> {
        cell.ref = Some(KStar)
        KStar
    }
    KFun(arg, res) -> {
        discard(defaultKind(arg))
        discard(defaultKind(res))
        KFun(arg, res)
    }
    KStar -> KStar
}

-- `* -> ... -> result`, with `n` arrows over fresh argument kinds.
fun kindArrow(n : Int, result : Kind) -> Kind {
    var k = result
    for var i = 0; i < n; i = i + 1 { k = KFun(kvar(), k) }
    k
}

fun showKind(k : Kind) -> String = match kprune(k) {
    KFun(arg, res) -> {
        let left = showKind(arg)
        let wrapped = match kprune(arg) {
            KFun(_, _) -> "(" + left + ")"
            _ -> left
        }
        wrapped + " -> " + showKind(res)
    }
    -- An undecided kind prints as the `*` it will default to.
    _ -> "*"
}

-- -- types --------------------------------------------------------------------------

-- A unification variable. `ref` is `None` while unbound.
--
-- Its kind is a fresh variable by default rather than `*`, because a variable
-- written in an annotation may turn out to stand for a constructor -- the `f`
-- of `Wrap f a` is discovered to be `* -> *` only by seeing it applied.
type VarCell = VarCell {
    id    : Int,
    level : Int,
    ref   : Option Type,
    kind  : Kind,
}

type Type
    = TVar(VarCell)
    -- A type constructor, *unapplied*: `Int`, `Array`, `Stack`. Arguments are
    -- not carried here -- `Array Int` is `TApp(TCon "Array", Int)`. A
    -- constructor is therefore rigid and variable-free, which is what lets
    -- `instantiate` return it untouched and what makes decomposing an
    -- application sound.
    --
    -- The `Int` is the level. `noScope` for a real constructor: `Int` is the
    -- same type at every rank, so it can be mentioned from anywhere. A
    -- *skolem* is not -- it is a constructor only for the length of one body,
    -- standing for a type the caller has yet to choose, so it carries the rank
    -- of the binder that made it and must not be seen from outside it.
    | TCon(String, Kind, Int)
    -- `f a` -- one type applied to one argument. Curried, so a *variable* can
    -- sit at the head. That is the whole point: `Functor f` needs an `f` to
    -- abstract over, and a saturated constructor has no sub-term for it to be.
    | TApp(Type, Type, Kind)
    -- `F t` -- an associated family applied to its class's parameter. A family
    -- is a *function* on types, which is why it cannot be a constructor at the
    -- head of an application: decomposing `f a ~ g b` pointwise is sound only
    -- because every head is rigid, and a family head is precisely the one that
    -- is not. Saturated by construction, and *not injective*.
    | TFam(String, Type, Kind)
    -- `fun(t1, ..., tn) -> t`. Uncurried, fixed arity.
    | TFun(Array Type, Type)
    | TTuple(Array Type)
    -- A field name lifted into the type language. Legal only in the first
    -- argument of `HasField`, and deliberately not kinded -- it exists so one
    -- predicate former can name any field, rather than the domain growing a
    -- former per label.
    | TLabel(String)
    -- A numeric projection index lifted into a predicate argument.
    | TIndex(Int)
    -- A closed set of constructor names, lifted into the type language. Legal
    -- only in the second argument of `OneOf`, and the counterpart of `TLabel`.
    -- Held sorted and deduplicated, so two sets are equal when their arrays
    -- are.
    | TSet(Array String)
    -- The type of an expression that never yields a value.
    | TBottom

-- A source of fresh unification variables. Whoever hands one of these out is
-- also responsible for recording the variables it makes, so that generalization
-- can later find them.
type Fresh = fun() -> VarCell

-- The `level` of a constructor that belongs to no binder -- every declared one.
-- Below every rank the solver hands out, so the escape test is false for it
-- without a special case.
let noScope = 0 - 1

-- A predicate mentioning no variables can be settled anywhere.
let ground = 1073741824

-- The name of the equality predicate (SPEC-DELTAS.md 39). It lives here, with
-- `Pred`, because a scheme carries it like any other; it is deliberately not a
-- class name, which is what keeps it erased.
let equals = "~"

-- Global counters. Python's are `itertools.count()` on the classes themselves.
-- The values never reach any output -- `show` names variables positionally --
-- so all that matters is that two live cells never share one.
type Counters = Counters {
    kind : Int,
    tyvar : Int,
}

let counters = Counters { kind = 0, tyvar = 0 }

fun newVar(level : Int, kind : Kind) -> VarCell {
    counters.tyvar = counters.tyvar + 1
    VarCell { id = counters.tyvar, level = level, ref = None, kind = kind }
}

fun freshVar(level : Int) -> VarCell = newVar(level, kvar())

fun tvar(level : Int) -> Type = TVar(freshVar(level))

-- -- names --------------------------------------------------------------------------

-- The part of an internal name a reader wrote. `Turkey.Modules` owns the `#`
-- separator this splits on.
fun shortName(name : String) -> String = match String.rsplitOnce(name, "#") {
    None -> name
    Some((_, after)) -> after
}

-- An internal name written the way a program would qualify it.
fun qualified(name : String) -> String = String.replace(name, "#", ".")

-- The internal names that must print qualified, because two modules declared
-- the same short one and a message that said `Node` twice would be worse than
-- one that says which is which (SPEC-DELTAS.md 43). It is a program-wide
-- property rather than a constructor's own, and the same constructor may exist
-- as more than one `TCon` value, so it cannot live on the type.
let qualifySet = Set.new()

fun qualify(name : String) -> Unit = Set.add(qualifySet, name)

fun qualifyAll(names : Array String) -> Unit {
    for n in names { Set.add(qualifySet, n) }
}

-- Emptied when a declaration table is built, because the set belongs to *a
-- program* and this compiler checks several in one process where the Python
-- checks one per interpreter. A leak between them makes a name print qualified
-- in a program that never had a clash, which is how this was found.
fun clearQualified() -> Unit {
    for n in Set.elements(qualifySet) { discard(Set.remove(qualifySet, n)) }
}

-- What a message shows for a constructor: its short name, unless two clash.
fun display(name : String) -> String =
    if Set.contains(qualifySet, name) { qualified(name) } else { shortName(name) }

-- -- the built-in constructors ------------------------------------------------------

fun tInt() -> Type = TCon("Int", KStar, noScope)
fun tByte() -> Type = TCon("Byte", KStar, noScope)
fun tFloat() -> Type = TCon("Float", KStar, noScope)
fun tString() -> Type = TCon("String", KStar, noScope)
fun tChar() -> Type = TCon("Char", KStar, noScope)
-- Declared in the Prelude as `type Bool = False | True`; this still names it,
-- because a constructor is compared by name.
fun tBool() -> Type = TCon("Data.Bool.Type#Bool", KStar, noScope)
fun tUnit() -> Type = TCon("Unit", KStar, noScope)

-- The types the checker seeds itself with, and therefore also the ones a
-- program may not redefine. `Bool` is deliberately absent: it is declared in
-- the Prelude like any other type.
let primitiveNames = ["Int", "Byte", "Float", "String", "Char", "Unit"]

fun isPrimitive(name : String) -> Bool = Array.contains(primitiveNames, name)

fun primitives() -> Array String = primitiveNames

-- The ranges the primitives actually have. `Int` is two's-complement 64-bit and
-- `Byte` is unsigned 8-bit on every target; neither is "whatever the host does"
-- (PRIMITIVES.md 1, 2).
let intMin = 0 - 9223372036854775807 - 1
let intMax = 9223372036854775807
let byteMin = 0
let byteMax = 255

-- A `Char` is a Unicode *scalar value*, not a code point: the surrogate range
-- is excluded, which is what makes "a `String` is well-formed UTF-8" an
-- invariant no sequence of `Char`s can break (PRIMITIVES.md 5).
let charMax = 1114111
let surrogateMin = 55296
let surrogateMax = 57343

fun isScalarValue(code : Int) -> Bool =
    code >= 0 && code <= charMax
    && !(code >= surrogateMin && code <= surrogateMax)

fun tArray() -> Type = TCon("Data.Array#Array", KFun(KStar, KStar), noScope)
fun tRawArray() -> Type = TCon("Prim.Array", KFun(KStar, KStar), noScope)

fun arrayOf(element : Type) -> Type = TApp(tArray(), element, KStar)
fun rawArrayOf(element : Type) -> Type = TApp(tRawArray(), element, KStar)

-- -- application and spines ---------------------------------------------------------

-- The kind of a type. Everything but a constructor, a variable, an application
-- and a family classifies values, so it is `*`.
fun kindOf(t : Type) -> Kind = match prune(t) {
    TVar(cell) -> cell.kind
    TCon(_, k, _) -> k
    TApp(_, _, k) -> k
    TFam(_, _, k) -> k
    _ -> KStar
}

-- `head a1 ... an`, checking each application against `head`'s kind.
--
-- The only place an application is built, so also the only place a kind can be
-- got wrong. An over-applied constructor is caught here rather than by a
-- separate arity check: `Array Int Bool` fails because `*` is not an arrow,
-- which is the same rule that rejects `Int Bool`.
fun applyTo(head : Type, args : Array Type, where : Option Span) -> Type {
    var t = head
    for arg in args {
        let k = kprune(kindOf(t))
        let result = kvar()
        if !unifyKinds(k, KFun(kindOf(arg), result)) {
            -- A kind still undecided can only have failed the occurs check,
            -- since a variable unifies with anything else: `f f` would need
            -- `f`'s kind to contain itself.
            match kprune(k) {
                KVar(_) -> oops("'" + show(t) + "' cannot be applied to '"
                                + show(arg) + "': that would need a kind that "
                                + "contains itself", where)
                _ -> oops("'" + show(t) + "' has kind " + showKind(k)
                          + ", so it cannot be applied to '" + show(arg) + "'",
                          where)
            }
        }
        t = TApp(t, arg, result)
    }
    t
}

-- Decompose `f a1 ... an` into its head and its arguments.
--
-- Most of the checker wants to ask "is this an `Array`?", which is a question
-- about the head.
fun spine(t : Type) -> (Type, Array Type) {
    let args = Array.new(2)
    var head = prune(t)
    var going = True
    while going {
        match head {
            TApp(fn, arg, _) -> {
                Array.push(args, arg)
                head = prune(fn)
            }
            _ -> { going = False }
        }
    }
    (head, Array.reverse(args))
}

-- The constructor at the head of `t`, if it is one.
fun headCon(t : Type) -> Option String {
    let (head, _) = spine(t)
    match head {
        TCon(name, _, _) -> Some(name)
        _ -> None
    }
}

-- -- the numeric tower --------------------------------------------------------------
--
-- A numeric literal does not have a type; it has a *set* of types it could
-- have, and `OneOf` is what carries that set until something decides. The set
-- is closed and built in -- there is no way to add a member from source -- so
-- the two tables below are the whole of it.
--
-- Both are tables so that a sized-integer tower drops in by editing them, with
-- no change to the generator or the solver. A width is the number of
-- two's-complement bits a type has; a mantissa is how many bits of significand
-- a float type has, which is what decides whether an integer literal is exactly
-- representable in it.
--
-- `Byte` is deliberately absent. It is an integral type but not a *literal*
-- one: there is no `Byte` numeral, only `Byte.fromInt`, so nothing ever needs
-- to default to it (PRIMITIVES.md 2).
--
-- Order is meaning: defaulting takes the first member of a set in this order,
-- and printing renders a set in it. So `Int` leads the integral types and
-- `Float` the decimal ones -- the "integral defaults to Int, decimal defaults
-- to Float" rule, obtained from one mechanism rather than two.
let integralWidths = [("Int", 64)]
let decimalMantissas = [("Float", 53)]

-- The tower, most-preferred first.
fun numericOrder() -> Array String {
    let out = Array.new(4)
    for (name, _) in integralWidths { Array.push(out, name) }
    for (name, _) in decimalMantissas { Array.push(out, name) }
    out
}

-- The types an integer literal could have: everything that holds `value`.
--
-- **Including the float types.** An integer literal is not an integer-typed
-- expression; it is a written numeral, and `1` denotes a perfectly good
-- `Float`. Only the reverse is unsafe, which is why `floatLiteralSet` is the
-- float types alone. This is the `Num`/`Fractional` split, and it is what lets
-- `1 + 2.0` mean what it reads as.
--
-- One rule decides membership for the whole tower: can the type hold this value
-- exactly? For an integral type that is its width; for a float type it is its
-- mantissa, so a literal past 2^53 is an `Int` and not a `Float` rather than
-- silently rounding.
--
-- The width test the Python performs is absent here and cannot fail: a literal
-- outside two's-complement 64-bit range is a *lex* error in this implementation
-- (PRIMITIVES.md 1.3), so by the time a value reaches inference it is an `Int`
-- already. Python's lexer builds a bignum and leaves the range to this
-- function; the two agree on every program either will accept.
fun intLiteralSet(value : Int) -> Array String {
    let out = Array.new(2)
    for (name, _) in integralWidths { Array.push(out, name) }
    let magnitude = Int.abs(value)
    for (name, mantissa) in decimalMantissas {
        if magnitude < Int.shl(1, mantissa) { Array.push(out, name) }
    }
    out
}

-- The types a decimal literal could have: the float types, and only those.
--
-- Not narrowed by the value the way `intLiteralSet` is. `0.1` is inexact in
-- every binary float, so representability would reject every member and say
-- nothing useful; what a decimal literal picks out is the *kind* of type, and
-- precision is the programmer's business from there.
fun floatLiteralSet() -> Array String {
    let out = Array.new(2)
    for (name, _) in decimalMantissas { Array.push(out, name) }
    out
}

-- Every tower member is a nullary constructor, so its type is a `TCon`.
fun numericType(name : String) -> Type = TCon(name, KStar, noScope)

fun sortNumeric(names : Array String) -> Array String {
    let order = numericOrder()
    Array.sortBy(names, fun(a, b) = {
        let ra = rankOf(order, a)
        let rb = rankOf(order, b)
        if ra != rb { Int.compare(ra, rb) } else { Array.threeWay(a, b) }
    })
}

fun rankOf(order : Array String, name : String) -> Int = match Array.indexOf(order, name) {
    Some(i) -> i
    None -> len(order)
}

-- -- predicates and schemes ---------------------------------------------------------

-- An atomic predicate: `HasField l r a`, `OneOf t {...}`, `Eq a`.
--
-- Predicates live here rather than beside the solver because a scheme can carry
-- them -- they are part of the type language, not of the machinery that
-- discharges them.
type Pred = Pred {
    name : String,
    args : Array Type,
}

fun pred(name : String, args : Array Type) -> Pred =
    Pred { name = name, args = args }

-- An identity, for dropping a context's duplicate predicates. `r.n = r.n + 1`
-- demands the same field twice, and there is no reason for the scheme to say so
-- twice.
fun predKey(p : Pred) -> String {
    let b = String.builder()
    String.push(b, p.name)
    for a in p.args {
        String.push(b, " ")
        String.push(b, typeKey(a))
    }
    String.build(b)
}

-- The binder depth a predicate is tied to.
--
-- A predicate is no more general than its most-constrained variable, so its
-- level is the minimum over the variables it mentions, and generalization
-- compares against it exactly as it does for a type. Keeping the two tests
-- identical is what stops a predicate from outliving, or being stranded by, the
-- type it constrains.
fun predLevel(p : Pred) -> Int {
    var out = ground
    for v in varsOf(p.args) {
        if v.level < out { out = v.level }
    }
    out
}

-- `forall q1 ... qn . preds => body`. An empty `quantified` means monomorphic;
-- an empty `preds` means unqualified.
type Scheme = Scheme {
    quantified : Array VarCell,
    body       : Type,
    preds      : Array Pred,
}

fun mono(t : Type) -> Scheme =
    Scheme { quantified = Array.new(0), body = t, preds = Array.new(0) }

fun scheme(quantified : Array VarCell, body : Type, preds : Array Pred) -> Scheme =
    Scheme { quantified = quantified, body = body, preds = preds }

-- -- operations ---------------------------------------------------------------------

-- Follow bound variables to the type they stand for, path-compressing.
fun prune(t : Type) -> Type = match t {
    TVar(cell) -> match cell.ref {
        None -> t
        Some(inner) -> {
            let settled = prune(inner)
            cell.ref = Some(settled)
            settled
        }
    }
    _ -> t
}

-- What unification needs from the solver once families exist.
--
-- Two operations, and deliberately no more: reduce a family application if the
-- instance table decides it, and take an equation that cannot yet be decided.
-- A record of two closures rather than an interface, which is also how this
-- compiler's module DAG is kept acyclic -- the solver knows about types, and
-- types know nothing about the solver.
type Families = Families {
    -- The family application's definition, or `None` if no instance decides it.
    reduce : fun(Type) -> Option Type,
    -- Put `a ~ b` back on the queue: neither solvable nor false, yet.
    defer  : fun(Type, Type, Option Span, String) -> Unit,
}

-- Prune, then reduce family applications at the head until one sticks.
--
-- `None` means "no reducer available" -- the post-solving passes, where every
-- family that was going to reduce already has. Only the head is reduced: a
-- family buried inside an argument is reduced when something compares it, which
-- is the only moment its value can matter.
fun normalize(t : Type, fams : Option Families) -> Type {
    var out = prune(t)
    match fams {
        None -> return out
        Some(f) -> {
            var going = True
            while going {
                match out {
                    TFam(_, _, _) -> match f.reduce(out) {
                        None -> { going = False }
                        Some(reduced) -> { out = prune(reduced) }
                    }
                    _ -> { going = False }
                }
            }
            out
        }
    }
}

-- Occurs check, lowering levels along the way.
--
-- A variable reachable from `var`'s new value must not outlive `var`, so its
-- level is capped at `var`'s. Doing both walks at once keeps them in step.
fun occursAndAdjust(slot : VarCell, t : Type) -> Bool = match prune(t) {
    TVar(cell) -> {
        if cell.id == slot.id { return True }
        if slot.level < cell.level { cell.level = slot.level }
        False
    }
    TApp(fn, arg, _) -> occursAndAdjust(slot, fn) || occursAndAdjust(slot, arg)
    -- Over-strict, strictly speaking: a family is not injective, so `a ~ Elem a`
    -- is not obviously a cycle. Rejecting it is the standard choice and the safe
    -- one -- admitting it would need the family's definition to prove the
    -- recursion bottoms out.
    TFam(_, arg, _) -> occursAndAdjust(slot, arg)
    TFun(params, ret) -> {
        for p in params {
            if occursAndAdjust(slot, p) { return True }
        }
        occursAndAdjust(slot, ret)
    }
    TTuple(elems) -> {
        for e in elems {
            if occursAndAdjust(slot, e) { return True }
        }
        False
    }
    _ -> False
}

-- The first skolem in `t` that outlives `var`, if there is one.
--
-- A signature's variable becomes a rigid constructor for the length of the body,
-- standing for a type the *caller* picks. So it means something only inside the
-- binder that made it. Letting it bind a variable born outside would state that
-- some type out there is equal to a type the callee never gets to see -- the
-- escape that makes a checked signature unsound rather than merely unchecked.
--
-- Ranks already say this exactly, which is why the test is one comparison: the
-- skolem carries the rank of its binder, `var` carries its own, and a skolem
-- younger than the variable it would bind is out of scope. Run before
-- `occursAndAdjust`, which lowers levels and would erase the evidence.
fun escaping(slot : VarCell, t : Type) -> Option String = match prune(t) {
    TCon(name, _, level) -> if level > slot.level { Some(name) } else { None }
    TApp(fn, arg, _) -> firstOf(escaping(slot, fn), escaping(slot, arg))
    TFam(_, arg, _) -> escaping(slot, arg)
    TFun(params, ret) -> {
        for p in params {
            match escaping(slot, p) {
                Some(found) -> return Some(found)
                None -> {}
            }
        }
        escaping(slot, ret)
    }
    TTuple(elems) -> {
        for e in elems {
            match escaping(slot, e) {
                Some(found) -> return Some(found)
                None -> {}
            }
        }
        None
    }
    _ -> None
}

fun firstOf(a : Option v, b : Option v) -> Option v = match a {
    Some(_) -> a
    None -> b
}

-- Equate two types, or report that it cannot be decided yet.
--
-- A family application whose argument is still open is neither solvable nor an
-- error: `Elem a ~ Int` becomes true or false depending on what `a` turns out to
-- be. That is the third outcome, and `defer` is where it goes -- back on the
-- solver's queue, to be retried when something has been learnt. Without a
-- reducer there is no queue, so it is a mismatch.
fun unify(a : Type, b : Type, where : Option Span, context : String,
          fams : Option Families) -> Unit {
    let x = normalize(a, fams)
    let y = normalize(b, fams)

    -- Python compares by object identity here; structural equality is the same
    -- answer for a unifier that has nothing to do when the two already agree,
    -- and is what there is without identities.
    if typeKey(x) == typeKey(y) { return {} }

    -- Section 4.3: bottom is absorbed by anything it meets. A no-op, not a
    -- binding -- `join` is what recovers the surviving type.
    match x {
        TBottom -> return {}
        _ -> {}
    }
    match y {
        TBottom -> return {}
        _ -> {}
    }

    match x {
        TVar(cell) -> {
            match escaping(cell, y) {
                Some(out) -> oops("the type '" + out + "' cannot escape the "
                    + "signature that declares it: it stands for whatever a "
                    + "caller of that function chooses, so nothing outside can "
                    + "be equal to it", where)
                None -> {}
            }
            if occursAndAdjust(cell, y) {
                oops("cannot construct the infinite type " + show(x) + " = "
                     + show(y), where)
            }
            if !unifyKinds(cell.kind, kindOf(y)) { kindMismatch(x, y, where) }
            cell.ref = Some(y)
            return {}
        }
        _ -> {}
    }
    match y {
        TVar(_) -> return unify(y, x, where, context, fams)
        _ -> {}
    }

    match (x, y) {
        -- Levels distinguish two skolems that happen to share a name: a nested
        -- signature may write `a` too, and those two are not the same type.
        (TCon(n1, _, l1), TCon(n2, _, l2)) ->
            if n1 != n2 || l1 != l2 { mismatch(x, y, where, context) }
        -- Decomposing an application is sound only because there are no
        -- type-level lambdas: every head is rigid, or a variable that will be
        -- bound to a rigid head, so `f a ~ g b` cannot be satisfied any way but
        -- pointwise. Aliases are expanded before they reach here.
        (TApp(f1, a1, _), TApp(f2, a2, _)) -> {
            unify(f1, f2, where, context, fams)
            unify(a1, a2, where, context, fams)
        }
        (TFun(p1, r1), TFun(p2, r2)) -> {
            if len(p1) != len(p2) { arityMismatch(len(p1), len(p2), where) }
            for var i = 0; i < len(p1); i = i + 1 {
                unify(p1[i], p2[i], where, context, fams)
            }
            unify(r1, r2, where, context, fams)
        }
        (TLabel(n1), TLabel(n2)) -> if n1 != n2 { mismatch(x, y, where, context) }
        (TIndex(v1), TIndex(v2)) -> if v1 != v2 { mismatch(x, y, where, context) }
        (TTuple(e1), TTuple(e2)) -> {
            if len(e1) != len(e2) { mismatch(x, y, where, context) }
            for var i = 0; i < len(e1); i = i + 1 {
                unify(e1[i], e2[i], where, context, fams)
            }
        }
        _ -> unifyRest(x, y, where, context, fams)
    }
}

-- A family that did not reduce, or a plain mismatch.
--
-- Two families are equal when they are the *same* application -- `Elem i ~
-- Elem i` is reflexivity, and is as far as it goes, because a family is not
-- injective and `Elem i ~ Elem j` must not conclude `i ~ j`. Anything else
-- waits for the argument to be decided.
fun unifyRest(x : Type, y : Type, where : Option Span, context : String,
              fams : Option Families) -> Unit {
    if !isFam(x) && !isFam(y) { mismatch(x, y, where, context) }
    match (x, y) {
        (TFam(n1, a1, _), TFam(n2, a2, _)) ->
            if n1 == n2 && typeKey(a1) == typeKey(a2) { return {} }
        _ -> {}
    }
    match fams {
        None -> mismatch(x, y, where, context)
        Some(f) -> f.defer(x, y, where, context)
    }
}

fun isFam(t : Type) -> Bool = match t {
    TFam(_, _, _) -> True
    _ -> False
}

fun kindMismatch(a : Type, b : Type, where : Option Span) -> a =
    oops("'" + show(b) + "' has kind " + showKind(kindOf(b))
         + ", but a type of kind " + showKind(kindOf(a))
         + " was expected here", where)

fun mismatch(a : Type, b : Type, where : Option Span, context : String) -> a {
    let suffix = if context == "" { "" } else { " in " + context }
    oops("expected " + show(a) + ", found " + show(b) + suffix, where)
}

fun arityMismatch(want : Int, got : Int, where : Option Span) -> a =
    oops("this function takes " + Int.toString(want) + " argument"
         + (if want == 1 { "" } else { "s" }) + " but " + Int.toString(got)
         + " " + (if got == 1 { "was" } else { "were" }) + " supplied", where)

-- Unify two types and answer the one that survives.
--
-- Plain `unify` cannot answer this. `if c { return 1 } else { 2 }` has arms of
-- type bottom and Int; unifying them is a no-op and the caller still has to know
-- the result is Int. Every place where two branches must agree -- `if` arms,
-- `match` arms, a loop's `break` values -- uses this instead.
fun join(a : Type, b : Type, where : Option Span, context : String,
         fams : Option Families) -> Type {
    let x = prune(a)
    let y = prune(b)
    match x {
        TBottom -> return y
        _ -> {}
    }
    match y {
        TBottom -> return x
        _ -> {}
    }
    unify(x, y, where, context, fams)
    x
}

-- Quantify every variable created deeper than `level` (design.md 4.4).
--
-- `preds` are the predicates the solver decided this binding retains. They are
-- walked too, so a variable a predicate constrains is quantified along with the
-- type's own -- a retained predicate must not mention a variable the scheme left
-- free.
fun generalize(t : Type, level : Int, preds : Array Pred) -> Scheme {
    let quantified = Array.new(4)
    let seen = Set.new()
    generalizeWalk(t, level, quantified, seen)
    for p in preds {
        for arg in p.args { generalizeWalk(arg, level, quantified, seen) }
    }
    Scheme { quantified = quantified, body = t, preds = preds }
}

fun generalizeWalk(t : Type, level : Int, out : Array VarCell,
                   seen : Set.Set Int) -> Unit = match prune(t) {
    TVar(cell) -> if cell.level > level && !Set.contains(seen, cell.id) {
        Set.add(seen, cell.id)
        Array.push(out, cell)
    }
    TApp(fn, arg, _) -> {
        generalizeWalk(fn, level, out, seen)
        generalizeWalk(arg, level, out, seen)
    }
    TFam(_, arg, _) -> generalizeWalk(arg, level, out, seen)
    TFun(params, ret) -> {
        for p in params { generalizeWalk(p, level, out, seen) }
        generalizeWalk(ret, level, out, seen)
    }
    TTuple(elems) -> for e in elems { generalizeWalk(e, level, out, seen) }
    _ -> {}
}

-- Replace the scheme's quantified variables with fresh ones.
fun instantiate(s : Scheme, fresh : Fresh) -> Type {
    let (_, body, _) = instantiateQual(s, fresh)
    body
}

-- Instantiate a scheme, answering its context, its body and its type arguments.
--
-- `fresh` is supplied by the caller rather than a rank being passed in, because
-- a new variable has to be registered wherever its owner tracks them -- the
-- solver's pool for the current rank, or the enclosing existential while a
-- constraint is still being generated. Making a variable here would create one
-- nothing is keeping track of.
--
-- The context has to be renamed by the same substitution as the body, or its
-- predicates would constrain the scheme's original variables rather than this
-- use site's fresh ones.
--
-- The third result is the substitution itself, in `quantified`'s order: what
-- this occurrence instantiated the scheme *at*. A typed Core asks for it -- it
-- is the argument list of the type application that a use of a polymorphic name
-- becomes -- and the variables are still bare here, which is right: the solver
-- decides what they are.
fun instantiateQual(s : Scheme, fresh : Fresh) -> (Array Pred, Type, Array Type) {
    if len(s.quantified) == 0 { return (s.preds, s.body, Array.new(0)) }
    -- A fresh variable stands for a quantified one, so it inherits its kind:
    -- instantiating `Wrap f a` must not turn the `f` into something of kind `*`.
    let mapping = Map.new()
    let args = Array.new(len(s.quantified))
    for v in s.quantified {
        let replacement = fresh()
        discard(unifyKinds(replacement.kind, v.kind))
        Map.put(mapping, v.id, TVar(replacement))
        Array.push(args, TVar(replacement))
    }
    let preds = Array.map(s.preds, fun(p) =
        Pred { name = p.name, args = Array.map(p.args, fun(a) = subst(a, mapping)) })
    (preds, subst(s.body, mapping), args)
}

fun subst(t : Type, mapping : Map Int Type) -> Type = match prune(t) {
    TVar(cell) -> Map.getOr(mapping, cell.id, TVar(cell))
    TApp(fn, arg, k) -> TApp(subst(fn, mapping), subst(arg, mapping), k)
    TFam(name, arg, k) -> TFam(name, subst(arg, mapping), k)
    TFun(params, ret) -> TFun(Array.map(params, fun(p) = subst(p, mapping)),
                              subst(ret, mapping))
    TTuple(elems) -> TTuple(Array.map(elems, fun(e) = subst(e, mapping)))
    _ -> prune(t)
}

-- A structural identity for a type, up to the current substitution.
--
-- Python's is a tuple; a string is what there is here, and it is used the same
-- way: as a map key and as the equality two family applications are compared by.
fun typeKey(t : Type) -> String {
    let b = String.builder()
    typeKeyInto(b, t)
    String.build(b)
}

fun typeKeyInto(b : String.Builder, t : Type) -> Unit = match prune(t) {
    TVar(cell) -> {
        String.push(b, "v")
        String.push(b, Int.toString(cell.id))
    }
    TCon(name, _, level) -> {
        String.push(b, "c")
        String.push(b, name)
        String.push(b, "@")
        String.push(b, Int.toString(level))
    }
    TApp(fn, arg, _) -> {
        String.push(b, "(a ")
        typeKeyInto(b, fn)
        String.push(b, " ")
        typeKeyInto(b, arg)
        String.push(b, ")")
    }
    TFam(name, arg, _) -> {
        String.push(b, "(f ")
        String.push(b, name)
        String.push(b, " ")
        typeKeyInto(b, arg)
        String.push(b, ")")
    }
    TFun(params, ret) -> {
        String.push(b, "(n")
        for p in params {
            String.push(b, " ")
            typeKeyInto(b, p)
        }
        String.push(b, " -> ")
        typeKeyInto(b, ret)
        String.push(b, ")")
    }
    TTuple(elems) -> {
        String.push(b, "(t")
        for e in elems {
            String.push(b, " ")
            typeKeyInto(b, e)
        }
        String.push(b, ")")
    }
    TLabel(name) -> {
        String.push(b, "l")
        String.push(b, name)
    }
    TIndex(value) -> {
        String.push(b, "i")
        String.push(b, Int.toString(value))
    }
    TSet(names) -> {
        String.push(b, "s{")
        String.push(b, String.join(names, ","))
        String.push(b, "}")
    }
    TBottom -> String.push(b, "!")
}

-- Every sub-term of `t`, itself included, pruned as it goes.
--
-- An occurs check over whole types rather than over variables: SPEC-DELTAS.md 39
-- needs to know whether one *family application* appears inside another type,
-- which `varsOf` cannot answer.
fun subterms(t : Type) -> Array Type {
    let out = Array.new(4)
    subtermsInto(t, out)
    out
}

fun subtermsInto(t : Type, out : Array Type) -> Unit {
    let here = prune(t)
    Array.push(out, here)
    match here {
        TApp(fn, arg, _) -> {
            subtermsInto(fn, out)
            subtermsInto(arg, out)
        }
        TFam(_, arg, _) -> subtermsInto(arg, out)
        TFun(params, ret) -> {
            for p in params { subtermsInto(p, out) }
            subtermsInto(ret, out)
        }
        TTuple(elems) -> for e in elems { subtermsInto(e, out) }
        _ -> {}
    }
}

-- The unbound variables reachable from `types`, first occurrence first.
fun varsOf(types : Array Type) -> Array VarCell {
    let out = Array.new(4)
    let seen = Set.new()
    for t in types { varsWalk(t, out, seen) }
    out
}

fun varsWalk(t : Type, out : Array VarCell, seen : Set.Set Int) -> Unit =
    match prune(t) {
        TVar(cell) -> if !Set.contains(seen, cell.id) {
            Set.add(seen, cell.id)
            Array.push(out, cell)
        }
        TApp(fn, arg, _) -> {
            varsWalk(fn, out, seen)
            varsWalk(arg, out, seen)
        }
        TFam(_, arg, _) -> varsWalk(arg, out, seen)
        TFun(params, ret) -> {
            for p in params { varsWalk(p, out, seen) }
            varsWalk(ret, out, seen)
        }
        TTuple(elems) -> for e in elems { varsWalk(e, out, seen) }
        _ -> {}
    }

fun freeVars(t : Type) -> Set.Set Int {
    let out = Set.new()
    for v in varsOf([t]) { Set.add(out, v.id) }
    out
}

-- -- printing -----------------------------------------------------------------------

-- Variable names, assigned in order of first appearance.
--
-- Shared across calls on purpose: several types are rendered as one story -- a
-- scheme's `forall`, its context and its body all name a variable alike -- and
-- restarting the counter would call the second call's first new variable `a` as
-- well. `next` therefore starts at however many names the caller already had.
type Namer = Namer {
    names  : Map Int String,
    next   : Int,
    prefix : String,
}

fun namer(prefix : String) -> Namer =
    Namer { names = Map.new(), next = 0, prefix = prefix }

-- A namer that has already named a scheme's quantified variables, positionally.
fun namerFrom(quantified : Array VarCell, prefix : String) -> Namer {
    let names = Map.new()
    for var i = 0; i < len(quantified); i = i + 1 {
        Map.put(names, quantified[i].id, varName(i))
    }
    Namer { names = names, next = len(quantified), prefix = prefix }
}

-- `a`..`z`, then `a1`, `b1`, ... Built from the code point rather than from a
-- table, since the twenty-six are contiguous ASCII and a table would be a
-- second place to be wrong about that.
fun varName(index : Int) -> String {
    let letter = match Char.fromInt(97 + Int.mod(index, 26)) {
        Some(c) -> Char.toString(c)
        None -> "?"
    }
    if index < 26 { letter } else { letter + Int.toString(index / 26) }
}

-- Render a type in surface syntax, naming variables a, b, c, ... in order.
fun show(t : Type) -> String = showAt(t, namer(""), 0)

fun showAt(t : Type, n : Namer, prec : Int) -> String = match prune(t) {
    TVar(cell) -> match Map.get(n.names, cell.id) {
        Some(name) -> name
        None -> {
            let name = n.prefix + varName(n.next)
            Map.put(n.names, cell.id, name)
            n.next = n.next + 1
            name
        }
    }
    TBottom -> "!"
    TLabel(name) -> "\"" + name + "\""
    TIndex(value) -> Int.toString(value)
    TSet(names) -> "{" + String.join(sortNumeric(names), ", ") + "}"
    TTuple(elems) -> "(" + String.join(showEach(elems, n, 0), ", ") + ")"
    TFun(params, ret) -> {
        let out = "fun(" + String.join(showEach(params, n, 0), ", ") + ") -> "
                  + showAt(ret, n, 0)
        if prec > 0 { "(" + out + ")" } else { out }
    }
    TCon(name, _, _) -> display(name)
    TFam(name, arg, _) -> {
        let out = shortName(name) + " " + showAt(arg, n, 2)
        if prec > 1 { "(" + out + ")" } else { out }
    }
    TApp(_, _, _) -> {
        let (head, args) = spine(prune(t))
        let out = showAt(head, n, 2) + " "
                  + String.join(showEach(args, n, 2), " ")
        if prec > 1 { "(" + out + ")" } else { out }
    }
}

fun showEach(ts : Array Type, n : Namer, prec : Int) -> Array String {
    let out = Array.new(len(ts))
    for t in ts { Array.push(out, showAt(t, n, prec)) }
    out
}

-- Render a scheme, marking free (monomorphic) variables with a leading `_`.
--
-- A `let` bound to an expansive expression is not generalized (design.md 4.4),
-- so its variables stay free -- `Array _a` rather than `Array a`.
--
-- A qualified scheme prints its context first, in the bracket syntax a `fun`
-- declaration writes: `[Ord a] fun(Array a) -> a`. The names are assigned before
-- the context is rendered so that the `a` in the context and the `a` in the body
-- come out as the same letter.
fun showScheme(s : Scheme) -> String {
    let n = namerFrom(s.quantified, "_")
    let body = showAt(s.body, n, 0)
    if len(s.preds) == 0 { return body }
    let parts = Array.new(len(s.preds))
    for p in s.preds { Array.push(parts, showPredWith(p, n)) }
    "[" + String.join(parts, ", ") + "] " + body
}

fun showPred(p : Pred) -> String = showPredWith(p, namer("_"))

fun showPredWith(p : Pred, n : Namer) -> String {
    -- An equality is written infix, as it is in a context: `Item c ~ Op`. Its
    -- sides are rendered at application precedence rather than argument
    -- precedence, since `~` binds looser than application and a family need not
    -- be parenthesized on either side of it.
    if p.name == equals && len(p.args) == 2 {
        return showAt(p.args[0], n, 1) + " ~ " + showAt(p.args[1], n, 1)
    }
    -- A predicate is itself an application, so its arguments are rendered at
    -- argument precedence: `HasField "d" a (Array Int)`.
    let name = shortName(p.name)
    if len(p.args) == 0 { return name }
    name + " " + String.join(showEach(p.args, n, 2), " ")
}

-- -- diagnostics --------------------------------------------------------------------

fun oops(message : String, where : Option Span) -> a = match where {
    None -> Diag.fail(Diag.whole("type error", message), "")
    Some(s) -> Diag.fail(Diag.at("type error", message, s), s.file)
}

fun discard(x : a) -> Unit = {}
