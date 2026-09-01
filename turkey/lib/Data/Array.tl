module Data.Array (
    Array,
    new,
    push,
    pop,
    map,
    filter,
    fold,
    reverse,
    append,
)

import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Int ()
import Data.Option.Type (Option(..))

type Array a = Array {
    raw : Prim.Array a
}

fun new(capacity : Int) -> Array a = Array { raw = Prim.arrayNew(capacity) }
fun push(xs : Array a, x : a) -> Unit = Prim.arrayPush(xs.raw, x)
fun pop(xs : Array a) -> Option a = Prim.arrayPop(xs.raw)

fun map(xs : Array a, f : fun(a) -> b) -> Array b {
    let out = new(len(xs))
    for x in xs { push(out, f(x)) }
    out
}

fun filter(xs : Array a, keep : fun(a) -> Bool) -> Array a {
    let out = new(len(xs))
    for x in xs {
        if keep(x) { push(out, x) }
    }
    out
}

fun fold(xs : Array a, initial : b, step : fun(b, a) -> b) -> b {
    var acc = initial
    for x in xs { acc = step(acc, x) }
    acc
}

fun reverse(xs : Array a) -> Array a {
    let out = new(len(xs))
    var i = len(xs) - 1
    while i >= 0 {
        push(out, xs[i])
        i = i - 1
    }
    out
}

fun append(xs : Array a, ys : Array a) -> Array a {
    let out = new(len(xs) + len(ys))
    for x in xs { push(out, x) }
    for y in ys { push(out, y) }
    out
}

instance Index (Array a) {
    type IndexKey = Int
    type IndexItem = a
    fun get(xs, i) = Prim.arrayGet(xs.raw, i)
    fun set(xs, i, x) = Prim.arraySet(xs.raw, i, x)
}

instance Length (Array a) { fun len(xs) = Prim.arrayLength(xs.raw) }

type ArrayCursor = ArrayCursor { at : Int }

instance Iterator (Array a) {
    type Item = a
    type Cursor = ArrayCursor
    fun iter(xs) = ArrayCursor { at = 0 }
    fun next(xs, cur) {
        if cur.at >= len(xs) { return None }
        let x = get(xs, cur.at)
        cur.at = cur.at + 1
        Some(x)
    }
}

instance Add (Array a) { fun add(xs, ys) = append(xs, ys) }
instance Semigroup (Array a) { fun combine(xs, ys) = append(xs, ys) }
instance Monoid (Array a) { fun empty() = [] }

instance Foldable Array {
    fun foldMap[Monoid m](xs, f) {
        var acc : m = empty()
        for x in xs { acc = combine(acc, f(x)) }
        acc
    }
}

instance Functor Array {
    fun map(xs, f) {
        let out = new(len(xs))
        for x in xs { push(out, f(x)) }
        out
    }
}

instance Applicative Array { fun pure(x) = [x] }

instance Monad Array {
    fun bind(xs, f) {
        let out = new(len(xs))
        for x in xs {
            for y in f(x) { push(out, y) }
        }
        out
    }
}

instance Show (Array a) : Show a {
    fun show(xs) {
        var out = "["
        var rest = False
        for x in xs {
            if rest { out = Prim.stringConcat(out, ", ") }
            out = Prim.stringConcat(out, show(x))
            rest = True
        }
        Prim.stringConcat(out, "]")
    }
}
