type Stack a = Stack {
    data : Array a,
    top  : Int
}

fun newStack(capacity : Int) -> Stack a {
    Stack { data = Array.new(capacity), top = 0 }
}

fun push(s : Stack a, x : a) -> Unit {
    Array.push(s.data, x)
    s.top = s.top + 1
}

fun pop(s : Stack a) -> a {
    if s.top == 0 { return error("empty stack") }
    s.top = s.top - 1
    Array.pop(s.data)
}

fun drain(s : Stack a) -> Array a {
    let out = [] : Array a
    loop {
        if s.top == 0 { break out }
        Array.push(out, pop(s))
    }
}

fun main() {
    let s = newStack(4)
    push(s, 10)
    push(s, 20)
    push(s, 30)
    let d = drain(s)
    for x in d { print(Int.toString(x)) }
    print(Int.toString(d.length))
}
