# Tasks

A task is a function that runs concurrently with the code that started it.
Tasks come from the library, `System.Task`, not from the language.

`spawn(f)` starts `f` as a task, and `join(task)` waits for its result. A
task only starts inside `runScope`, which waits for every task started in it
before it returns, so no task outlives the scope that started it. `spawn` is
sequenced with `?`, in a `do` block that `runScope` runs.

`System.Parallel.map` spreads a function over an array's elements, and
returns the results in order. Tasks that change shared data need to take
turns: `System.Sync.mutex` guards a value, and `Sync.update` changes it while
holding the lock.

<!-- run -->
```kotlin
import System.Parallel as Parallel
import System.Sync as Sync
import System.Task (join, runScope, spawn)

fun sumOfSquares(from, to) {
	var sum = 0
	for n in Int.range(from, to) {
		sum = sum + n * n
	}
	sum
}

fun main() {
	let (low, high) = runScope(do {
		let low = spawn(fun() = sumOfSquares(0, 500))?
		let high = spawn(fun() = sumOfSquares(500, 1000))?
		pure((low, high))
	})
	print(join(low) + join(high))
	print(Parallel.map([1, 2, 3, 4], _ * 10))
	let seen = Sync.mutex(0)
	runScope(do {
		for word in ["one", "two", "three"] {
			let _ = spawn(fun() = Sync.update(seen, _ + String.byteLength(word)))?
		}
		pure(())
	})
	print(Sync.withLock(seen, fun(n) = n))
}
```

```text
332833500
[10, 20, 30, 40]
11
```

Scheduling is cooperative: a task runs until it waits, in `join` or on a
lock, or finishes. In the browser every task runs on one thread, so they
take turns. A program built with `turkey build` runs them on a worker per
core.

## In the library

* [`System.Task`](https://p7g.github.io/turkey/System.Task.html)
* [`System.Parallel`](https://p7g.github.io/turkey/System.Parallel.html)
* [`System.Sync`](https://p7g.github.io/turkey/System.Sync.html)
