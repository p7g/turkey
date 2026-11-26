# Strings and method calls

A string literal takes holes: `"${n} items"` puts `show(n)` where the hole
is. Any value with a `Show` instance can go in one, and a string goes in as
it is, without quotes.

`x.f(args)` calls the function `f` of the module that declares `x`'s type,
with `x` first. `item.trim()` is `String.trim(item)`, and calls chain left to
right.

A string that starts with `"""` at the end of a line runs to a `"""` on a
line of its own, and the indentation before that closing line is not part of
the text.

<!-- run -->
```kotlin
fun main() {
	let item = "  Turkey sandwich  "
	let price = 7
	print("${item.trim()} costs ${price} dollars")
	let fruits = "apples, pears, figs".split(", ")
	print(fruits)
	print("${len(fruits)} fruits, and the first is ${fruits[0]}")
	print("""
		Order:
		  ${String.join(fruits, " + ")}
		""")
}
```

```text
Turkey sandwich costs 7 dollars
["apples", "pears", "figs"]
3 fruits, and the first is apples
Order:
  apples + pears + figs
```

`print(fruits)` shows the strings in quotes. A collection shows each element
as Turkey would write it, so `["a,b"]` and `["a", "b"]` can't print alike.

A string is UTF-8, and it has no `len` and no index: "length" could mean
bytes, code points or what a reader sees, so each has its own function.

<!-- run -->
```kotlin
fun main() {
	let word = "crème"
	print(String.byteLength(word))
	print(String.codePointCount(word))
	print(String.padStart("42", 5, '0'))
}
```

```text
6
5
00042
```

## In the reference

* [String interpolation](../ref/expressions.md#string-interpolation)
* [Method calls](../ref/expressions.md#method-calls)
* [Multi-line strings](../ref/lexical.md#multi-line-strings)
* [String](../ref/types.md#string)
