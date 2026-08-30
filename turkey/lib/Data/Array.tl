-- design.md section 8.3. `Array` itself is a built-in type constructor, with
-- literal syntax and indexing; what a program calls by name is here.
module Data.Array (new, push, pop) where

import Data.Option (Option(..))

-- `new(n)` reserves capacity, it does not fill it: the array's length is zero
-- and `a[0]` still panics (delta 8).
fun new(capacity : Int) -> Array a = Prim.arrayNew(capacity)

fun push(xs : Array a, x : a) -> Unit = Prim.arrayPush(xs, x)

-- Total, where indexing past the length is still a panic (delta 37).
fun pop(xs : Array a) -> Option a = Prim.arrayPop(xs)
