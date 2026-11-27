# Type classes

A type class is an interface: a set of method signatures. A type implements a
class with an `instance` declaration, which defines the methods for that type.

Call a method like any other function. The compiler selects the instance from
the type of the argument, so `describe(Dog { name = "Rex" })` calls the
`describe` in `instance Describe Dog`.

An instance can be generic, with constraints on its type parameters.
`instance Describe (Array a) : Describe a` implements `Describe` for an array
of any type `a` that implements `Describe`. That one instance covers arrays of
dogs, arrays of `Bool`s, and arrays of arrays.

<!-- run -->
```kotlin
class Describe a {
	fun describe(a) -> String
}

type Dog = Dog { name : String }

instance Describe Dog {
	fun describe(dog) = "${dog.name} the dog"
}

instance Describe Bool {
	fun describe(b) = if b { "yes" } else { "no" }
}

instance Describe (Array a) : Describe a {
	fun describe(xs) = "[" + String.join(Array.map(xs, describe), ", ") + "]"
}

fun main() {
	print(describe(Dog { name = "Rex" }))
	print(describe([True, False, True]))
	print(describe([[Dog { name = "Rex" }], [Dog { name = "Fido" }]]))
}
```

```text
Rex the dog
[yes, no, yes]
[[Rex the dog], [Fido the dog]]
```

Operators are class methods. `a + b` calls `add(a, b)` from the class `Add`,
and `==` and `<` come from `Eq` and `Ord`. To overload an operator for your
own type, implement its class. Implement `Show` to control how `print` writes
a value:

<!-- run -->
```kotlin
type Money = Money(Int)

instance Add Money {
	fun add(Money(a), Money(b)) = Money(a + b)
}

instance Show Money {
	fun show(Money(cents)) =
		"$" + show(cents / 100) + "." + String.padStart(show(cents % 100), 2, '0')
}

fun main() {
	print(Money(1250) + Money(199) + Money(51))
}
```

```text
$15.00
```

**Try this:** add `print(describe(42))` to `main`, and run the program. The
compiler reports that there is no instance of `Describe` for `Int`.

## In the reference

* [Classes](../ref/classes.md#classes)
* [Instances](../ref/classes.md#instances)
* [Using a class](../ref/classes.md#using-a-class)
*
  [Operators are class methods](../ref/expressions.md#operators-are-class-methods)
