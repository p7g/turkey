"""The prelude: the classes every operator means, written in turkey itself.

Until M8 an operator was a table entry. `+` was `fun(Int, Int) -> Int`, and
`+.` was the same thing again for floats; `==` was Int-only, with `String.eq`,
`Char.eq` and the rest shipped as named builtins because there was no way to
say "equality, at whatever type this is". design.md section 8.2 recorded that
as a debt and named its repayment: classes.

So every operator is a class method, and the classes are ordinary source. The
only thing the checker knows about `+` is which name it desugars to; `Add`
itself has no special status, and a program may write `instance Add` for its
own type and use `+` on it.

**Per-operator, not one omnibus `Num`.** `Add`, `Sub`, `Mul`, `Div`, `Rem`,
`Neg` are separate classes, as in Rust's `std::ops`, so a type that adds is not
thereby required to divide. That is the choice Haskell's `Num` does not offer.

**Operators are homogeneous, and that is the price of dropping MPTCs.** With
one class parameter there is no `Add a b`, so both operands and the result have
the same type; `Vec * Scalar` is not expressible. This is the one place where
the no-MPTC decision is visible in the surface language, and it is stated here
rather than discovered.

**`Show` is a class, and `print` is an ordinary function over it.** `print(x)`
is `Prim.print(show(x))` -- written here, in turkey, with an inferred context of
`[Show a]`. The two machine writes keep the `Prim.` prefix and nothing else can
name them, so the only way to put anything on stdout is to say what it looks
like as a `String`.

**`Iterator` is a cursor, and it has two families.** `Item c` is the element
type and `Cursor c` is the mutable state that walks the container; `iter`
produces one and `next` advances it, returning `Option (Item c)`. Indexing
would have been simpler, but it only describes containers that can produce
their *k*th element in the first place -- a linked list, a stream, a file's
lines cannot, and those are the cases iteration exists for. The cost of the
cursor is `Option`, which is therefore declared here too. `Array` is the first
instance; it is no longer the only one possible.

Both families are indexed by the container rather than split across an
`Iterable`/`Iterator` pair as Rust does, because the second class would need
`Iterator (Iter c)` as a superclass over its own family application. One class
with two families says the same thing and asks nothing new of M5's machinery.

The instance bodies are written against `Prim.*` (see `turkey/builtins.py`),
which is in scope here and nowhere else.
"""

from __future__ import annotations

SOURCE = """
type Bool = False | True

type Option a = None | Some(a)

class Eq a {
    fun eq(a, a) -> Bool
    fun ne(x : a, y : a) -> Bool = !eq(x, y)
}

class Ord a : Eq a {
    fun lt(a, a) -> Bool
    fun lte(x : a, y : a) -> Bool = lt(x, y) || eq(x, y)
    fun gt(x : a, y : a) -> Bool = lt(y, x)
    fun gte(x : a, y : a) -> Bool = !lt(x, y)
}

class Add a { fun add(a, a) -> a }
class Sub a { fun sub(a, a) -> a }
class Mul a { fun mul(a, a) -> a }
class Div a { fun div(a, a) -> a }
class Rem a { fun rem(a, a) -> a }
class Neg a { fun neg(a) -> a }

class Show a { fun show(a) -> String }

class Iterator c {
    type Item c
    type Cursor c

    fun iter(c) -> Cursor c
    fun next(c, Cursor c) -> Option (Item c)
}

instance Eq Int { fun eq(x, y) = Prim.intEq(x, y) }
instance Eq Float { fun eq(x, y) = Prim.floatEq(x, y) }
instance Eq String { fun eq(x, y) = Prim.stringEq(x, y) }
instance Eq Char { fun eq(x, y) = Prim.charEq(x, y) }
instance Eq Bool { fun eq(x, y) = Prim.boolEq(x, y) }

instance Ord Int { fun lt(x, y) = Prim.intLt(x, y) }
instance Ord Float { fun lt(x, y) = Prim.floatLt(x, y) }
instance Ord String { fun lt(x, y) = Prim.stringLt(x, y) }
instance Ord Char { fun lt(x, y) = Prim.charLt(x, y) }
instance Ord Bool { fun lt(x, y) = Prim.boolLt(x, y) }

instance Add Int { fun add(x, y) = Prim.intAdd(x, y) }
instance Sub Int { fun sub(x, y) = Prim.intSub(x, y) }
instance Mul Int { fun mul(x, y) = Prim.intMul(x, y) }
instance Div Int { fun div(x, y) = Prim.intDiv(x, y) }
instance Rem Int { fun rem(x, y) = Prim.intRem(x, y) }
instance Neg Int { fun neg(x) = Prim.intNeg(x) }

instance Add Float { fun add(x, y) = Prim.floatAdd(x, y) }
instance Sub Float { fun sub(x, y) = Prim.floatSub(x, y) }
instance Mul Float { fun mul(x, y) = Prim.floatMul(x, y) }
instance Div Float { fun div(x, y) = Prim.floatDiv(x, y) }
instance Neg Float { fun neg(x) = Prim.floatNeg(x) }

-- A cursor over an array is a mutable index. Nothing else in the language may
-- name it; it exists so that `Cursor (Array a)` has something to be.
type ArrayCursor = ArrayCursor { at : Int }

instance Iterator (Array a) {
    type Item = a
    type Cursor = ArrayCursor

    fun iter(xs) = ArrayCursor { at = 0 }

    fun next(xs, cur) {
        if cur.at >= xs.length { return None }
        let x = xs[cur.at]
        cur.at = cur.at + 1
        Some(x)
    }
}

instance Show Int { fun show(x) = Int.toString(x) }
instance Show Float { fun show(x) = Float.toString(x) }
instance Show Bool {
    fun show(x) = match x {
        True -> "True"
        False -> "False"
    }
}
instance Show Char { fun show(x) = Char.toString(x) }
instance Show String { fun show(x) = x }

instance [Show a] Show (Option a) {
    fun show(o) = match o {
        None -> "None"
        Some(x) -> "Some(" ++ show(x) ++ ")"
    }
}

instance [Show a] Show (Array a) {
    fun show(xs) {
        var out = "["
        var rest = False
        for x in xs {
            if rest { out = out ++ ", " }
            out = out ++ show(x)
            rest = True
        }
        out ++ "]"
    }
}

-- The prelude's only top-level bindings, and the only place `Prim.print` and
-- `Prim.write` are reachable from. Both infer `[Show a] fun(a) -> Unit`.
fun print(x) = Prim.print(show(x))
fun write(x) = Prim.write(show(x))
"""

# Operator -> the method it desugars to. `&&`, `||`, `!` and `++` are absent
# deliberately: the first two short-circuit, which no function call does, and
# `++` is concatenation on `String` and has no class to belong to yet.
BINARY_METHOD: dict[str, str] = {
    "+": "add", "-": "sub", "*": "mul", "/": "div", "%": "rem",
    "==": "eq", "!=": "ne",
    "<": "lt", "<=": "lte", ">": "gt", ">=": "gte",
}

UNARY_METHOD: dict[str, str] = {"-": "neg"}

# What `for x in xs` is written in terms of: a cursor from `iter`, advanced by
# `next` until it answers `None`. The two constructor names are here because
# the evaluator has to recognize the answer, and one place should say so.
ITER_CLASS = "Iterator"
ITER_ITEM = "Item"
ITER_CURSOR = "Cursor"
ITER_ITER = "iter"
ITER_NEXT = "next"

OPTION = "Option"
OPTION_NONE = "None"
OPTION_SOME = "Some"
