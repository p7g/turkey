# Built-in types and classes

Several pieces of syntax are defined in terms of library types and classes:
`if` needs a `Bool`, `+` calls `Add`, a `for` loop calls `Iterator`, `a[i]`
calls `Index`, `a[i] = v` calls `SetIndex`, and `?` calls `Monad`. This
chapter describes those types and classes, and the Prelude that makes them
available. It does not document the rest of the library.

## The Prelude

Every module implicitly imports the module `Prelude`. A module that imports
`Prelude` explicitly gets that import instead of the implicit one, so
`import Prelude (print)` brings in only `print`.

The Prelude provides these names unqualified:

| Kind | Names |
|---|---|
| Types and their constructors | `Int`, `Float`, `Byte`, `Char`, `Unit`, `Bool`, `Option`, `Either`, `Ordering`, `SomeError`, and the types `String`, `Array`, `Vec` and `Map` without their constructors |
| Classes and their methods | `Eq`, `Ord`, `Add`, `Sub`, `Mul`, `Div`, `Rem`, `Neg`, `Show`, `Iterator`, `Index`, `SetIndex`, `Length`, `Functor`, `Applicative`, `Monad`, `Semigroup`, `Monoid`, `Foldable`, `Hash`, `Error`, `Typed` |
| Functions | `print`, `write`, `error`, `fail`, `const` |

It also provides the library modules `Array`, `Bool`, `Byte`, `Char`,
`Either`, `Error`, `Float`, `Int`, `Map`, `Option`, `Ordering`, `String` and
`Vec` under those names, qualified only. That is why `Vec.push` and
`Int.parse` need no import, while the bare names `push` and `parse` stay free
for a program to use.

A type is in scope only through an import, the implicit Prelude import
included. A module that replaces the Prelude import imports the types it
names, as it does anything else:

<!-- error: unknown type 'Int' -->
```kotlin
import Prelude (print)

fun double(n : Int) -> Int = n * 2

fun main() {
    print(double(21))
}
```

