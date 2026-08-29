-- Reference semantics for records and arrays (design.md section 6.3, 6.5).

type Cell = Cell { value : Int }

fun bump(c : Cell) -> Unit { c.value = c.value + 1 }

fun main() {
    -- `let d = c` aliases; writes through one are visible through the other.
    let c = Cell { value = 0 }
    let d = c
    bump(c)
    d.value = d.value + 10
    print(Int.toString(c.value))

    -- `for x in arr` binds a reference, so mutating x mutates the array.
    let cells = [Cell { value = 1 }, Cell { value = 2 }]
    for x in cells { x.value = x.value * 100 }
    print(Int.toString(cells[0].value))
    print(Int.toString(cells[1].value))

    -- Arrays are references too.
    let a = [1, 2, 3]
    let b = a
    b[0] = 99
    print(Int.toString(a[0]))
    print(Int.toString(a.length))

    -- push grows capacity; length tracks what has actually been written.
    let grown = Array.new(1)
    Array.push(grown, 1)
    Array.push(grown, 2)
    Array.push(grown, 3)
    print(Int.toString(grown.length))
    print(Bool.toString(grown.capacity >= grown.length))

    -- `var` rebinds the name; `let` data stays mutable either way.
    var current = c
    current = Cell { value = 42 }
    print(Int.toString(current.value))
    print(Int.toString(c.value))
}
