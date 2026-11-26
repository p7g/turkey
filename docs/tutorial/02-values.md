# Values, `let` and `var`

`let` gives a value a name. The program names an `Int`, a `Float`, a `Char`,
a `String` and a `Bool`, and nothing says which is which: each literal has a
type, and so does every name bound to it.

`var` makes a name that can be assigned again. A `let` name can't be.

<!-- run -->
```kotlin
fun main() {
	let price = 12
	let rate = 0.25
	let grade = 'A'
	let item = "notebook"
	var total = price * 3
	total = total + 4
	let discounted = Float.fromInt(total) * (1.0 - rate)
	let cheap = discounted < 50.0
	print(item)
	print(total)
	print(discounted)
	print(grade)
	print(cheap)
}
```

```text
notebook
40
30.0
A
True
```

Nothing converts between number types by itself. `total` is an `Int`, so it
becomes a `Float` through `Float.fromInt` before it is multiplied by a
`Float`.

Assigning a name bound with `let` is an error:

<!-- error: cannot assign to 'limit': it was bound with 'let' -->
```kotlin
fun main() {
	let limit = 10
	limit = 20
}
```

A later `let` can reuse a name, which hides the earlier one. That is how a
value changes type as a program works on it:

<!-- run -->
```kotlin
fun main() {
	let input = "42"
	let input = Int.parse(input)
	print(input)
}
```

```text
Some(42)
```

`Int.parse` can fail, so it returns an `Option Int`, which
[Option, Either and `!`](08-option.md) covers.

## In the reference

* [Primitive types](../ref/types.md#primitive-types)
* [`let` and `var`](../ref/declarations.md#let-and-var)
* [Numeric literals](../ref/inference.md#numeric-literals)
