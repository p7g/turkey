module Even (even)
import Odd (odd)

fun even(n : Int) -> Bool = if n == 0 { True } else { odd(n - 1) }
