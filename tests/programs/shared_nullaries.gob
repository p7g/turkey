type Flag = First | Second
let initial : Flag = First
fun choose(i : Int) -> Flag = if i % 2 == 0 { First } else { Second }
fun main() {
    let flags : Array Flag = Array.new(64)
    let strings : Array String = Array.new(64)
    let empty : Array String = []
    for var i = 0; i < 20; i = i + 1 {
        Array.push(flags, choose(i))
        Array.push(strings, "value" + Int.toString(i))
    }
    var first = 0
    for flag in flags {
        match flag { First -> { first = first + 1 }; Second -> {} }
    }
    print(first)
    print(match initial { First -> "first"; Second -> "second" })
    print(strings[19])
    print(len(empty))
    let filled = Array.filled(4, Second)
    print(match filled[3] { First -> 1; Second -> 2 })
}
