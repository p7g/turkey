# Anonymous functions

Functions are values. You can store a function in a variable, pass it to
another function, and return it from one.

An anonymous function, or lambda, is a `fun` without a name. It has the same
two body forms as a declaration: an expression after `=`, as in
`fun(x) = x * 2`, or a block.

The program passes functions to `Array.map`, which calls a function on each
element of an array and returns the results, and to `Array.filter`, which
returns the elements for which a function returns `True`.

When a lambda uses its parameter once, you can shorten it to a placeholder
function. Write `_` for the parameter and leave out the `fun`: `_ + 1` means
`fun(p) = p + 1`, and `_ > 10` means `fun(p) = p > 10`.

<!-- run -->
```kotlin
fun main() {
	let double = fun(x) = x * 2
	print(double(21))
	let prices = [12, 40, 7, 55]
	print(Array.map(prices, double))
	print(Array.map(prices, fun(p) = p + 1))
	print(Array.filter(prices, fun(p) = p > 10))
	let discounted = Array.map(prices, fun(p) {
		let discount = if p > 30 { 10 } else { 0 }
		p - discount
	})
	print(discounted)
	print(Array.map(prices, _ + 1))
	print(Array.filter(prices, _ > 10))
}
```

```text
42
[24, 80, 14, 110]
[13, 41, 8, 56]
[12, 40, 55]
[12, 30, 7, 45]
[13, 41, 8, 56]
[12, 40, 55]
```

**Try this:** change `_ > 10` to `_ % 2 == 0`, and run the program. The last
line now prints the even prices.

## In the reference

* [Anonymous functions](../ref/declarations.md#anonymous-functions)
* [Placeholder functions](../ref/declarations.md#placeholder-functions)
