# Closures and tail calls

A closure is a function that captures variables from the scope around it.
Every lambda is a closure. A closure captures variables by reference, not by
value, so it sees assignments to them and can assign to them itself.

`makeCounter` returns a closure that increments `count`. `count` lives as long
as the closure does, so each call continues from the previous one. Every call
to `makeCounter` creates a new `count`, so `next` and `other` count
separately.

Turkey eliminates tail calls. A call is in tail position when its result is
the function's result, with nothing left to do after it. A tail call reuses
the caller's stack frame, so a function that recurses through tail calls runs
in constant stack space, as a loop does. This holds for mutually recursive
functions too, such as `isEven` and `isOdd`. `sumTo` recurses a hundred
million times without overflowing the stack.

<!-- run -->
```kotlin
fun makeCounter() {
	var count = 0
	fun() {
		count = count + 1
		count
	}
}

fun sumTo(n, total) = if n == 0 { total } else { sumTo(n - 1, total + n) }

fun isEven(n) = if n == 0 { True } else { isOdd(n - 1) }
fun isOdd(n) = if n == 0 { False } else { isEven(n - 1) }

fun main() {
	let next = makeCounter()
	let other = makeCounter()
	let _ = next()
	let _ = next()
	print(next())
	print(other())
	print(sumTo(100000000, 0))
	print(isEven(1000001))
}
```

```text
3
1
5000000050000000
False
```

`let _ = next()` calls `next` and discards the result.

A call isn't in tail position when the function still uses its result.
`n * factorial(n - 1)` multiplies after the call returns, so every call needs
its own stack frame, and the depth of the recursion is limited by the size of
the stack.

## In the reference

* [Anonymous functions](../ref/declarations.md#anonymous-functions)
* [Functions](../ref/declarations.md#functions)
* [Discarded values](../ref/statements.md#discarded-values)
