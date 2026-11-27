# Functions

`fun` declares a function. Write a short body after `=`, as `square` does, and
a longer one as a block, as `shipping` does.

A block evaluates to its last expression, so you don't write `return` at the
end of a function. `return` is for exiting early.

`if` is an expression too. In `shipping`, `base` is the value of the branch
that ran, and the second `if` is the function's result.

<!-- run -->
```kotlin
fun square(x) = x * x

fun shipping(weight, express) {
	let base = if weight > 10 { 15 } else { 5 }
	if express { base * 2 } else { base }
}

fun main() {
	print(square(12))
	print(shipping(3, False))
	print(shipping(12, True))
}
```

```text
144
5
30
```

You can declare top-level functions in any order: a function can call one that
is declared below it.

You can also declare a function inside a block. It is in scope for the
statements after it and for itself, so it can be recursive:

<!-- run -->
```kotlin
fun main() {
	fun factorial(n) = if n <= 1 { 1 } else { n * factorial(n - 1) }
	print(factorial(10))
}
```

```text
3628800
```

**Try this:** change `shipping(3, False)` to `shipping(3)`, and run the
program. The compiler reports that the function takes two arguments.

## In the reference

* [Functions](../ref/declarations.md#functions)
* [Local functions](../ref/declarations.md#local-functions)
* [`if`](../ref/statements.md#if)
