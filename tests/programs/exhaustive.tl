-- Exhaustiveness (design.md section 5.1). A missing case is a *compile error*
-- since SPEC-DELTAS 61; the three programs that used to be the second half of
-- this file are `err_exhaustive_option.tl`, `err_exhaustive_nested.tl` and
-- `err_exhaustive_catchall.tl`.
--
-- What stays here is the other half of that check, and it is the half worth
-- keeping at full breadth: a match the checker must *accept*. An
-- exhaustiveness rule that rejects a total match is now a rule that rejects a
-- working program, so the cost of a false positive went up when this became an
-- error.
-- `Option`, matched on below, comes from the prelude.

type Color = Red | Green | Blue

-- Every constructor is covered.
fun describe(c : Color) -> String = match c {
    Red -> "red"
    Green -> "green"
    Blue -> "blue"
}

-- Exhaustive through a tuple, including the nested Bool.
fun pair(p : (Color, Bool)) -> Int = match p {
    (Red, True) -> 1
    (Red, False) -> 2
    (Green, _) -> 3
    (Blue, _) -> 4
}

-- Exhaustive at depth: every shape of a nested `Option` is named.
fun flatten(o : Option (Option Int)) -> Int = match o {
    None -> 0
    Some(None) -> 1
    Some(Some(n)) -> n
}

-- A scrutinee with too many values to enumerate is exhaustive through a
-- catch-all, which is the only way an `Int` can be.
fun small(n : Int) -> Bool = match n {
    0 -> True
    1 -> True
    _ -> False
}

-- A wildcard covering the constructors it does not name.
fun warm(c : Color) -> Bool = match c {
    Red -> True
    _ -> False
}

fun main() {
    print(describe(Green))
    print(Int.toString(pair((Blue, True))))
    print(Int.toString(flatten(Some(Some(7)))))
    print(Bool.toString(small(0)))
    print(Bool.toString(small(9)))
    print(Bool.toString(warm(Red)))
    print(Bool.toString(warm(Blue)))
}
