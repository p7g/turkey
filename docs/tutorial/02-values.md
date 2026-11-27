# Values, `let` and `var`

`let` declares an immutable variable, and `var` declares a mutable one. You
can assign to a `var` variable again, but not to a `let` variable.

You don't have to write types, because the compiler infers them. The program
declares an `Int`, a `Float`, a `Char`, a `String` and a `Bool`.

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

Turkey has no implicit conversions. `total` is an `Int`, so the program
converts it with `Float.fromInt` before it multiplies it by a `Float`.

An integer literal such as `12` can be an `Int` or a `Float`, and the compiler
infers which from how you use the value. A literal with a decimal point, such
as `0.25`, is always a `Float`.

Assigning to a `let` variable is a compile error:

<!-- error: cannot assign to 'limit': it was bound with 'let' -->
```kotlin
fun main() {
	let limit = 10
	limit = 20
}
```

You can declare a variable again with the same name. The new variable shadows
the old one, and it can have a different type:

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

`Int.parse` returns an `Option Int`, because parsing can fail.
[Option, Either and `!`](08-option.md) explains `Option`.

## In the reference

* [Primitive types](../ref/types.md#primitive-types)
* [`let` and `var`](../ref/declarations.md#let-and-var)
* [Numeric literals](../ref/inference.md#numeric-literals)
