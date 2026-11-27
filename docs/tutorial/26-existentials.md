# Hiding a type

An existential type hides a type parameter. Values of different types can then
share one type, as long as they implement the same classes. Use one to build a
heterogeneous collection.

`type Shown = Shown[Show a](a)` declares a constructor that accepts a value of
any type `a` that implements `Show`. The brackets hold a constraint, as a
function's do. The type `Shown` has no parameter, so `a` is hidden:
`Shown(42)` and `Shown("text")` have the same type, and you can put them in
one array.

To use the value, match on the constructor, in a parameter pattern or a
`match` arm. You know only that the hidden type implements the classes in the
brackets. So `cell` can show `value`, and can do nothing else with it.

<!-- run -->
```kotlin
type Shown = Shown[Show a](a)

fun cell(Shown(value)) = "[${value}]"

fun main() {
	let row = [Shown(42), Shown("text"), Shown(Some(1.5)), Shown([True])]
	for shown in row {
		print(cell(shown))
	}
}
```

```text
[42]
[text]
[Some(1.5)]
[[True]]
```

A record constructor can hide a type too, and its fields can be functions that
take the hidden type. That gives you an abstract data type: state packaged
with the operations on it. Code that uses a `Counter` can't depend on the type
of its state, which is an `Int` in `ticks` and a `String` in `marks`.

<!-- run -->
```kotlin
type Counter = Counter[s] {
	start  : s
	step   : fun(s) -> s
	report : fun(s) -> String
}

fun runCounter(Counter { start, step, report }, times) {
	var state = start
	for _ in Int.range(0, times) {
		state = step(state)
	}
	report(state)
}

fun main() {
	let ticks = Counter { start = 0, step = _ + 1, report = show }
	let marks = Counter { start = "", step = _ + "|", report = fun(s) = s }
	print(runCounter(ticks, 3))
	print(runCounter(marks, 3))
}
```

```text
3
|||
```

The hidden type can't escape the function or `match` arm that unpacks it. You
can't return the value inside a `Shown`, for example:

<!-- error: the type 'a' hidden by 'Shown' cannot leave the 'match' arm or function that opened it -->
```kotlin
type Shown = Shown[Show a](a)

fun unwrap(Shown(value)) = value
```

## In the reference

* [Existential types](../ref/types.md#existential-types)
* [Existential patterns](../ref/patterns.md#existential-patterns)
