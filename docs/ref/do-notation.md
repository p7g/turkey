# The `?` operator and `do`

`e?` sequences a computation through a [monad](#monads). It takes the value
out of `e` and runs the rest of the block with it, and the monad's own `bind`
method decides what "the rest of the block" means. For `Option` and `Either`,
that means stopping at the first failure. For other monads it means other
things: `Array` runs the rest of the block once for each element. `?` is not
an early return, and nothing about it is specific to error handling.

## Syntax

```ebnf
question ::= postfix "?"
do-block ::= "do" block
```

`?` is a postfix operator, like a call or a field access, so it binds tighter
than any binary operator and can be chained: `parse(line)?.name`.

## What `e?` means

The value of `e` must have a type `m a` for some type constructor `m` with a
[`Monad`](builtins.md#monad) instance. The value of `e?` is an `a`: a value
"inside" `e`.

A `?` splits the block it appears in. Everything from the statement holding
the `?` to the end of the block becomes a function, and that function is
handed to `bind`. For a `let` statement:

```text
{
    before
    let x = e?
    rest
}
```

is approximately

```text
{
    before
    bind(e, fun($x) {
        let x = $x
        rest
    })
}
```

where `bind` is the method of the `Monad` class:

```text
class Monad m : Applicative m {
    fun bind(m a, fun(a) -> m b) -> m b
}
```

Each `?` adds one `bind`, nested inside the previous one. Statements before
the first `?` run as usual. The value of the whole block is the value `bind`
returns, which is why the last statement of a block containing `?` must itself
be a monadic value, such as `Some(x)`, `Right(x)` or `pure(x)`
([No implicit `pure`](#no-implicit-pure)).

`?` can appear anywhere inside an expression, not only on the right of a
`let`. In that case the operand of `?` is evaluated first, before any other
part of the statement that holds it, and the rest of the statement runs inside
the function passed to `bind`.

## Monads

> **For readers new to this.** A *monad* is a type constructor `m` with two
> operations: `pure(x)`, which wraps a plain value, and `bind(mx, f)`, which
> takes the value or values out of `mx`, passes each to `f`, and combines what
> `f` returns. Different monads combine differently, and that difference is
> the whole point: code written with `?` means something different, and
> useful, in each.

The following examples use four monads to show the range. The library defines
`Monad` instances for `Option`, `Either l` and `Array`; a program can define
its own for any suitable type.

### Stopping at the first failure: `Option`

`Option`'s `bind` calls the function only for `Some`. For `None`, it returns
`None` without running the rest of the block. So a chain of `?` stops at the
first `None`:

<!-- run -->
```kotlin
fun addStrings(a, b) {
    let x = Int.parse(a)?
    let y = Int.parse(b)?
    Some(x + y)
}

fun main() {
    print(addStrings("20", "22"))
    print(addStrings("20", "twenty-two"))
}
```

```text
Some(42)
None
```

### Stopping with a reason: `Either`

`Either`'s `bind` calls the function for `Right` and passes `Left` through
unchanged, so a `Left` carries the reason for the failure out of the block:

<!-- run -->
```kotlin
fun divide(a, b) = if b == 0 { Left("division by zero") } else { Right(a / b) }

fun ratio(a, b, c) {
    let first = divide(a, b)?
    let second = divide(first, c)?
    Right(second)
}

fun main() {
    print(ratio(100, 5, 2))
    print(ratio(100, 0, 2))
}
```

```text
Right(10)
Left(division by zero)
```

### Every combination: `Array`

`Array`'s `bind` calls the function once for **each** element and
concatenates the arrays it returns. So the rest of the block runs once per
element, and a block with two `?` runs once per pair:

<!-- run -->
```kotlin
fun outfits(shirts, trousers) {
    let shirt = shirts?
    let pair = trousers?
    [shirt + " with " + pair]
}

fun main() {
    for outfit in outfits(["red", "blue"], ["jeans", "shorts"]) {
        print(outfit)
    }
}
```

```text
red with jeans
red with shorts
blue with jeans
blue with shorts
```

### Accumulating: a monad of your own

A monad can carry something alongside the value. Here `Logged` pairs a value
with log lines, and its `bind` joins the log of each step:

<!-- run -->
```kotlin
type Logged a = Logged(a, Array String)

instance Functor Logged {
    fun map(Logged(value, log), f) = Logged(f(value), log)
}

instance Applicative Logged {
    fun pure(value) = Logged(value, [])
}

instance Monad Logged {
    fun bind(Logged(value, log), f) = match f(value) {
        Logged(result, more) -> Logged(result, log + more)
    }
}

fun note(line) = Logged((), [line])

fun area(width, height) {
    note("width is " + show(width))?
    note("height is " + show(height))?
    pure(width * height)
}

fun main() {
    match area(3, 4) {
        Logged(value, log) -> {
            for line in log {
                print(line)
            }
            print(value)
        }
    }
}
```

```text
width is 3
height is 4
12
```

`note(...)?` has type `Unit`, so it can stand alone as a statement.

## Do-contexts

A `?` belongs to the nearest enclosing **do-context**, and splits the block of
statements up to the end of that context. There are two kinds:

* **A function or lambda body** that contains a `?` is a do-context. The
  function's result is the value `bind` returns, so its return type is
  monadic.
* **An explicit `do { ... }` block.** It lets a `?` be used inside a larger
  function whose own result is not monadic.

<!-- run -->
```kotlin
fun main() {
    let total = do {
        let a = Int.parse("19")?
        let b = Int.parse("23")?
        Some(a + b)
    }
    print(total)
}
```

```text
Some(42)
```

`if`, `match`, loops and plain blocks are **transparent**: a `?` inside one of
them belongs to the enclosing do-context, and the construct takes part in the
sequencing. Lambdas are **opaque**: a `?` inside a lambda belongs to the
lambda, and leaves the enclosing function alone.

<!-- run -->
```kotlin
fun main() {
    let parsed = Array.map(["1", "two", "3"], fun(text) {
        let n = Int.parse(text)?
        Some(n * 10)
    })
    print(parsed)
}
```

```text
[Some(10), None, Some(30)]
```

A `do` block with no `?` in it is an ordinary block, and `do { }` is `()`.

## Control flow with `?`

A `?` may appear inside an `if`, a `match` or a loop, and `return`, `break`
and `continue` may appear after a `?`. They keep their usual meaning for
monads whose `bind` calls the function at most once, such as `Option` and
`Either`:

<!-- run -->
```kotlin
fun sumAll(texts) {
    var sum = 0
    for text in texts {
        let n = Int.parse(text)?
        if n < 0 {
            return None
        }
        sum = sum + n
    }
    Some(sum)
}

fun main() {
    print(sumAll(["1", "2", "3"]))
    print(sumAll(["1", "x", "3"]))
    print(sumAll(["1", "-2", "3"]))
}
```

```text
Some(6)
None
None
```

In general, though, `?` does not leave anything early. It hands the rest of
the block to `bind`, and what happens next is up to the monad. When `bind`
calls the function several times, as `Array`'s does, a `return` ends only the
call it happens in: it supplies that call's result, and the other calls go on.

<!-- run -->
```kotlin
fun scaled(xs) {
    let x = xs?
    if x == 2 {
        return [99]
    }
    [x * 10]
}

fun main() {
    print(scaled([1, 2, 3]))
}
```

```text
[10, 99, 30]
```

Variables captured by the rest of the block are shared, not copied, across
those calls ([Closures](declarations.md#anonymous-functions)).

## No implicit `pure`

The last statement of a do-context is its result, and it is not wrapped
automatically: write `Some(x)`, `Right(x)` or `pure(x)`, not `x`. This keeps
the meaning of an ordinary expression the same whether or not a `?` appears
earlier in the block. A plain value in that position is a type error:

<!-- error: found String -->
```kotlin
fun label(maybe) {
    let x = maybe?
    show(x)
}
```

> **Coming from Rust.** `?` generalizes Rust's `?` from `Option` and `Result`
> to any monad. On those two it behaves the same way, except that it never
> converts the error type: a `Left` passes through unchanged, so all the
> `Either` values in one block must share their left type.
>
> **Coming from Haskell.** A function body with `?` is a `do` block, and
> `let x = e?` is `x <- e`. `?` can also appear inside an expression, as in
> `f(g(x)?)`, which Haskell would write with a separate bind. `return` is not
> `pure`: it is the control-flow keyword, and `pure` is the method.
