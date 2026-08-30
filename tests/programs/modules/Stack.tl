module Stack (Stack, empty, push, pop) where

-- `push` and `pop` are the point of this module: before M11a the built-in
-- `Array.push` claimed the bare name and a program could not define one.
type Stack a = Stack { items : Array a }

fun empty() -> Stack a = Stack { items = [] }

fun push(s : Stack a, x : a) -> Unit { Array.push(s.items, x) }

fun pop(s : Stack a) -> Option a = Array.pop(s.items)
