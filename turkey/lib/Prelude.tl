-- The prelude: the classes every operator means, written in turkey itself.
--
-- Until M8 an operator was a table entry. `+` was `fun(Int, Int) -> Int`, and
-- `+.` was the same thing again for floats; `==` was Int-only, with `String.eq`,
-- `Char.eq` and the rest shipped as named builtins because there was no way to
-- say "equality, at whatever type this is". design.md section 8.2 recorded that
-- as a debt and named its repayment: classes.
--
-- So every operator is a class method, and the classes are ordinary source. The
-- only thing the checker knows about `+` is which name it desugars to; `Add`
-- itself has no special status, and a program may write `instance Add` for its
-- own type and use `+` on it.
--
-- Per-operator, not one omnibus `Num`. `Add`, `Sub`, `Mul`, `Div`, `Rem`, `Neg`
-- are separate classes, as in Rust's `std::ops`, so a type that adds is not
-- thereby required to divide. That is the choice Haskell's `Num` does not offer.
--
-- Operators are homogeneous, and that is the price of dropping MPTCs. With one
-- class parameter there is no `Add a b`, so both operands and the result have
-- the same type; `Vec * Scalar` is not expressible.
--
-- `Show` is a class, and `print` is an ordinary function over it. `print(x)` is
-- `Prim.print(show(x))` -- written here, with an inferred context of `[Show a]`.
-- The two machine writes keep the `Prim.` prefix, and `Prim.*` is spellable only
-- from a module under `turkey/lib` (see `turkey/modules.py`), so the only way to
-- put anything on stdout is to say what it looks like as a `String`.
--
-- `Iterator` is a cursor, and it has two families. `Item c` is the element type
-- and `Cursor c` is the mutable state that walks the container; `iter` produces
-- one and `next` advances it, returning `Option (Item c)`. Indexing would have
-- been simpler, but it only describes containers that can produce their kth
-- element in the first place -- a linked list, a stream, a file's lines cannot,
-- and those are the cases iteration exists for. The cost of the cursor is
-- `Option`, which `Data.Option` declares.
--
-- Both families are indexed by the container rather than split across an
-- `Iterable`/`Iterator` pair as Rust does, because the second class would need
-- `Iterator (Iter c)` as a superclass over its own family application. One class
-- with two families says the same thing and asks nothing new of M5's machinery.

-- Every program is checked as if this module were imported, and nothing else
-- is (delta 41). What that gets a program is three things: the classes the
-- operators mean, `print`, and the library re-exported *qualified* -- so
-- `Array.push` and `Int.toString` resolve with no import while the bare names
-- `push`, `new`, `pop` and `toString` stay free for a program to define. A
-- name re-export would have claimed them; a module re-export does not. That is
-- the papercut `plan.txt` item 3 opens with, and it is what `module Array`
-- below is for.
module Prelude (
    print, write, error,

    -- The two types the language itself names: `if` demands `Bool` and a
    -- cursor's `next` answers `Option`. Their modules are re-exported below
    -- for the functions; these two lines are what puts the types and their
    -- constructors in scope unqualified.
    Bool(..),
    Option(..),

    -- `Either` is named by no rule of the language, unlike the two above. It
    -- is here because `?` is only worth having over more than one monad, and
    -- an error that says why is the second one (delta 45).
    Either(..),

    module Array,
    module Bool,
    module Char,
    module Either,
    module Float,
    module Int,
    module Option,
    module String,
) where

import Data.Bool   (Bool(..))
import Data.Either (Either(..))
import Data.Option (Option(..))

import qualified Data.Array  as Array
import qualified Data.Bool   as Bool
import qualified Data.Char   as Char
import qualified Data.Either as Either
import qualified Data.Float  as Float
import qualified Data.Int    as Int
import qualified Data.Option as Option
import qualified Data.String as String

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

-- The three classes `?` is written against (delta 45). `Monad` is the one it
-- needs; the other two are here because a `Monad` that cannot lift a plain
-- value is not one anybody can write code against, and because the chain is
-- the one every reader already knows.
--
-- The kinds are discovered, not written: `f a` in `map`'s signature is the only
-- evidence that `f :: * -> *`, exactly as `type Wrap f a = Wrap(f a)` discovers
-- it at the type level (M4). Nothing here asks anything new of M5 or M6.
--
-- Unlike `Add` and `Ord`, these three claim ordinary names: `map`, `pure` and
-- `bind` are the prelude's now, and a program cannot define its own. `?` did
-- not need that -- it reaches `bind` the way `+` reaches `add`, through a
-- marked node resolution leaves alone -- so exporting them is a choice made for
-- the reader, and `map` is the one most likely to be missed.
class Functor f {
    fun map(f a, fun(a) -> b) -> f b
}

