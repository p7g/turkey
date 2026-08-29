type Cell = Cell { n : Int }

-- The receiver is a variable that nothing else mentions, so no scheme can
-- carry the demand and no later unification can reach it. This is what is
-- left of the old "add a type annotation" error now that an unresolved
-- receiver is, by itself, perfectly fine.
fun main() {
    var box = []
    print(Int.toString(box[0].n))
}
