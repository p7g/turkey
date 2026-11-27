# Strings

A string literal is written in double quotes, and `+` concatenates two
strings.

String literals support interpolation. `"${price} dollars"` evaluates `price`,
converts it to a string, and inserts the result. You can interpolate any
expression whose value you can `print`. A string is inserted as it is, without
quotes.

To write a multi-line string, start it with `"""` at the end of a line and end
it with `"""` on a line of its own. The indentation of the closing `"""` is
stripped from every line, so you can indent the string with the code around
it.

<!-- run -->
```kotlin
fun main() {
	let item = "notebook"
	let price = 7
	print(item + "s")
	print("One ${item} costs ${price} dollars")
	print("Two cost ${price * 2}")
	let fruits = ["apples", "pears", "figs"]
	print(fruits)
	print("${len(fruits)} fruits, and the first is ${fruits[0]}")
	print("""
	    Order:
	      ${String.join(fruits, " + ")}
	    """)
}
```

```text
notebooks
One notebook costs 7 dollars
Two cost 14
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
}
```

```text
6
5
```

**Try this:** add `print(len(item))` to `main`, and run the program. The
compiler rejects it, because a string has no `len`.

## In the reference

* [String interpolation](../ref/expressions.md#string-interpolation)
* [Multi-line strings](../ref/lexical.md#multi-line-strings)
* [String](../ref/types.md#string)
