-- let-generalization and the value restriction (design.md section 4.4).

type Box a = MkBox(a)

fun identity(x) = x

fun main() {
    -- A lambda is a syntactic value, so a `let` binding of one generalizes.
    let g = fun(x) = x
    print(g("string"))
    print(Int.toString(g(1)))

    -- A constructor applied to values is still a value.
    let boxed = MkBox(identity)
    print("boxed a polymorphic function")

    -- An array literal is expansive: its element type stays monomorphic.
    let numbers = [1, 2, 3]
    print(Int.toString(numbers[2]))
}