`import Prelude (print, Int)` fixes it. The unit type can always be written
`()`, which is syntax rather than a name ([Unit](types.md#unit)).

## `Bool`

```text
type Bool = False | True
```

An ordinary data type. `if`, `while`, the test of a C-style `for`, `&&`,
`||` and `!` all require this type. `==` and the other comparisons return it.

## `Option`

```text
type Option a = None | Some(a)
```

A value that may be absent. The `Iterator` class's `next` returns an
`Option` to say whether there is another element, and `Option` is a `Monad`
whose `?` stops at the first `None` ([`?`](do-notation.md)).

## `Either`

```text
type Either l r = Left(l) | Right(r)
```

A value that is one of two things, conventionally an error (`Left`) or a
result (`Right`). `Either l` is a `Monad` whose `?` passes a `Left` through
and continues with the value in a `Right`.

When both sides are `Error`s, so is `Either l r`, and its `message` is the
message of the side it holds. That is how one error type can say "this or
that failed" without a type of its own:

<!-- run -->
```kotlin
type Unreadable = Unreadable
type Empty = Empty

instance Error Unreadable { fun message(e) = "the file could not be read" }
instance Error Empty { fun message(e) = "the file is empty" }

fun load(text : Option String) -> Either (Either Unreadable Empty) String = match text {
    None -> Left(Left(Unreadable))
    Some("") -> Left(Right(Empty))
    Some(s) -> Right(s)
}

fun main() {
    for text in [Some("hello"), Some(""), None] {
        match load(text) {
            Right(s) -> print(s)
            Left(e) -> print(message(e))
        }
    }
}
```

```text
hello
the file is empty
the file could not be read
```

## `Array`

`Array a` is a fixed-length, mutable sequence of `a`, with
[reference semantics](types.md#mutability-and-sharing). Its length is set when
it is made and never changes, but its elements can be assigned. Its
constructor is hidden: arrays are made with
[array literals](expressions.md#array-literals) and with library functions
such as `Array.filled` and `Array.init`. `Array.init(n, f)` is an array of
length `n` whose element `i` is `f(i)`, computed in order from `0`:

<!-- run -->
```kotlin
fun main() {
    print(Array.init(5, fun(i) = i * i))
}
```

```text
[0, 1, 4, 9, 16]
```

Arrays are indexed from zero by `Int` ([Indexing](expressions.md#indexing)),
their length is `len(xs)`, `for x in xs` visits their elements in order, and
`+` concatenates two arrays into a new one. Reading or writing outside
`0 .. len(xs) - 1` panics. `Array` is a `Monad`, whose `?` runs the rest of the
block once per element.

Assigning an element changes the array in place; nothing changes its length:

<!-- run -->
```kotlin
fun main() {
    let counts = Array.filled(3, 0)
    for word in ["a", "bb", "a", "ccc"] {
        let n = String.byteLength(word) - 1
        counts[n] = counts[n] + 1
    }
    print(counts)
}
```

```text
[2, 1, 1]
```

So the module `Array` has no `push`, `pop` or `clear`. A sequence that grows
is a [`Vec`](#vec):

<!-- error: 'Array.push' is not defined -->
```kotlin
fun main() {
    let xs = [1, 2]
    Array.push(xs, 3)
}
```

> **Coming from Rust:** `Array a` is not `[T; N]`: its length is not part of
> its type, and two arrays of different lengths have the same type. It is
> closest to a `Box<[T]>`, a boxed slice, shared by reference. `Vec` is
> Rust's `Vec`.

> **Coming from Haskell:** an `Array` is mutable. Assigning `xs[i] = v`
> changes the array every other reference to it sees; it is not a new array
> with one element replaced. It is closest to an `IOArray Int a` indexed from
> zero.

## `Vec`

`Vec a` is a growable, mutable sequence of `a`: its length changes as
`Vec.push` adds an element at the end, `Vec.pop` removes the last one, and `Vec.clear` removes them all. It
has the same instances as `Array` (indexing, `len`, `for`, `+`, `Show`,
`Monad` and the rest), and the module `Vec` has the same functions as `Array`,
such as `Vec.map`, `Vec.sort` and `Vec.slice`. Its constructor is hidden too: a
`Vec` is made with an [array literal](expressions.md#array-literals) that is
used as one, with `Vec.new` or `Vec.filled`, or by copying an array with
`Vec.fromArray`. `Vec.toArray` copies it back.

<!-- run -->
```kotlin
fun main() {
    let stack = []
    Vec.push(stack, "a")
    Vec.push(stack, "b")
    print(Vec.pop(stack))
    print(stack)
}
```

```text
Some(b)
[a]
```

Nothing has to say that `stack` is a `Vec`: `Vec.push` takes one, so the
literal it was made from is one ([Array literals](inference.md#array-literals)).

`Map k v`, a hash map, also has `Index`, `Length` and `Iterator` instances,
and iterates over `(key, value)` pairs in insertion order.

## Operator classes

Each arithmetic and comparison operator calls a class method
([Operators](expressions.md#operators-are-class-methods)):

```text
class Add a { fun add(a, a) -> a }
class Sub a { fun sub(a, a) -> a }
class Mul a { fun mul(a, a) -> a }
class Div a { fun div(a, a) -> a }
class Rem a { fun rem(a, a) -> a }
class Neg a { fun neg(a) -> a }

class Eq a {
    fun eq(a, a) -> Bool
    fun ne(x : a, y : a) -> Bool = !eq(x, y)
}

class Ord a : Eq a {
    fun lt(a, a) -> Bool
    fun lte(x : a, y : a) -> Bool = lt(x, y) || eq(x, y)
    fun gt(x : a, y : a) -> Bool = lt(y, x)
    fun gte(x : a, y : a) -> Bool = !lt(x, y)
}
```

The instances the library provides for built-in types:

| Type | `Eq` | `Ord` | `Add` | `Sub` `Mul` `Div` `Neg` | `Rem` | `Show` |
|---|---|---|---|---|---|---|
| `Int` | yes | yes | yes | yes | yes | yes |
| `Float` | yes | yes | yes | yes | no | yes |
| `Byte`, `Char`, `Bool` | yes | yes | no | no | no | yes |
| `String` | yes | yes | concatenation | no | no | yes |
| tuples of 2 to 4 elements | if the elements have it | if the elements have it | no | no | no | if the elements have it |
| `Option a`, `Either l r` | if the contents have it | no | no | no | no | if the contents have it |
| `Array a`, `Vec a` | no | no | concatenation | no | no | if the elements have it |
| `Unit` | no | no | no | no | no | no |

Tuples compare lexicographically, and strings compare byte by byte.

## `Show` and printing

```text
class Show a { fun show(a) -> String }

fun print[Show a](x : a) -> Unit
fun write[Show a](x : a) -> Unit
```

`show` converts a value to text. `print` writes `show(x)` and a line break to
standard output; `write` does the same without the line break. `show` of a
`String` is the string itself, without quotes. A type gets a `Show` instance
only if someone writes one; there is no automatic derivation.

### When standard output is written

Standard output is buffered: `print` and `write` add their text to one buffer
that the whole program shares, and the buffer is written to the operating
system later. Whether standard output is a terminal is checked once, when the
program starts. The buffer is written out:

* after every line, when standard output is a terminal;
* otherwise, when it fills (it holds 64 KiB), up to its last complete line.
  A single line longer than the buffer is written as it stands;
* before each write to standard error through `System.IO.stderr()`, so that
  the two streams come out in the order the program wrote them;
* before each read of standard input through `System.IO.stdin()`, when
  standard input is a terminal, so that a prompt appears before the program
  waits for an answer;
* when the program ends: when `main` returns, when `System.Env.exit` is called,
  and when the program panics, before the panic's message;
* when the program calls `System.IO.flush(System.IO.stdout())`.

A line printed by `print` is added whole, so lines printed by tasks running at
the same time never mix within a line. Output still in the buffer is lost only
if the process is killed by a signal.

`print` and `write` do not report a failed write, so a program whose standard
output is a closed pipe need not test every line it prints. `flush` reports
the first failure since the last `flush`.

<!-- run -->
```kotlin
import System.IO as IO

fun main() {
    write("working... ")
    -- Without this, a program whose output goes to a pipe would show nothing
    -- until the buffer filled or the program ended.
    match IO.flush(IO.stdout()) {
        Left(e) -> {
            let _ = IO.writeText(IO.stderr(), message(e) + "\n")
        }
        Right(_) -> {}
    }
    print("done")
}
```

```text
working... done
```

> **Coming from Rust:** Rust's `println!` writes every line as it is printed,
> even into a pipe. Turkey's output to a file or a pipe is written a buffer at
> a time, as C's `printf` does, and a program that talks to another over a
> pipe flushes before it waits for the answer.
>
> **Coming from C:** writing to standard error writes out standard output
> first, which C's `stdio` does not do.

## `Iterator`

```text
class Iterator c {
    type Item c
    type Cursor c

    fun iter(c) -> Cursor c
    fun next(c, Cursor c) -> Option (Item c)
}
```

`for x in c` runs over any type with an `Iterator` instance
([`for` over a collection](statements.md#for-over-a-collection)). `Item c` is
the element type, and `Cursor c` is the type of the state that records how far
the loop has got. `iter` makes a new cursor, and `next` returns the next
element and advances the cursor, or returns `None` when there are no more. The
cursor is usually a mutable record, and nothing asks the collection for its
length, so a type can be iterated even when it cannot be indexed, such as a
linked list or a generated sequence:

<!-- run -->
```kotlin
type Countdown = Countdown { from : Int }
type CountdownCursor = CountdownCursor { var current : Int }

instance Iterator Countdown {
    type Item = Int
    type Cursor = CountdownCursor

    fun iter(c) = CountdownCursor { current = c.from }

    fun next(c, cursor) {
        if cursor.current == 0 {
            return None
        }
        let value = cursor.current
        cursor.current = value - 1
        Some(value)
    }
}

fun main() {
    for n in (Countdown { from = 3 }) {
        print(n)
    }
}
```

```text
3
2
1
```

## `Index`

```text
class Index c {
    type Key c
    type Value c

    fun get(c, Key c) -> Value c
}

class SetIndex c : Index c { fun set(c, Key c, Value c) -> Unit }
```

`c[k]` calls `get(c, k)`, and the assignment `c[k] = v` calls `set(c, k, v)`
([Indexing](expressions.md#indexing)). `Key c` and `Value c` are the key and
element types: `Int` and `a` for `Array a` and `Vec a`, and `k` and `v` for
`Map k v`. All three have both instances.

Writing is a class of its own so that a type can be read by key without being
writable. A type with only an `Index` instance can be read with `c[k]`, and
`c[k] = v` on it is rejected:

<!-- error: no instance for 'SetIndex Squares' -->
```kotlin
type Squares = Squares

instance Index Squares {
    type Key = Int
    type Value = Int
    fun get(_, i) = i * i
}

fun main() {
    let s = Squares
    print(s[4])
    s[4] = 0
}
```

> **Coming from Rust:** `Index` and `SetIndex` are Rust's `Index` and
> `IndexMut`. `SetIndex` takes the new value rather than handing out a
> mutable reference, since Turkey has no references to an element.

## `Length`

```text
class Length c { fun len(c) -> Int }
```

`len(xs)` is the number of elements in an `Array`, a `Vec`, a `Map` or a
`Set`.
`String` has no `Length` instance ([String](types.md#string)).

## `Functor`, `Applicative` and `Monad`

```text
class Functor f {
    fun map(f a, fun(a) -> b) -> f b
}

class Applicative f : Functor f {
    fun pure(a) -> f a
}

class Monad m : Applicative m {
    fun bind(m a, fun(a) -> m b) -> m b
}
```

`e?` calls `bind`, and the translation of `?` may call `pure`
([The `?` operator](do-notation.md)). The library provides instances for
`Option`, `Either l`, `Array` and `Vec`. A program can write instances for its
own types; a `Monad` instance needs `Functor` and `Applicative` instances too.

## `error`

```text
fun error(message : String) -> a
```

`error` [panics](runtime-errors.md) with the message. Because its result type
is a variable that its parameter does not mention, a call to it fits any type.

## `Typed` and `cast`

```text
class Typed a { fun typeRep(Proxy a) -> TypeRep }
```

`Typed` gives a runtime description of a type. The compiler supplies an
instance for every type, and a program may not declare one, because checked
downcasting relies on the answer being true.

Its use is recovering the concrete type of an error that has been packed into
the library's `SomeError`. `SomeError` is an
[existential type](types.md#existential-types) that holds a value of any type
with an `Error` instance, and `Error` has `Typed` as a superclass. So
`Error.cast` can check whether a `SomeError` holds a particular type, and
return it:

<!-- run -->
```kotlin
type NotFound = NotFound { path : String }

instance Error NotFound {
    fun message(e) = "not found: " + e.path
}

fun load(path) = if path == "" { Right("empty") } else { Left(fail(NotFound { path })) }

fun main() {
    match load("notes.txt") {
        Left(err) -> {
            print(Error.describe(err))
            match Error.cast(err) : Option NotFound {
                Some(missing) -> print("could create " + missing.path)
                None -> print("some other error")
            }
        }
        Right(text) -> print(text)
    }
}
```

```text
not found: notes.txt
could create notes.txt
```

`fail(e)` packs a value into a `SomeError`, and `Error.cast` returns `Some`
only when the packed value has exactly the requested type.
