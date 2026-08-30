type Tree a = Leaf | Node(Tree a, a, Tree a)
type Counter = Counter { n : Int }

fun id(x) = x
fun apply(f, x) = f(x)

fun size(t) -> Int = match t {
    Leaf -> 0
    Node(l, _, r) -> size(l) + 1 + size(r)
}

fun isEven(n) = if n == 0 { True } else { isOdd(n - 1) }
fun isOdd(n) = if n == 0 { False } else { isEven(n - 1) }

fun classify(o) -> String = match o {
    None -> "none"
    Some(x) -> "some " ++ Int.toString(x)
}

fun bump(c : Counter) -> Unit { c.n = c.n + 1 }

fun firstEven(xs : Array Int) -> Option Int {
    for x in xs {
        if x % 2 == 0 { return Some(x) }
    }
    None
}

fun countdown(n : Int) -> Int {
    var i = n
    loop {
        if i <= 0 { break i }
        i = i - 1
    }
}

let poly = id
let tree = Node(Leaf, 1, Node(Leaf, 2, Leaf))

fun main() {
    print(Int.toString(size(tree)))
    print(Bool.toString(isEven(10)))
    print(classify(Some(3)))
    print(classify(None))
    let c = Counter { n = 0 }
    let alias = c
    bump(c)
    bump(alias)
    print(Int.toString(c.n))
    print(classify(firstEven([1, 3, 4, 5])))
    print(Int.toString(countdown(5)))
    print(poly("polymorphic use") ++ " / " ++ Int.toString(poly(7)))
    print(Int.toString(apply(fun(y) = y * 2, 21)))
}
