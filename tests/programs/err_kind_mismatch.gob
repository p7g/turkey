-- `Wrap`'s first parameter is used as `f a` in its body, so it has kind
-- `* -> *`. Supplying `Int` is rejected where it is written, not later as a
-- confusing mismatch between two saturated types.

type Wrap f a = Wrap(f a)

fun broken(w : Wrap Int Bool) = w

fun main() {
    print("unreachable")
}
