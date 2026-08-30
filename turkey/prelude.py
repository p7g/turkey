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

**`Iterator` is the `for` loop's protocol,** and it is where M7's families
earn their place: `Item i` is the element type, determined by the container and
named by nobody. Iteration is indexed rather than cursor-based because a
cursor's `next` wants an `Option`, and v0 has no such type -- `count`/`nth` is
what the language can actually say today, and `for x in xs` desugars through
it. `Array` is the first instance; it is no longer the only one possible.

The instance bodies are written against `Prim.*` (see `turkey/builtins.py`),
which is in scope here and nowhere else.
"""

from __future__ import annotations

SOURCE = """
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

class Iterator i {
    type Item i

    fun count(i) -> Int
    fun nth(i, Int) -> Item i
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

instance Iterator (Array a) {
    type Item = a

    fun count(xs) = xs.length
    fun nth(xs, k) = xs[k]
}
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

# What `for x in xs` is written in terms of.
ITER_CLASS = "Iterator"
ITER_ITEM = "Item"
ITER_COUNT = "count"
ITER_NTH = "nth"
