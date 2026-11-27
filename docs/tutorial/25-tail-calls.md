# Tail calls

Turkey eliminates tail calls, so you can write a loop as a recursive function
without growing the stack.

A call is in tail position when its result is the function's result, with
nothing left to do after it. A tail call reuses the caller's stack frame. So a
function that recurses through tail calls runs in constant stack space,
however deep the recursion goes.

<!-- run -->
```kotlin
fun sumTo(n, total) = if n == 0 { total } else { sumTo(n - 1, total + n) }

fun isEven(n) = if n == 0 { True } else { isOdd(n - 1) }
fun isOdd(n) = if n == 0 { False } else { isEven(n - 1) }

fun main() {
	print(sumTo(100000000, 0))
	print(isEven(1000001))
}
```

```text
5000000050000000
False
```

`sumTo` recurses a hundred million times. It carries its running total in a
parameter, an accumulator, so that the recursive call is the last thing it
does. `isEven` and `isOdd` are mutually recursive, and their calls are tail
calls too.

A call isn't in tail position when the function still uses its result. In
`n * factorial(n - 1)`, the multiplication happens after the call returns.
Every call then needs its own stack frame, and the depth of the recursion is
limited by the size of the stack.

**Try this:** change `isEven(1000001)` to `isEven(100000000)`, and run the
program. A hundred million calls between `isEven` and `isOdd` still run in
constant stack space.

## In the reference

* [Functions](../ref/declarations.md#functions)
