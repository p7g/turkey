module Data.Array (
    Array,
    new,
    filled,
    push,
    pop,
    map,
    filter,
    fold,
    reverse,
    append,
)

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Int ()
import Data.Option.Type (Option(..))

type ArrayStorage a = ArrayStorage {
    storage : Prim.Array a,
    length  : Int,
}

type Array a = Array(ArrayStorage a)

fun state(Array(value)) = value

fun new(capacity : Int) -> Array a = Array(ArrayStorage {
    storage = Prim.arrayNewUninit(capacity),
    length = 0,
})

fun filled(length : Int, value : a) -> Array a = Array(ArrayStorage {
    storage = Prim.arrayNew(length, value),
    length,
})

fun grow(xs : Array a) {
    let data = state(xs)
    let capacity = Prim.arrayLength(data.storage)
    if data.length < capacity { return }
    let nextCapacity = if capacity == 0 { 1 } else { capacity * 2 }
    let storage = Prim.arrayNewUninit(nextCapacity)
    for var i = 0; i < data.length; i = i + 1 {
        Prim.arraySet(storage, i, Prim.arrayGet(data.storage, i))
    }
    data.storage = storage
}

fun push(xs : Array a, x : a) -> Unit {
    grow(xs)
    let data = state(xs)
    Prim.arraySet(data.storage, data.length, x)
    data.length = data.length + 1
}

fun pop(xs : Array a) -> Option a {
    let data = state(xs)
    if data.length == 0 { return None }
    data.length = data.length - 1
    Some(Prim.arrayGet(data.storage, data.length))
}

fun bounds(xs : Array a, i : Int, operation : String) {
    let length = state(xs).length
    if i < 0 || i >= length {
        Prim.error(Prim.stringConcat(
            Prim.stringConcat(
                Prim.stringConcat(
                    Prim.stringConcat("array index out of bounds: ", operation),
                    " index "),
                Prim.intToString(i)),
            Prim.stringConcat(", length ", Prim.intToString(length))))
    }
}

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
    type Key = Int
    type Value = a
    fun get(xs, i) {
        bounds(xs, i, "read at")
        Prim.arrayGet(state(xs).storage, i)
    }
    fun set(xs, i, x) {
        bounds(xs, i, "write at")
        Prim.arraySet(state(xs).storage, i, x)
    }
}

instance Length (Array a) { fun len(xs) = state(xs).length }

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
