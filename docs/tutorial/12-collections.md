# Arrays, `Vec` and `Map`

Turkey has three array types. An `Array` has a fixed length, a `Vec` is
growable, and a `FrozenArray` is immutable. You write all three with the same
literal, `[...]`, and the compiler infers which one you mean from how you use
the value. When nothing decides, the literal is an `Array`.

Index an array with `xs[i]`. You can assign to an element of an `Array` or a
`Vec` the same way.

A `Map` is a hash map. Index it with a key to read or assign an entry. Reading
a missing key panics, so when the key may be missing, call `Map.get`, which
returns an `Option`.

`for` iterates over all of these. A map yields its entries as `(key, value)`
tuples, in insertion order, and the loop variable can be a pattern that
destructures them.

<!-- run -->
```kotlin
fun main() {
	let scores = [3, 5, 8]
	scores[0] = 10
	print(scores)
	let words = ["the", "cat", "saw", "the", "other", "cat"]
	let long = []
	for word in words {
		if String.byteLength(word) > 3 {
			Vec.push(long, word)
		}
	}
	print(long)
	let counts = Map.new()
	for word in words {
		counts[word] = Map.getOr(counts, word, 0) + 1
	}
	for (word, n) in counts {
		print("${word}: ${n}")
	}
	print(counts["cat"])
	print(Map.get(counts, "dog"))
}
```

```text
[10, 5, 8]
["other"]
the: 2
cat: 2
saw: 1
other: 1
2
None
```

The program passes `long` to `Vec.push`, so `long` is a `Vec`. Nothing
constrains `scores` or `words`, so they are `Array`s.

## In the reference

* [`Array`](../ref/builtins.md#array)
* [`Vec`](../ref/builtins.md#vec)
* [Array literals](../ref/inference.md#array-literals)
* [Indexing](../ref/expressions.md#indexing)
