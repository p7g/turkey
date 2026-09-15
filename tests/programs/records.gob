-- M9.1/M9.2: a record variant is a positional variant plus a list of names,
-- so either form constructs it and either form matches it. Only the record
-- form may leave fields out; a positional pattern must name every one.

type Shape =
      Circle { radius : Int }
    | Rect { width : Int, height : Int }

-- A single-variant record, which is the mutable shape (section 6.3).
type Point = Point { x : Int, y : Int }

fun area(s : Shape) -> Int {
    match s {
        Circle(r) -> r * r * 3
        Rect { width, height } -> width * height
    }
}

fun describe(s : Shape) -> String {
    match s {
        Circle { radius } -> "circle of " + Int.toString(radius)
        Rect(w, h) -> Int.toString(w) + "x" + Int.toString(h)
    }
}

-- A record pattern may name a subset; a positional one may not.
fun width(s : Shape) -> Int {
    match s {
        Rect { width } -> width
        Circle(_) -> 0
    }
}

fun shift(p : Point, dx : Int) -> Point {
    let x = p.x + dx
    let y = p.y
    Point { x, y }        -- punning, as patterns have always allowed
}

fun main() {
    print(Int.toString(area(Circle(2))))
    print(Int.toString(area(Rect { width = 3, height = 4 })))
    print(describe(Circle { radius = 5 }))
    print(describe(Rect(6, 7)))
    print(Int.toString(width(Rect(6, 7))))

    let Point(a, b) = shift(Point { x = 1, y = 2 }, 10)
    print(Int.toString(a) + "," + Int.toString(b))

    match shift(Point(0, 0), 4) {
        Point { x } -> print(Int.toString(x))
    }
}
