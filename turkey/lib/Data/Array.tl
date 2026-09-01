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
    at,
    last,
    swap,
    slice,
    indexOf,
    contains,
    sortBy,
    sort,
    threeWay,
)

import Prelude ()
import Std.Classes
import Data.Bool.Type (Bool(..))
import Data.Int ()
import Data.Option.Type (Option(..))
import Data.Ordering.Type (Ordering(..))

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

-- The message is built in its own function, and that is the whole point of
-- it being one. Every string in it is allocated, so building it inline made
-- the *failure* path decide what the success path costs: the pointers live
-- across those allocations all need to be visible to the collector, so
-- `bounds` paid a thirteen-slot root frame, a root registration and a 288-byte
-- stack frame on every in-range access, and was too large to inline besides.
-- Rust outlines the same path into a cold `panic_bounds_check` for the same
-- reason.
fun outOfBounds(operation : String, i : Int, length : Int) -> a {
    Prim.error(Prim.stringConcat(
        Prim.stringConcat(
            Prim.stringConcat(
                Prim.stringConcat("array index out of bounds: ", operation),
                " index "),
            Prim.intToString(i)),
        Prim.stringConcat(", length ", Prim.intToString(length))))
}

fun bounds(xs : Array a, i : Int, operation : String) {
    let length = state(xs).length
    if i < 0 || i >= length { outOfBounds(operation, i, length) }
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

-- -- reading without panicking -------------------------------------------------
--
-- `xs[i]` is the `Index` method and panics, which is right for a program that
-- knows the index is good. `at` is for the one that is asking.
--
-- Not called `get`: that name belongs to the `Index` class, and this is not an
-- instance of it -- an `Index` method must answer `Index.Value`, which is the
-- element, not an `Option` of it.

fun at(xs : Array a, i : Int) -> Option a =
    if i < 0 || i >= len(xs) { None } else { Some(xs[i]) }

fun last(xs : Array a) -> Option a = at(xs, len(xs) - 1)

fun swap(xs : Array a, i : Int, j : Int) -> Unit {
    let x = xs[i]
    xs[i] = xs[j]
    xs[j] = x
}

-- A copy, not a view. A view would alias storage that `push` is free to
-- reallocate out from under it, and the growth policy is deliberately not part
-- of this type's contract.
fun slice(xs : Array a, from : Int, to : Int) -> Array a {
    if from < 0 || to > len(xs) || from > to {
        error(Prim.stringConcat(
            Prim.stringConcat("array slice out of bounds: ", Prim.intToString(from)),
            Prim.stringConcat("..", Prim.intToString(to))))
    }
    let out = new(to - from)
    for var i = from; i < to; i = i + 1 { push(out, xs[i]) }
    out
}

fun indexOf[Eq a](xs : Array a, wanted : a) -> Option Int {
    for var i = 0; i < len(xs); i = i + 1 {
        if xs[i] == wanted { return Some(i) }
    }
    None
}

fun contains[Eq a](xs : Array a, wanted : a) -> Bool = match indexOf(xs, wanted) {
    Some(_) -> True
    None -> False
}

-- -- sorting -------------------------------------------------------------------
--
-- A stable merge sort, answering a new array. Stable because the alternative is
-- a sort whose result depends on how the input happened to be arranged, which
-- makes a golden file a coin toss -- and M26 compares two compilers' output
-- byte for byte.
--
-- Merge, not quicksort: n log n is a bound rather than an average, there is no
-- pivot choice to get wrong on already-sorted input, and stability is free. The
-- scratch array is allocated once for the whole sort rather than per merge.
--
-- On `Ord Float`, the language's one lawless instance: this terminates and
-- panics nowhere, and on an array containing NaN the result is unspecified
-- rather than sorted (PRIMITIVES.md 3.2b). Passing `Float.totalCompare` to
-- `sortBy` is how to have an opinion about NaN.

fun sortBy(xs : Array a, compare : fun(a, a) -> Ordering) -> Array a {
    let out = new(len(xs))
    for x in xs { push(out, x) }
    if len(out) < 2 { return out }
    let scratch = new(len(xs))
    for x in xs { push(scratch, x) }
    mergeRange(out, scratch, 0, len(out), compare)
    out
}

fun mergeRange(xs : Array a, scratch : Array a, lo : Int, hi : Int,
               compare : fun(a, a) -> Ordering) -> Unit {
    if hi - lo < 2 { return }
    let mid = lo + (hi - lo) / 2
    mergeRange(xs, scratch, lo, mid, compare)
    mergeRange(xs, scratch, mid, hi, compare)
    var left = lo
    var right = mid
    var out = lo
    while out < hi {
        if left >= mid {
            scratch[out] = xs[right]
            right = right + 1
        } else {
            if right >= hi {
                scratch[out] = xs[left]
                left = left + 1
            } else {
                -- The left run wins ties, which is what makes this stable.
                match compare(xs[right], xs[left]) {
                    LT -> {
                        scratch[out] = xs[right]
                        right = right + 1
                    }
                    _ -> {
                        scratch[out] = xs[left]
                        left = left + 1
                    }
                }
            }
        }
        out = out + 1
    }
    for var i = lo; i < hi; i = i + 1 { xs[i] = scratch[i] }
}

fun sort[Ord a](xs : Array a) -> Array a = sortBy(xs, threeWay)

-- `Ord` answers in `Bool`, so a three-way comparison costs two questions.
-- `Ordering` is the shape a sort wants, and this is the adapter.
fun threeWay[Ord a](x : a, y : a) -> Ordering {
    if x < y { return LT }
    if y < x { return GT }
    EQ
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
