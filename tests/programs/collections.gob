-- The container surface a compiler needs: reading without panicking, a stable
-- sort, and the three questions a map was missing (M18c).

import Data.Array as A
import Data.Map as Map
import Data.Map (Map)
import Data.Set as Set
import Data.Set (Set)

type Word = Word { text : String, rank : Int }

fun main() {
    let xs = [5, 3, 9, 1, 3, 8, 2]

    -- `sort` answers a new array; the original is untouched.
    print(show(A.sort(xs)))
    print(show(xs))
    print(show(A.sortBy(xs, fun(x, y) = A.threeWay(y, x))))
    print(show(A.sort([] : Array Int)) + show(A.sort([7])))
    print(show(A.sort(["pear", "apple", "fig"])))

    -- Stability is observable: two records comparing equal on `rank` come out
    -- in the order they went in.
    let ws = [
        Word { text = "first", rank = 1 },
        Word { text = "tied a", rank = 0 },
        Word { text = "tied b", rank = 0 },
    ]
    var out = "stable:"
    for w in A.sortBy(ws, fun(x, y) = A.threeWay(x.rank, y.rank)) {
        out = out + " " + w.text
    }
    print(out)

    print(show(A.at(xs, 2)) + " " + show(A.at(xs, 99)) + " " + show(A.at(xs, -1)))
    print(show(A.last(xs)) + " " + show(A.last([] : Array Int)))
    print(show(A.slice(xs, 2, 5)) + show(A.slice(xs, 0, 0)))
    print(show(A.indexOf(xs, 9)) + " " + show(A.indexOf(xs, 100)))
    print(Bool.toString(A.contains(xs, 8)) + Bool.toString(A.contains(xs, 100)))
    let swapped = A.slice(xs, 0, len(xs))
    A.swap(swapped, 0, 6)
    print(show(swapped))

    -- A map answers in insertion order, which is what makes a program built on
    -- one reproducible.
    let m : Map String Int = Map.new()
    Map.put(m, "b", 2)
    Map.put(m, "a", 1)
    print(Bool.toString(Map.contains(m, "a")) + Bool.toString(Map.contains(m, "z")))
    print(Int.toString(Map.getOr(m, "a", -1)) + " " + Int.toString(Map.getOr(m, "z", -1)))
    Map.update(m, "a", 0, fun(n) = n + 10)
    Map.update(m, "fresh", 0, fun(n) = n + 5)
    print(show(m))
    print(show(Map.keys(m)))
    print(show(Map.values(m)))
    print(show(Map.entries(m)))

    let s : Set String = Set.new()
    Set.add(s, "x")
    Set.add(s, "y")
    Set.add(s, "x")
    print(show(s) + " " + Int.toString(len(s)))
    print(Bool.toString(Set.contains(s, "y")) + Bool.toString(Set.contains(s, "z")))
    print(Bool.toString(Set.remove(s, "y")) + Bool.toString(Set.remove(s, "y")))
    print(show(Set.elements(s)))
    for e in s { print("elem " + e) }
}
