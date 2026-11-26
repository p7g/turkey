# Second

A program of two files.

<!-- module: Greeting.gob -->
```kotlin
export (greeting)

fun greeting() -> String = "hi"
```

<!-- run -->
```kotlin
import Greeting (greeting)

fun main() {
	print(greeting())
}
```

```text
hi
```
