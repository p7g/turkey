# Type inference

Turkey checks every type when a program is compiled, and it works out almost
all of them itself. A function with no annotations still has a precise type,
often a more general one than its author had in mind. This chapter covers
what inference produces and when: generalization and its limits, how numeric
and array literals get their types, and the constraints that appear in inferred
types.
To see the types the compiler infers for a file, run:

```sh
boot check --dump types program.gob
```

## Principal types

The type system is Hindley–Milner, extended with type classes and a few other
kinds of constraint. Inference finds each binding's **principal type**: the
most general type the binding can have. Every other type it could be given is
an instance of that one. So an annotation is never needed for a program to
type-check, except in the few places listed [below](#where-annotations-are-needed),
and adding one can only make a type more specific or state the same type.
A few constraints that appear in inferred types cannot be written in an
annotation: [`OneOf`](#numeric-literals) and the [field
constraints](#field-and-projection-constraints), and `Typed` of a type constructor
([`Typed` and `cast`](builtins.md#typed-and-cast)). A binding inferred with
one of those has no annotation that restates its type.

<!-- check -->
```kotlin
fun twice(f, x) = f(f(x))
fun pairUp(x) = (x, x)
fun label(x) = "<" + show(x) + ">"
```

The `types` dump reports these as:

```text
twice : fun(fun(a) -> a, a) -> a
pairUp : fun(a) -> (a, a)
label : [Show a] fun(a) -> String
```

> **For readers new to this.** *Hindley–Milner* is the inference algorithm
> of ML and Haskell. It gives every expression a type by solving equations
> between the types of its parts, and it never needs to guess, because every
> well-typed binding has a single most general type, its *principal type*.
> A type variable such as `a` means "any type": `twice` works for every `a`,
> so it is *polymorphic*.

## Generalization

A binding is **generalized** when its inferred type is made polymorphic, so
that each use of it may pick different types for its type variables.

* **Top-level functions** are always generalized.
* **Local `fun` declarations and `let` bindings** are generalized only when
  they are *closed*, as described [below](#local-bindings).
* **`let` bindings** are generalized only when the right-hand side is a
  *value*: a literal, a variable, a lambda, a constructor of an
  [immutable](types.md#mutability-and-sharing) type applied to values, or a
  tuple of values. This is the **value restriction**. A right-hand side
  that calls a function, or creates an array, is not generalized, because
  what it creates might be mutable, and a polymorphic mutable object could be
  written at one type and read at another.
* **`var` bindings** are never generalized. A `var` can be assigned, and
  every value assigned to it must have its one type.
* **Parameters** are never generalized. A parameter has one type for the
  whole body of its function.

<!-- run -->
```kotlin
fun main() {
    let identity = fun(x) = x
    print(identity(1))
    print(identity("one"))
}
```

```text
1
one
```

`[]` is not a value in this sense, since it allocates a new array, so an empty
array bound with `let` has one element type, fixed by its first use:

<!-- error: a numeric literal cannot have type 'String' -->
```kotlin
fun main() {
    let names = []
    Vec.push(names, 1)
    Vec.push(names, "Ada")
}
```

A top-level `let` that is not generalized has one type for the whole program.
Its type variables are fixed by its uses, in its own module or in one that
imports it. A numeric literal or a class constraint in it is settled sooner,
at the end of its own module: a later use in that module decides it, and
whatever is still open there is [defaulted](#numeric-literals) or
[ambiguous](#ambiguity). Modules that [import each other in a
cycle](modules.md#modules-and-files) are checked together, so for them it is
the end of the cycle, and a use in any of them decides it. Here `seed` becomes a `Float`, because `half` divides
it by one:

<!-- run -->
```kotlin
fun same(x) = x

let seed = same(3)

fun half() = seed / 2.0

fun main() {
    print(half())
}
```

```text
1.5
```

A use in a module that imports `seed` comes too late, since `seed` has
already defaulted to `Int`:

<!-- module: Seed.gob -->
```kotlin
export (seed)

fun same(x) = x

let seed = same(3)
```

<!-- error: expected 'Int', found 'Float' in the operands of '/' -->
```kotlin
import Seed (seed)

fun main() {
    print(seed / 2.0)
}
```

An [array literal](#array-literals) in a top-level `let` is decided sooner
still, by the binding's own definition.

### Local bindings

A local `fun` declaration or `let` binding is generalized only if it is
**closed**: every variable it uses, other than those it defines itself, must
be closed. A variable is closed if it is

* defined at the top level or imported, except inside its own recursive
  group: a top-level function without a signature is not closed in the
  bodies of the functions it is defined together with, itself included, or
* a local `fun` with a [signature](declarations.md#signatures) that mentions
  no type variable of the function around it, or
* a local `fun` or `let` that was itself generalized, and whose type mentions
  no type variable of the function around it, or
* a `let` that the value restriction keeps from being generalized, if every
  variable it uses is closed and its type has no type variables.

A parameter, a variable bound by a pattern, a `var` and a `do` binder are
never closed. The value restriction applies as well: a closed `let` is
generalized only if its right-hand side is a value.

`show` is defined at the top level, so `describe` is closed and is generalized,
and so is `both`, which uses only `describe`:

<!-- run -->
```kotlin
fun main() {
    let describe = show
    let both = fun(a, b) = describe(a) + " and " + describe(b)
    print(describe(1))
    print(describe(True))
    print(both(2.5, "two"))
}
```

```text
1
True
2.5 and two
```

A `let` whose right-hand side is a call is not generalized, but when its
type has no type variables there is nothing to generalize, and it is closed
like any other. Here `suffix` is a `String`, so `tag` is generalized:

<!-- run -->
```kotlin
fun main() {
    let suffix = show(1) + "!"
    let tag = fun(v) = show(v) + suffix
    print(tag(2))
    print(tag(True))
}
```

```text
21!
True1!
```

A local binding that uses a parameter is not generalized. It has one type,
fixed by its uses, as a `var` has:

<!-- error: 'line' is used here at a second type, 'fun(Bool) -> String', but it cannot be polymorphic because it uses 'prefix' -->
```kotlin
fun report(prefix : String) {
    let line = fun(v) = prefix + show(v)
    print(line(1))
    print(line(True))
}
```

A signature states the type, so a local `fun` with one is polymorphic whatever
its body uses:

<!-- run -->
```kotlin
fun report(prefix : String) {
    fun line[Show a](v : a) -> String = prefix + show(v)
    print(line(1))
    print(line(True))
}

fun main() {
    report("> ")
}
```

```text
> 1
> True
```

A [partial annotation](declarations.md#partial-annotations) also keeps a local
`fun` generalized, because each type variable its header writes must stay a
type of its own.

> **For readers new to this.** A binding that uses a parameter can have the
> parameter's type in its own type, and that type belongs to the function
> around it, where inference may still be working it out. When that
> function's context has an [equality
> constraint](classes.md#equality-constraints), generalizing over such a type
> can leave the binding with no single most general type. A closed binding
> shares no such undecided type with the function around it: its type can
> mention one of that function's type variables only where an annotation
> names it, and then the type is fixed. So generalizing it is always safe.
> GHC calls this rule `MonoLocalBinds`.

**Recursive groups.** Functions that call each other are inferred together,
and inside the group each has a single, monomorphic type. So a function
cannot be inferred to call itself at a different type. With a complete
[signature](declarations.md#signatures), it can.

> **For readers new to this.** The *value restriction* is the rule ML uses to
> keep polymorphism and mutation apart. Without it, `let cell = []` could be
> both a `Vec Int` and a `Vec String`, and a program could push an `Int` and
> then read it back as a `String`.

## Numeric literals

A numeric literal does not have a single type. An integer literal such as `3`
can be an `Int` or a `Float`, and a floating-point literal such as `2.5` can
only be a `Float`. The literal's type is decided by how its value is used:

<!-- run -->
```kotlin
fun main() {
    let count = 3
    print(count + 1)
    print(count + 1.5)
    print(1.0 + 2)
}
```

```text
4
4.5
3.0
```

`count` is bound to a value with `let` and uses no local variable, so it is
[generalized](#local-bindings), and each use
picks a type: `Int` in `count + 1` and `Float` in `count + 1.5`. In `1.0 + 2`,
both operands of `+` have the same type, so the `2` is a `Float`.

A `var` is not generalized, so every use of it must agree. Here `total + 0.5`
makes `total` a `Float`, including in the line before it:

<!-- run -->
```kotlin
fun main() {
    var total = 3
    print(total + 1)
    print(total + 0.5)
}
```

```text
4.0
3.5
```

Whether an integer literal can have a type depends on its value. It can be a
`Float` only if its magnitude is below 2^53, the range in which a `Float` holds
every integer exactly, so `9007199254740993` can only be an `Int`. A literal
past the largest `Int` is rejected by the lexer
([Integer literals](lexical.md#integer-literals)).

When nothing decides a literal's type, it is **defaulted**: an integer literal
to `Int`, and a floating-point literal to `Float`. `print(1 + 2)` prints the
`Int` `3`.

A function that does arithmetic on a literal without fixing its type stays
general over both numeric types:

```text
fun inc(x) = x + 1
-- inc : [OneOf a {Int, Float}, Add a] fun(a) -> a
```

`OneOf a {Int, Float}` is the constraint a literal contributes: `a` must be
one of the listed types. It appears in inferred types, but it cannot be
written in a program.

> **Coming from Haskell.** This is Haskell's `Num` and `Fractional`
> defaulting, with a closed set of types instead of classes. A program cannot
> add a numeric type to the set.

## Array literals

An [array literal](expressions.md#array-literals) does not have a single type
either. It is an `Array` or a `Vec` of its elements, decided by how its value is
used, and an `Array` when nothing decides:

<!-- run -->
```kotlin
fun main() {
    let seen = []
    for word in ["to", "be", "or", "not", "to", "be"] {
        if !Vec.contains(seen, word) {
            Vec.push(seen, word)
        }
    }
    print(seen)
}
```

```text
[to, be, or, not]
```

`seen` is a `Vec` because `Vec.contains` and `Vec.push` take one. The words are
an `Array`: `for` accepts either, and nothing else decides. An annotation
decides too, as in `let names : Vec String = []`, and so does a function's
declared parameter or result type.

Unlike a numeric literal, an array literal is never generic in its container.
A binding that is generalized, such as a function, still decides which of the
two it builds, and defaults to `Array` when its own body does not say:

<!-- error: expected 'Vec', found 'Array' in a function call -->
```kotlin
fun empty() = []

fun fill(v : Vec Int) = Vec.push(v, 1)

fun main() {
    fill(empty())
}
```

`empty` is `fun() -> Array a`: generic in the element, but not in the
container. Declaring `fun empty() -> Vec a` makes it build a `Vec`. A signature
cannot ask for both: `fun empty() -> c a = []` is rejected with `an array
literal cannot have type 'c a'; it must be one of 'Array', 'Vec'`.

A literal in a top-level `let` is decided by that `let`'s own definition. The
binding is not generalized ([the value restriction](#generalization)), and uses
of it in functions, in the same module or in a module that imports it, come too
late: a table that only a function pushes to is an `Array`, and the push is a
type error:

<!-- error: expected 'Vec', found 'Array' in a function call -->
```kotlin
let pending = []

fun remember(x : Int) = Vec.push(pending, x)
```

Annotate the binding, `let pending : Vec Int = []`, to make it a `Vec`.

The constraint a literal contributes is `OneOf t {Array, Vec} e`: `t` must be
one of the listed containers applied to the element type `e`. Like the numeric
one, it cannot be written in a program, and a mismatch names the set:

<!-- error: an array literal cannot have type 'Int'; it must be one of 'Array', 'Vec' -->
```kotlin
let count : Int = [1, 2]
```

> **Coming from Haskell.** This is `OverloadedLists`, with a closed set of two
> containers instead of the class `IsList`, and with a default: a literal
> nothing decides is an `Array` rather than ambiguous.

> **Coming from Rust.** `[a, b]` covers both `[a, b]` and `vec![a, b]`, and the
> program's use of it chooses. There is no fixed-size array type whose length
> is part of the type.

## Field and projection constraints

Reading a field does not require the record's type to be known. `r.width`
contributes the constraint "`r`'s type has a field `width`", and the solver
checks it once the type of `r` is known. A function that reads fields is
therefore polymorphic over every record type that has them
([Field access](expressions.md#field-access)):

```text
fun area(r) = r.width * r.height
-- area : [Field.width a ~ Field.height a, Mul (Field.width a),
--         HasField "width" a, HasField "height" a] fun(a) -> Field.width a
```

Read the constraints as: `a` has fields `width` and `height`; `Field.width a`
is the type of the `width` field; the two fields have the same type; and that
type can be multiplied. Tuple projection works the same way, with
`HasProjection 1 a` and `Elem.1 a` for `t.1`.

Assigning a field adds `SetField`, which holds only when the field is declared
[`var`](types.md#mutability-and-sharing):

```text
fun deposit(account, amount) {
    account.balance = account.balance + amount
}
-- deposit : [HasField "balance" a, SetField "balance" a,
--            Add (Field.balance a)] fun(a, Field.balance a) -> Unit
```

Like `OneOf`, these constraints appear in inferred types but cannot be written
in a program. Records are still nominal: a type has a field because its
declaration says so, not because of its shape.

## Ambiguity

A constraint is **ambiguous** when it mentions a type variable that nothing
can ever decide: one that appears in no parameter, no result, and no other
use. The classic case is a method whose class parameter appears only in its
result, such as `default() -> a`, passed to a function that accepts any type:

<!-- error: cannot determine what type 'a' is in 'Show a'; add a type annotation -->
```kotlin
class Default a {
    fun default() -> a
}

instance Default Int {
    fun default() = 0
}

fun main() {
    print(default())
}
```

The fix is an annotation that picks the type: `print(default() : Int)`.

The same happens with an empty array whose element type nothing fixes, when
it is printed or compared:

<!-- error: add a type annotation -->
```kotlin
fun main() {
    print([])
}
```

A signature that states a constraint on a variable its type does not mention
is rejected for the same reason, where it is written.

## Where annotations are needed

Inference covers everything except:

* **Ambiguous constraints**, [above](#ambiguity).
* **Polymorphic recursion**: a function that calls itself at a different type
  needs a complete [signature](declarations.md#signatures).
* **Recursive groups that use a member at two types**: every function in the
  group that is used polymorphically needs a signature.
* **A type that only a body under an equality decides**: a type from outside
  a function whose context has an
  [equality constraint](classes.md#equality-constraints) has to be decided
  outside it.

Annotations are also useful as documentation at module boundaries. A
signature is always checked, so it cannot drift from the code
([Type annotations](declarations.md#type-annotations)).
