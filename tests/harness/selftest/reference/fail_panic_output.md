# A panic whose output before it is not what the chapter says

<!-- panic: no -->
```kotlin
fun main() {
    print("before")
    error("no")
}
```

```text
after
```
