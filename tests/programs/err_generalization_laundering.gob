-- The value restriction has to survive being read through a function.
-- `cell` is expansive, so its element type stays monomorphic; returning it
-- from `get` must not hand out a fresh element type on every call.

let cell = Array.new(4)

fun get() = cell

fun main() {
    Array.push(get(), 1)
    Array.push(get(), "not an int")
}
