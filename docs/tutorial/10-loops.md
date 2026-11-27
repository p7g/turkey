# Loops and collections

`for x in xs` iterates over a collection. It works on arrays, `Vec`s, `Map`s,
ranges of integers and every other iterable type.

The loop variable can be a pattern. `for (word, n) in counts` destructures
each entry of the map into its key and its value.

`continue` skips to the next iteration, and `break` exits the loop.

Turkey has three array types. An `Array` has a fixed length, a `Vec` is
growable, and a `FrozenArray` is immutable. You write all three with the same
literal, `[...]`, and the compiler infers which one you mean from how you use
the value. The program passes `long` to `Vec.push`, so `long` is a `Vec`.
Nothing constrains `words`, so it defaults to an `Array`.

A `Map` is a hash map. Index it with a key to read or assign an entry, as in
`counts[word] = ...`. `Map.get` returns an `Option`, because the key may be
missing. Iterating over a map yields its entries in insertion order.

<!-- run -->
```kotlin
fun main() {
	let words = ["the", "cat", "saw", "the", "other", "cat"]
	let counts = Map.new()
	for word in words {
		counts[word] = Map.getOr(counts, word, 0) + 1
	}
	for (word, n) in counts {
		print("${word}: ${n}")
	}
	let long = []
	for word in words {
		if String.byteLength(word) < 4 {
			continue
		}
		Vec.push(long, word)
	}
	print(long)
	for (i, word) in Iter.enumerate(words) {
		if word == "saw" {
			print("'saw' is word ${i}")
			break
		}
	}
	print(Iter.sum(Int.range(1, 11)))
}
```

```text
the: 2
cat: 2
saw: 1
other: 1
["other"]
'saw' is word 2
55
```

`Int.range(1, 11)` is the integers from 1 to 10: the end of a range is
exclusive.

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

## In the reference

* [`for` over a collection](../ref/statements.md#for-over-a-collection)
* [`loop`](../ref/statements.md#loop)
* [`Array`](../ref/builtins.md#array)
* [`Vec`](../ref/builtins.md#vec)
* [Array literals](../ref/inference.md#array-literals)
