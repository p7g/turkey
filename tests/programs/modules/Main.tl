import Stack (empty, push, pop)
import qualified Geometry as G

-- A bare `add`, which every version before M11a rejected outright.
fun add(x : String, y : String) -> String = x + y

fun main() {
    let s = empty()
    push(s, 1)
    push(s, 2)
    print(pop(s))
    print(pop(s))
    print(pop(s))

    print(add("mod", "ules"))
    print(1 + 2)                -- still Add.add, not the local `add`

    -- A constructor is scoped like everything else (delta 43), so under a
    -- qualified import it is written `G.Point`.
    print(G.describe(G.translate(G.Point(1, 2), 3, 4)))
}
