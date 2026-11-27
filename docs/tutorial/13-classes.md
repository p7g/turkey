# Type classes

A class names operations that many types can have. Its body lists method
signatures, and an instance gives them for one type. A method is called like
any function, and the argument's type picks the instance.

An instance can depend on others: `Describe (Array a) : Describe a` declares
that an array can be described whenever its elements can, so one instance
covers arrays of anything describable.

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

Operators are class methods too: `a + b` is `add(a, b)`, from the class
`Add`, and `==` and `<` come from `Eq` and `Ord`. So `+` works on a type of
your own once it has an instance, and `show` decides how `print` writes it:

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

A function that uses a class on a type it doesn't know gets the class as a
constraint on its type, inferred or written in brackets:
`fun total[Add a](xs : Array a, zero : a) -> a`.

## In the reference

* [Classes](../ref/classes.md#classes)
* [Instances](../ref/classes.md#instances)
* [Using a class](../ref/classes.md#using-a-class)
* [Operators are class methods](../ref/expressions.md#operators-are-class-methods)
