-- Bottom-typed expressions and the loop family (design.md sections 4.6, 6.7).

fun pick(n : Int) -> Int {
    -- One arm diverges; `join` must yield the other arm's type, not bottom.
    if n < 0 { return error("negative") } else { n }
}

fun firstMultiple(limit : Int, of : Int) -> Int {
    var i = 1
    loop {
        if i > limit { break -1 }
        if i % of == 0 { break i }
        i = i + 1
    }
}

fun countSkipping(limit : Int) -> Int {
    var total = 0
    for var i = 0; i < limit; i = i + 1 {
        if i % 3 == 0 { continue }
        if i > 7 { break }
        total = total + i
    }
    total
}

fun neverReturns() -> Int {
    loop { }
}

fun whileSum(limit : Int) -> Int {
    var total = 0
    var i = 0
    while true {
        if i >= limit { break }
        total = total + i
        i = i + 1
    }
    total
}

fun main() {
    print(Int.toString(pick(5)))
    print(Int.toString(firstMultiple(20, 7)))
    print(Int.toString(firstMultiple(3, 7)))
    print(Int.toString(countSkipping(20)))
    print(Int.toString(whileSum(5)))
}
