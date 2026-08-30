-- An associated type family: a type the class computes from its own
-- parameter. `Elem` is what makes `Container` a single-parameter class -- the
-- element type is a *function* of the container, not a second parameter that
-- would need a functional dependency to pin down.

class Show a {
    fun show(a) -> String
}

instance Show Int {
    fun show(n) = Int.toString(n)
}

instance Show Bool {
    fun show(b) = if b { "true" } else { "false" }
}

class Container c {
    type Elem c

    fun first(c) -> Elem c
    fun size(c) -> Int

    -- A default whose type mentions the family. `Elem c` is rigid inside this
    -- body -- `c` is the class's own variable, made a skolem -- so nothing
    -- reduces here and the definition has to be generic in the element.
    fun firstOr(xs : c, fallback : Elem c) -> Elem c {
        if size(xs) == 0 { return fallback }
        return first(xs)
    }
}

instance Container (Array a) {
    type Elem = a

    fun first(xs) = xs[0]
    fun size(xs) = xs.length
}

type Pair = Pair { left : Int, right : Int }

instance Container Pair {
    type Elem = Int

    fun first(p) = p.left
    fun size(p) = 2
}

-- `Show (Elem c)` constrains a type nobody wrote down and no argument
-- mentions. It waits while `c` is open and reduces the moment `c` is a
-- constructor, which is the whole of what a family buys.
fun describe[Container c, Show (Elem c)](xs : c) -> String {
    return show(first(xs)) ++ " of " ++ Int.toString(size(xs))
}

-- Inferred, context and all: nothing here is annotated.
fun twice(xs) = [first(xs), first(xs)]

fun main() {
    print(describe([10, 20, 30]))
    print(describe(Pair { left = 7, right = 8 }))
    print(describe([true]))
    -- Reduced at a nested head: `Elem (Array (Array Int))` is `Array Int`.
    print(Int.toString(size(first([[1, 2], [3]]))))
    print(Int.toString(first(twice([9, 8])) + 1))
    -- One elaboration of the default, two element types.
    print(Int.toString(firstOr([], 9)))
    print(describe(Pair { left = 1, right = 2 }))
}
