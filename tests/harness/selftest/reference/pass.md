# Examples that pass

A program and its output, with a comment and a grammar fence around it, which
are prose:

<!-- a comment that is not a directive -->

```ebnf
greeting = "hello" ;
```

<!-- run -->
```kotlin
fun main() {
    print("hello")
}
```

```text
hello
```

Declarations that only have to compile:

<!-- check -->
```kotlin
fun double(n : Int) -> Int = n * 2
```

A rejection, quoting part of its message:

<!-- error: expected String, found Int -->
```kotlin
fun main() {
    let n : Int = "one"
}
```

A panic, with the output before it, and one without:

<!-- panic: index 3 -->
```kotlin
fun main() {
    print("before")
    let xs = [1, 2, 3]
    print(Int.toString(xs[3]))
}
```

```text
before
```

<!-- panic: no -->
```kotlin
fun main() {
    error("no")
}
```

A program of two modules:

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
