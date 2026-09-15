type State = State { count : Int, text : String }
type Acc = Acc { parts : Array Int }
type Item = Item { value : Int }

fun tally(xs : Array Item) -> Int = match Array.at(xs, 0) {
    None -> 0
    Some(b) -> b.value
}

fun local(n : Int) -> String {
    var text = "start"
    let state = State { count = 0, text = "record" }
    for var i = 0; i < n; i = i + 1 {
        if i == 1 { continue }
        text = text + Int.toString(i)
        if i == 3 { state.text = text; break }
        state.count = state.count + i
        state.text = state.text + "."
    }
    print(state.count)
    state.text + ":" + text
}

fun main() {
    print(local(5))
    print(local(0))
    var shared = 0
    let escaped = State { count = 10, text = "kept" }
    let callbacks : Array (fun() -> Int) = [fun() -> Int = {
        shared = shared + 1
        escaped.count = escaped.count + shared
        escaped.count
    }]
    print(callbacks[0]())
    print(callbacks[0]())
    print(shared)
    print(escaped.count)
    let items = [Item { value = 42 }]
    let b = Acc { parts = Array.new(4) }
    Array.push(b.parts, tally(items))
    print(b.parts[0])
    var f = 1.25
    for var j = 0; j < 3; j = j + 1 { f = f + 0.5 }
    print(f)
}
