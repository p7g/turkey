# Closures and tail calls

A lambda can use the variables around it, and it captures them by reference,
not by copying. `makeCounter` returns a lambda that assigns its `count`, and
`count` lives on for as long as the lambda does, so each call sees the last
one's assignment. Two counters have a `count` each.

A call is in *tail position* when its result is the function's result, with
nothing left to do after it. Such a call takes the place of its caller's
frame, so recursion through tail calls runs in constant stack however deep
it goes, and that holds between functions that call each other too.
`sumTo` goes a hundred million calls deep.

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

`let _ = next()` calls `next` for what it does and drops the number it
returns.

A call whose result is still worked on isn't in tail position:
`n * factorial(n - 1)` has a multiplication left to do, so each call keeps a
frame until the one it made returns, and its depth is limited by the stack.

## In the reference

* [Anonymous functions](../ref/declarations.md#anonymous-functions)
* [Functions](../ref/declarations.md#functions)
* [Discarded values](../ref/statements.md#discarded-values)
