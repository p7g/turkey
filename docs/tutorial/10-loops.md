# Loops and collections

`for x in xs` runs once for each element of anything iterable: an array, a
`Vec`, a `Map`, a range of numbers, or the code points of a string, through
`String.codePoints(s)`. Its pattern can take each element apart, as
`(word, n)` does a map's entries.

`[]` is an empty array. An array has a fixed length. A `Vec` grows, and
nothing has to say which a literal is: `Vec.push(long, word)` needs a `Vec`,
so `long` is one. A `Map` is indexed by its keys, and `Map.get` returns an
`Option`, since a key may be missing.

`continue` goes on to the next element and `break` leaves the loop.

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

A map's entries come out in the order their keys were first added.
`Int.range(1, 11)` is the numbers from 1 up to 10: the end is left out.

`loop` runs until a `break`, and it is the one loop with a value, which
`break` gives it:

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
