# Closures

A closure is a function that captures variables from the scope around it.
Every lambda is a closure.

A closure captures variables by reference, not by value. It sees later
assignments to them, and it can assign to them itself. A captured variable
lives as long as the closure does.

<!-- run -->
```kotlin
fun makeCounter() {
	var count = 0
	fun() {
		count = count + 1
		count
	}
}

fun main() {
	let next = makeCounter()
	let other = makeCounter()
	let _ = next()
	let _ = next()
	print(next())
	print(other())
	var greeting = "hello"
	let greet = fun(name) = "${greeting}, ${name}"
	greeting = "goodbye"
	print(greet("Ada"))
}
```

```text
3
1
goodbye, Ada
```

`makeCounter` returns a closure that increments `count`. Each call to the
closure continues from the previous one, because `count` outlives the call to
`makeCounter` that declared it. Every call to `makeCounter` declares a new
`count`, so `next` and `other` count separately.

`greet` captures `greeting` by reference, so it sees the assignment that
follows it.

`let _ = next()` calls `next` and discards the result.

## In the reference

* [Anonymous functions](../ref/declarations.md#anonymous-functions)
* [Discarded values](../ref/statements.md#discarded-values)
