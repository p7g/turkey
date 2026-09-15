module Geometry (Point(..), translate, describe)

type Point = Point(Int, Int)

fun translate(p : Point, dx : Int, dy : Int) -> Point = match p {
    Point(x, y) -> Point(x + dx, y + dy)
}

fun describe(p : Point) -> String = match p {
    Point(x, y) -> "(" + comma(Int.toString(x), Int.toString(y)) + ")"
}

-- Not exported: `Main` may not name it, even though `describe` is written in
-- terms of it.
fun comma(a : String, b : String) -> String = a + ", " + b
