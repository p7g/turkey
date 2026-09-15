type Methods = Methods { run : fun(Int) -> Int, spare : fun(Int) -> Int }
type Effect = Effect { run : fun(Int) -> Int, side : Unit }
fun main() {
    print((Methods {
        run = fun(x : Int) -> Int = x + 2,
        spare = fun(x : Int) -> Int = x - 1,
    }).run(5))
    print((Effect {
        run = fun(x : Int) -> Int = x + 3,
        side = print("sibling"),
    }).run(5))
}
