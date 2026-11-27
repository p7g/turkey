# Strings and method calls

String literals support interpolation. `"${n} items"` evaluates `n`, converts
it to a string with `show`, and inserts the result. You can interpolate any
value that you can `print`. A string is inserted as it is, without quotes.

To write a multi-line string, start it with `"""` at the end of a line and end
it with `"""` on a line of its own. The indentation of the closing `"""` is
stripped from every line, so you can indent the string with the code around
it.

You can call a function with method syntax. `x.f(args)` calls the function `f`
from the module that declares the type of `x`, and passes `x` as the first
argument. So `item.trim()` is the same call as `String.trim(item)`. Method
calls chain from left to right.

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

`print(fruits)` prints the strings in quotes. `print` writes the elements of a
collection as literals, so `["a,b"]` and `["a", "b"]` print differently.

Strings are UTF-8. You can't index a string or take its `len`, because the
length of a string could mean bytes, code points or visible characters. Call
the function that counts what you mean:

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
