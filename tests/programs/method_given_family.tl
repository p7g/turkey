-- A class method whose own context has an equality on the class's associated
-- family, implemented by instances that need that equality. The body is
-- inferred first and meets its signature last, so what its predicates are
-- about reaches them as an unreduced family application, and only the given
-- `Elem (Iter s) ~ Part c` -- with `c` the instance head -- says what it is.
-- Discharge, the retry of a deferred equation, and elaboration all have to
-- read that given, and the lowering has to know it is not a dictionary.
-- FINDINGS 84.
import Data.Set as Set
import Data.Set (Set)

class Iterable c {
    type Iter c
    fun iterate(c) -> Iter c
}

class Stream i {
    type Elem i
    fun advance(i) -> Option (Elem i)
}

type ArrayIt a = ArrayIt { xs : Array a, at : Int }

instance Stream (ArrayIt a) {
    type Elem = a
    fun advance(it) {
        if it.at >= len(it.xs) { return None }
        let x = it.xs[it.at]
        it.at = it.at + 1
        Some(x)
    }
}

instance Iterable (Array a) {
    type Iter = ArrayIt a
    fun iterate(xs) = ArrayIt { xs = xs, at = 0 }
}

class Collect c {
    type Part c
    fun collect[Iterable s, Stream (Iter s), Elem (Iter s) ~ Part c](s) -> c
}

-- The report: `Hash (Elem (Iter s))` is `Hash a` only by the given.
instance Collect (Set a) : Hash a {
    type Part = a
    fun collect(s) {
        let out = Set.new()
        let it = iterate(s)
        loop {
            match advance(it) {
                None -> break
                Some(x) -> { Set.add(out, x) }
            }
        }
        out
    }
}

-- No constraint on the elements: this type-checked before, and died in the
-- lowering, which took the equality for a dictionary parameter.
instance Collect (Array a) {
    type Part = a
    fun collect(s) {
        let out = []
        let it = iterate(s)
        loop {
            match advance(it) {
                None -> break
                Some(x) -> { Array.push(out, x) }
            }
        }
        out
    }
}

-- A deferred *equation*: matching `Some(None)` says the element is an
-- `Option`, and the given has to decide that on retry.
instance Collect (Option c) : Collect c {
    type Part = Option (Part c)
    fun collect(s) {
        let it = iterate(s)
        match advance(it) {
            None -> None
            Some(None) -> None
            Some(Some(x)) -> Some(collect([x]))
        }
    }
}

-- The same given in a default body, checked once against the class variable.
class Sink c {
    type Item c
    fun push(c, Item c) -> Unit
    fun pushAll[Iterable s, Stream (Iter s), Elem (Iter s) ~ Item c](c : c, s : s) -> Unit {
        let it = iterate(s)
        loop {
            match advance(it) {
                None -> break
                Some(x) -> { push(c, x) }
            }
        }
    }
}

type Counter = Counter { total : Int }

instance Sink Counter {
    type Item = Int
    fun push(c, n) { c.total = c.total + n }
}

fun main() {
    let s : Set Int = collect([3, 1, 3])
    print(s)
    let a : Array String = collect(["x", "y"])
    print(a)
    let o : Option (Array Int) = collect([Some(4), None])
    print(o)
    let none : Option (Array Int) = collect([None, Some(4)])
    print(none)
    let c = Counter { total = 0 }
    pushAll(c, [1, 2, 3, 4])
    print(c.total)
}
