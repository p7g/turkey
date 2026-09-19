# Built-in types and classes

Several pieces of syntax are defined in terms of library types and classes:
`if` needs a `Bool`, `+` calls `Add`, a `for` loop calls `Iterator`, `a[i]`
calls `Index`, and `?` calls `Monad`. This chapter describes those types and
classes, and the Prelude that makes them available. It does not document the
rest of the library.

## The Prelude

Every module implicitly imports the module `Prelude`. A module that imports
`Prelude` explicitly gets that import instead of the implicit one, so
`import Prelude (print)` brings in only `print`.

The Prelude provides these names unqualified:

| Kind | Names |
|---|---|
| Types and their constructors | `Bool`, `Option`, `Either`, `Ordering`, `SomeError`, and the types `Array` and `Map` without their constructors |
| Classes and their methods | `Eq`, `Ord`, `Add`, `Sub`, `Mul`, `Div`, `Rem`, `Neg`, `Show`, `Iterator`, `Index`, `Length`, `Functor`, `Applicative`, `Monad`, `Semigroup`, `Monoid`, `Foldable`, `Hash`, `Error`, `Typed` |
| Functions | `print`, `write`, `error`, `fail`, `const` |

It also provides the library modules `Array`, `Bool`, `Byte`, `Char`,
`Either`, `Error`, `Float`, `Int`, `Map`, `Option`, `Ordering` and `String`
under those names, qualified only. That is why `Array.push` and `Int.parse`
need no import, while the bare names `push` and `parse` stay free for a
program to use.

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

## `Array`

`Array a` is a growable, mutable sequence of `a`, with
[reference semantics](types.md#mutability-and-sharing). Its constructor is
hidden: arrays are made with [array literals](expressions.md#array-literals)
and with library functions such as `Array.new` and `Array.filled`.

Arrays are indexed from zero by `Int` ([Indexing](expressions.md#indexing)),
their length is `len(xs)`, `for x in xs` visits their elements in order, and
`+` concatenates two arrays into a new one. Reading or writing outside
`0 .. len(xs) - 1` panics. `Array` is a `Monad`, whose `?` runs the rest of the
block once per element.

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
| `Array a` | no | no | concatenation | no | no | if the elements have it |
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
type CountdownCursor = CountdownCursor { current : Int }

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
    fun set(c, Key c, Value c) -> Unit
}
```

`c[k]` calls `get(c, k)`, and the assignment `c[k] = v` calls `set(c, k, v)`
([Indexing](expressions.md#indexing)). `Key c` and `Value c` are the key and
element types: `Int` and `a` for `Array a`, and `k` and `v` for `Map k v`.

## `Length`

```text
class Length c { fun len(c) -> Int }
```

`len(xs)` is the number of elements in an `Array`, a `Map` or a `Set`.
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
`Option`, `Either l` and `Array`. A program can write instances for its own
types; a `Monad` instance needs `Functor` and `Applicative` instances too.

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
