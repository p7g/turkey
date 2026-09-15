module Main

fun nine(a : Int, b : Int, c : Int, d : Int, e : Int, f : Int, g : Int,
         h : Int, i : Int) -> Int =
    if a <= 0 { b + c + d + e + f + g + h + i }
    else { nine(a - 1, b, c, d, e, f, g, h, i) }

fun main() -> Unit {
    print(Int.toString(nine(3, 2, 3, 4, 5, 6, 7, 8, 9)))
}
