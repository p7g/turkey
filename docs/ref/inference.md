# Type inference

Turkey checks every type when a program is compiled, and it works out almost
all of them itself. A function with no annotations still has a precise type,
often a more general one than its author had in mind. This chapter covers
what inference produces and when: generalization and its limits, how numeric
literals get their types, and the constraints that appear in inferred types.
To see the types the compiler infers for a file, run:

```sh
boot types program.gob
```

## Principal types

The type system is Hindley–Milner, extended with type classes and a few other
kinds of constraint. Inference finds each binding's **principal type**: the
most general type the binding can have. Every other type it could be given is
an instance of that one. So an annotation is never needed for a program to
type-check, except in the few places listed [below](#where-annotations-are-needed),
and adding one can only make a type more specific or state the same type.

<!-- check -->
```kotlin
fun twice(f, x) = f(f(x))
fun pairUp(x) = (x, x)
fun label(x) = "<" + show(x) + ">"
```

`boot types` reports these as:

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

* **Top-level functions** and **local `fun` declarations** are always
  generalized.
* **`let` bindings** are generalized only when the right-hand side is a
  *value*: a literal, a variable, a lambda, a constructor applied to values,
  or a tuple of values. This is the **value restriction**. A right-hand side
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
    Array.push(names, 1)
    Array.push(names, "Ada")
}
```

**Recursive groups.** Functions that call each other are inferred together,
and inside the group each has a single, monomorphic type. So a function
cannot be inferred to call itself at a different type. With a complete
[signature](declarations.md#signatures), it can.

> **For readers new to this.** The *value restriction* is the rule ML uses to
> keep polymorphism and mutation apart. Without it, `let cell = []` could be
> both an `Array Int` and an `Array String`, and a program could push an `Int`
> and then read it back as a `String`.

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

`count` is bound to a value with `let`, so it is generalized, and each use
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

Like `OneOf`, these constraints appear in inferred types but cannot be written
in a program. Records are still nominal: a type has a field because its
declaration says so, not because of its shape.

## Ambiguity

A constraint is **ambiguous** when it mentions a type variable that nothing
can ever decide: one that appears in no parameter, no result, and no other
use. The classic case is a method whose class parameter appears only in its
result, such as `default() -> a`, passed to a function that accepts any type:

<!-- error: cannot determine a type satisfying 'Show a'. Add a type annotation. -->
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

<!-- error: Add a type annotation -->
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

Annotations are also useful as documentation at module boundaries. A
signature is always checked, so it cannot drift from the code
([Type annotations](declarations.md#type-annotations)).
