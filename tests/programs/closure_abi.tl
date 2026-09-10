-- Closure boundaries preserve scalar widths, float registers, and traced roots.
fun apply(f : fun(Int, Float, String) -> String, x : Int) -> String =
    f(x, 2.5, "kept")
fun number(x : Float) -> Float = x + 1.25
fun main() {
    let fs : Array (fun(Int, Float, String) -> String) = [
        fun(n : Int, x : Float, s : String) -> String =
            s + Int.toString(n) + Float.toString(x)
    ]
    print(apply(fs[0], 7))
    let floats : Array (fun(Float) -> Float) = [number]
    print(floats[0](3.5))
    let strings : Array (fun(String, String) -> String) = [
        fun(a : String, b : String) -> String = a + b
    ]
    print(strings[0]("first" + Int.toString(1), "second" + Int.toString(2)))
    let bytes : Array (fun(Byte) -> Byte) = [fun(b : Byte) -> Byte = b]
    let chars : Array (fun(Char) -> Char) = [fun(c : Char) -> Char = c]
    let bools : Array (fun(Bool) -> Bool) = [fun(b : Bool) -> Bool = !b]
    let units : Array (fun(Unit) -> Unit) = [fun(u : Unit) -> Unit = u]
    print(Byte.toInt(bytes[0](Byte.truncate(255))))
    print(chars[0]('λ'))
    print(bools[0](False))
    units[0](())
}
