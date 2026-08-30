import Stack (empty, push, pop)
import qualified Geometry as G

-- A bare `add`, which every version before M11a rejected outright.
fun add(x : String, y : String) -> String = x ++ y

fun main() {
    let s = empty()
    push(s, 1)
    push(s, 2)
    print(pop(s))
    print(pop(s))
    print(pop(s))

    print(add("mod", "ules"))
    print(1 + 2)                -- still Add.add, not the local `add`

    -- A constructor is global in M11a even under a qualified import; the
    -- functions are not.
    print(G.describe(G.translate(Point(1, 2), 3, 4)))
}
