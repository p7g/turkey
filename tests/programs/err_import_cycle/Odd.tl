module Odd (odd) where
import Even (even)

fun odd(n : Int) -> Bool = if n == 0 { False } else { even(n - 1) }
