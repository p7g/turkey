# A panic whose trace is not what the chapter says

<!-- panic: no -->
```kotlin
fun main() {
    error("no")
}
```

<!-- stderr -->
```text
panic: no
  at main (Main.gob:9:9)
```