class Applicative f : Functor f {
    fun pure(a) -> f a
}

class Monad m : Applicative m {
    fun bind(m a, fun(a) -> m b) -> m b
}

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

instance Add String { fun add(x, y) = Prim.stringConcat(x, y) }

instance Add Float { fun add(x, y) = Prim.floatAdd(x, y) }
instance Sub Float { fun sub(x, y) = Prim.floatSub(x, y) }
instance Mul Float { fun mul(x, y) = Prim.floatMul(x, y) }
instance Div Float { fun div(x, y) = Prim.floatDiv(x, y) }
instance Neg Float { fun neg(x) = Prim.floatNeg(x) }

-- A cursor over an array is a mutable index. Nothing else in the language may
-- name it; it exists so that `Cursor (Array a)` has something to be.
type ArrayCursor = ArrayCursor { at : Int }

-- What a `return`, `break` or `continue` becomes when it has to cross a `?`
-- (delta 47). Like `ArrayCursor` above, nothing else in the language may name
-- it: it is not exported, and only `turkey/desugar.py` ever writes one.
--
-- A transfer of control cannot *escape* through a `bind` -- escaping is not
-- something a `bind` does, and for `Array`, whose bind runs its continuation
-- once per element, there is no coherent thing for an escape to mean. So it
-- travels as a value instead, because a value is the only thing a `bind`
-- propagates. Each statement in a block that needs this answers with one of
-- these, and the next statement runs only under `Fall`.
--
--   `Fall(a)`  fell off the end with this value; carry on
--   `Brk(b)`   the enclosing loop is finished, with this value
--   `Cont`     this iteration is finished
--   `Ret(r)`   the enclosing function is finished, with this value
--
-- Three parameters because the three values are three types: a block's own
-- value, its loop's, and its function's.
type Flow a b r = Fall(a) | Brk(b) | Cont | Ret(r)

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

-- The three monads that ship. `Option` is failure with nothing to say, `Either
-- l` is failure that says why, and `Array` is the list monad -- the one whose
-- bind invokes its continuation more than once, and so the one that settles
-- that `?` means bind and not an early return.
instance Functor Option {
    fun map(o, g) = match o {
        Some(x) -> Some(g(x))
        None -> None
    }
}

instance Applicative Option { fun pure(x) = Some(x) }

instance Monad Option {
    fun bind(o, g) = match o {
        Some(x) -> g(x)
        None -> None
    }
}

-- `Either l` fixes the left parameter and varies the right, because a class has
-- one parameter and the right is what is left over. The rightward bias every
-- `Either` in every language has is that fact, not a taste.
instance Functor (Either l) {
    fun map(e, g) = match e {
        Right(x) -> Right(g(x))
        Left(y) -> Left(y)
    }
}

instance Applicative (Either l) { fun pure(x) = Right(x) }

instance Monad (Either l) {
    fun bind(e, g) = match e {
        Right(x) -> g(x)
        Left(y) -> Left(y)
    }
}

instance Functor Array {
    fun map(xs, g) {
        let out = Array.new(xs.length)
        for x in xs { Array.push(out, g(x)) }
        out
    }
}

instance Applicative Array { fun pure(x) = [x] }

instance Monad Array {
    fun bind(xs, g) {
        let out = Array.new(xs.length)
        for x in xs {
            for y in g(x) { Array.push(out, y) }
        }
        out
    }
}

instance Show Int { fun show(x) = Int.toString(x) }
instance Show Float { fun show(x) = Float.toString(x) }
instance Show Bool { fun show(x) = Bool.toString(x) }
instance Show Char { fun show(x) = Char.toString(x) }
instance Show String { fun show(x) = x }

instance [Show a] Show (Option a) {
    fun show(o) = match o {
        None -> "None"
        Some(x) -> "Some(" + show(x) + ")"
    }
}

instance [Show l, Show r] Show (Either l r) {
    fun show(e) = match e {
        Left(x) -> "Left(" + show(x) + ")"
        Right(x) -> "Right(" + show(x) + ")"
    }
}

instance [Show a] Show (Array a) {
    fun show(xs) {
        var out = "["
        var rest = False
        for x in xs {
            if rest { out = out + ", " }
            out = out + show(x)
            rest = True
        }
        out + "]"
    }
}

-- The Prelude's own bindings. `print` and `write` are the only place
-- `Prim.print` and `Prim.write` are reachable from, and both infer
-- `[Show a] fun(a) -> Unit`. `error` diverges, so it can claim any result
-- type (section 10).
fun print(x) = Prim.print(show(x))
fun write(x) = Prim.write(show(x))

fun error(message : String) -> a = Prim.error(message)
