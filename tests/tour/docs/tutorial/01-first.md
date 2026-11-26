# First `steps`

Links to [a heading](../ref/chapter.md#unwrapping-with-), to
[a repeated heading](../ref/chapter.md#notes-1), to [a chapter](../ref/chapter.md),
to [another lesson](02-second.md), to [a module](https://p7g.github.io/turkey/Data.Thing.html)
and [elsewhere](https://example.com/a?b=1&c=2).

<!-- run -->
```kotlin
fun main() {
	print("<first> & \"last\"")
}
```

```text
<first> & "last"
```

<!-- error: cannot assign -->
```kotlin
fun main() {
	let n = 1
	n = 2
}
```

<!-- panic: unwrapped None -->
```kotlin
fun main() {
	print("before")
	print(Int.parse("x")!)
}
```

```text
before
```

<!-- check -->
```kotlin
fun double(n) = n * 2
```

```text
plain text
```

## In the reference

* [Notes](../ref/chapter.md#notes)
