# Hello, Turkey

Turkey is a statically typed language in the ML family. Its types are
inferred, its data is built from records and variants, and type classes give
operators and loops their meaning. Its compiler is written in Turkey, and on
this page it runs in your browser.

A program starts at `main`, a function that takes nothing and returns
nothing. `print` writes a value and a line break.

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

The program is in an editor, and the output under it is what the program
printed when this page was made.

* **Run** compiles the program and runs it. The first Run downloads the
  compiler, about 6 MB, and later visits use the browser's cache.
* **Format** lays the program out the way `turkey format` does.
* **Reset** brings back the lesson's program. Until then, this browser keeps
  your edits to each lesson.
* **Share** copies a link that opens the program in the playground.

Change the greeting, or print a sum such as `print(6 * 7)`, and run it again.

## In the reference

* [The entry point](../ref/modules.md#the-entry-point)
* [`Show` and printing](../ref/builtins.md#show-and-printing)
