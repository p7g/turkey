# Hello, Turkey

Turkey is a statically typed language in the ML family. It has type inference,
algebraic data types with pattern matching, and type classes. The compiler is
written in Turkey, and on this page it runs in your browser.

Every program starts in the `main` function, which takes no arguments and
returns nothing. `print` writes a value and a newline to standard output.

<!-- run -->
```kotlin
fun main() {
	print("Hello, Turkey!")
}
```

```text
Hello, Turkey!
```

## How the page works

You can edit the program. The output under it was recorded when this page was
built.

* **Run** compiles the program and runs it. The compiler is about 6 MB. It
  downloads the first time you click in the editor or press Run, and your
  browser caches it for later visits.
* **Format** formats the program as `turkey format` does.
* **Reset** restores the lesson's program. Until you press it, your browser
  keeps your edits to each lesson.
* **Share** copies a link that opens the program in the playground.

Change the greeting, or print an expression such as `print(6 * 7)`, and run
the program again.

## In the reference

* [The entry point](../ref/modules.md#the-entry-point)
* [`Show` and printing](../ref/builtins.md#show-and-printing)
