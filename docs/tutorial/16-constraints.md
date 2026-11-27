# Constraints

A generic function works for every type, so it can use only the operations
that every type has. A constraint lets it use more: it restricts a type
parameter to the types that implement a class.

In a type, a lowercase name such as `a` is a type parameter. Write the
constraints on a function's type parameters in brackets after its name.
`fun largest[Ord a](first : a, rest : Array a) -> a` works for any type `a`
that implements `Ord`. Inside `largest`, you can compare two values of type
`a` with `>`, because `>` is a method of `Ord`.

To require several classes, separate the constraints with commas, as in
`[Ord a, Show a]`.

<!-- run -->
```kotlin
fun largest[Ord a](first : a, rest : Array a) -> a {
	var best = first
	for x in rest {
		if x > best {
			best = x
		}
	}
	best
}

fun listed[Show a](items : Array a) -> String {
	var out = ""
	for item in items {
		out = out + "<${item}>"
	}
	out
}

fun smallest(first, rest) {
	var best = first
	for x in rest {
		if x < best {
			best = x
		}
	}
	best
}

fun main() {
	print(largest(3, [9, 4]))
	print(largest("pear", ["apple", "fig"]))
	print(smallest(3.5, [9.0, 0.25]))
	print(listed([True, False]))
}
```

```text
9
pear
0.25
<True><False>
```

Each call needs an instance that satisfies the constraint.
`largest(3, [9, 4])` uses the instance of `Ord` for `Int`, and
`largest("pear", ["apple", "fig"])` uses the one for `String`.

You don't have to write constraints. When a function has no annotations, the
compiler infers them: `smallest` gets the same constraint as `largest`.

An instance's constraints follow a colon instead, as in
`instance Describe (Array a) : Describe a` from [Type classes](15-classes.md).

**Try this:** delete `[Ord a]` from `largest`, and run the program. The
compiler rejects `x > best`, because nothing says that `a` implements `Ord`.

## In the reference

* [Contexts](../ref/declarations.md#contexts)
* [Using a class](../ref/classes.md#using-a-class)
* [Signatures](../ref/declarations.md#signatures)
