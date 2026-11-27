# Loops

`for x in xs` runs its block once for each element of a collection. The
program iterates over an array, written `[...]`, and over a range of integers.
`Int.range(1, 11)` is the integers from 1 to 10: the end of a range is
exclusive.

`continue` skips to the next iteration, and `break` exits the loop.

`while` repeats its block for as long as its condition is true.

<!-- run -->
```kotlin
fun main() {
	let words = ["the", "cat", "saw", "the", "other", "cat"]
	for word in words {
		if word == "the" {
			continue
		}
		if word == "other" {
			break
		}
		print(word)
	}
	var sum = 0
	for n in Int.range(1, 11) {
		sum = sum + n
	}
	print(sum)
	var size = 1000
	var halvings = 0
	while size > 1 {
		size = size / 2
		halvings = halvings + 1
	}
	print(halvings)
}
```

```text
cat
saw
55
9
```

`loop` repeats until a `break`. It is the only loop with a value:
`break value` exits the loop, and `value` becomes the value of the `loop`
expression.

<!-- run -->
```kotlin
fun main() {
	var guess = 1
	let root = loop {
		if guess * guess >= 50 {
			break guess
		}
		guess = guess + 1
	}
	print(root)
}
```

```text
8
```

**Try this:** change `Int.range(1, 11)` to `Int.range(1, 101)`, and run the
program. The sum is now `5050`.

## In the reference

* [`for` over a collection](../ref/statements.md#for-over-a-collection)
* [`while`](../ref/statements.md#while)
* [`loop`](../ref/statements.md#loop)
* [`break` and `continue`](../ref/statements.md#break-and-continue)
