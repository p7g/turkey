# Type classes

A type class names a set of operations. An instance provides those operations
for one type, and a function constrained by the class works on every type that
has an instance. Operators, `for` loops, indexing, `?` and `print` are all
built on classes ([Built-in types and classes](builtins.md)). This chapter
covers declaring classes and instances, superclasses and default methods,
associated types, equality constraints, and the rules that keep instances
coherent.

> **For readers new to this.** A type class is an interface that types
> implement *separately* from their declaration. It is how Turkey does
> *ad hoc polymorphism*: one name, such as `show` or `+`, with a different
> implementation for each type.
>
> **Coming from Rust.** A class is a trait, an instance is an `impl`, and a
> context `[Show a]` is a bound `<A: Show>`. Methods are called as plain
> functions, `show(x)`, not `x.show()`, and there are no trait objects;
> [existential types](types.md#existential-types) fill that role.
>
> **Coming from Haskell.** Classes are Haskell 98's, with one parameter, plus
> associated type families. There are no multi-parameter classes and no
> functional dependencies.

## Classes

**Syntax**

```ebnf
class-decl ::= "class" CONID IDENT (":" superclass ("," superclass)*)? "{" class-item* "}"
superclass ::= qualified-CONID IDENT
             | type "~" type                                                   -- equality superclass
class-item ::= "fun" IDENT context? "(" (type ("," type)*)? ")" "->" type      -- signature
             | fun-decl                                                        -- default method
             | "type" CONID IDENT                                              -- associated type
```

A class has a name and exactly one type parameter. Its body lists **method
signatures**: a `fun` with no body, whose parameter list holds *types* rather
than parameter names, and whose return type is required.

<!-- run -->
```kotlin
class Describe a {
    fun describe(a) -> String
    fun loudly(x : a) -> String = describe(x) + "!"
}

type Dog = Dog { name : String }

instance Describe Dog {
    fun describe(dog) = dog.name + " the dog"
}

instance Describe Bool {
    fun describe(b) = if b { "yes" } else { "no" }
}

fun main() {
    print(describe(Dog { name = "Rex" }))
    print(loudly(True))
}
```

```text
Rex the dog
yes!
```

A method with a body is a **default method**. An instance may define it, and
if it does not, the default is used. In a default method the parameters are
patterns, as in any function, so a parameter's type is written as an
annotation: `fun loudly(x : a) -> String`. A signature without a body cannot
name its parameters, and a function with a body must name them.

Every method is also a function in the module's namespace, with the class
constraint added to its type: `describe : [Describe a] fun(a) -> String`. A
method's signature may have its own context, for constraints on type variables
other than the class parameter.

The class parameter may stand for a type constructor rather than a complete
type. In `class Functor f { fun map(f a, fun(a) -> b) -> f b }`, `f` is
applied to an argument, so it has [kind](types.md#type-expressions)
`* -> *`, and the instances are for `Option`, `Array` and so on, not for
`Option Int`.

### Superclasses

`class Ord a : Eq a { ... }` makes `Eq` a **superclass** of `Ord`. Every type
with an `Ord` instance must also have an `Eq` instance, and a function with an
`Ord a` constraint can use `Eq`'s methods on `a` without asking for `Eq`
separately. A superclass constrains the class's own parameter, never a type
built from it. A superclass may also be an equality that fixes an associated
type, as described in [Equality superclasses](#equality-superclasses).

<!-- error: 'Show Cat' is required by 'Named', but there is no such instance -->
```kotlin
class Named a : Show a {
    fun name(a) -> String
}

type Cat = Cat

instance Named Cat {
    fun name(c) = "cat"
}
```

## Instances

**Syntax**

```ebnf
instance-decl ::= "instance" qualified-CONID atype (":" constraint ("," constraint)*)?
                  "{" instance-item* "}"
instance-item ::= fun-decl
                | "type" CONID "=" type
```

An instance gives the class's methods for one **head** type. The head is a
type constructor applied to distinct type variables, such as `Dog`,
`Option a`, `(a, b)` or `Either l`. It cannot be a type like `Option Int`,
cannot repeat a variable, and cannot be a function type. A head with arguments
is written in parentheses.

Every method without a default must be defined. Each method is checked
against the class's signature, with the class parameter replaced by the head,
and it must be exactly as general as that: a `map` in `instance Functor
Option` must work for every element type, because the signature says it
does.

An instance can require instances for the head's type variables. The
requirements follow the head after a `:`:

<!-- run -->
```kotlin
type Box a = Box(a)

instance Show (Box a) : Show a {
    fun show(Box(x)) = "Box(" + show(x) + ")"
}

fun main() {
    print(Box(3))
    print(Box([Some("a")]))
}
```

```text
Box(3)
Box([Some(a)])
```

`Show (Box a) : Show a` says: a `Box a` can be shown whenever an `a` can. The
instance's methods may use those constraints. The context may also hold
[equalities](#equality-constraints) about the head's variables.

> **Coming from Haskell.** `instance Show (Box a) : Show a` is
> `instance Show a => Show (Box a)`: the context comes after the head.

### Coherence

For each class, a type constructor has **at most one instance** in the whole
program. Because an instance head is a constructor applied to variables, two
instances for the same constructor would always apply to the same types, so a
second one is an error:

<!-- error: overlapping instances: 'Describe Dog' is already declared at 7:1 -->
```kotlin
class Describe a {
    fun describe(a) -> String
}

type Dog = Dog

instance Describe Dog {
    fun describe(d) = "a dog"
}

instance Describe Dog {
    fun describe(d) = "another dog"
}
```

Instances are global: once declared, an instance is used wherever its class
and type meet, whether or not the module that declared it is imported by the
module that uses it.

**The orphan rule.** An instance must be declared in the module that declares
its class, or in the module that declares its head's type constructor. An
instance declared anywhere else would be an *orphan*, and two unrelated
modules could each declare one for the same class and type. The primitive
types and tuples belong to the language itself, so a program can give them
instances only of its own classes. The same holds for library types such as
`String`, `Array` and `Option`, which belong to library modules:

<!-- error: orphan instance: 'Semigroup Int' is declared in 'Main' -->
```kotlin
instance Semigroup Int {
    fun combine(a, b) = a + b
}
```

To give a library class a new behaviour on an existing type, wrap the type in
a data type of your own and write the instance for the wrapper.

### Derived instances

The compiler provides the instances of the class `Typed` itself, for every
type, and a program may not write one. See [`Typed` and
`cast`](builtins.md#typed-and-cast). No other class has derived instances: a
type gets `Show`, `Eq` and the rest only from instances someone writes.

## Using a class

A method is called like any function. Where the argument's type is known, the
call uses that type's instance. Where it is a type variable, the function's
type gains a constraint, written in a [context](declarations.md#contexts) or
inferred:

<!-- run -->
```kotlin
fun describeAll[Show a](items : Array a) -> String {
    var out = ""
    for item in items {
        out = out + show(item) + ";"
    }
    out
}

fun main() {
    print(describeAll([1, 2]))
    print(describeAll(["x"]))
}
```

```text
1;2;
x;
```

A method whose class parameter appears only in its result, such as a
`default() -> a`, can be called only where the result's type is fixed by the
context around the call. Otherwise the call is ambiguous:

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

`print(default() : Int)` fixes the type and prints `0`.

Classes belong to the module that declares them, so a program may declare a
class whose name is already used by the library, such as its own `Functor`.
Its methods and instances are separate from the library's.

## Associated types

A class can declare an **associated type**: a type that each instance defines
as a function of its head. It is declared in the class with `type Name c`,
where `c` is the class's parameter, and defined in each instance with
`type Name = T`. In types it is used like a type constructor of one argument,
`Name c`.

<!-- run -->
```kotlin
class Container c {
    type Elem c
    fun first(c) -> Elem c
}

type Pair a = Pair(a, a)

instance Container (Pair a) {
    type Elem = a
    fun first(Pair(x, _)) = x
}

instance Container String {
    type Elem = Char
    fun first(s) = match String.step(s, String.start(s)) {
        Some((c, _)) -> c
        None -> error("empty string")
    }
}

fun main() {
    print(first(Pair(10, 20)))
    print(first("hello"))
}
```

```text
10
h
```

`Elem (Pair Int)` is `Int` and `Elem String` is `Char`, and the compiler
replaces one with the other wherever the container type is known. Where it is
not, `Elem c` stays in the type, and a context can place constraints on it:

<!-- check -->
```kotlin
class Container c {
    type Elem c
    fun first(c) -> Elem c
}

fun describeFirst[Container c, Show (Elem c)](xs : c) -> String = show(first(xs))
```

An associated type must be defined in every instance. Its definition may
mention only the head's type variables, and may apply an associated type only
to one of those variables, which guarantees that working out an associated
type always terminates.

An associated type is not *injective*: knowing `Elem c` says nothing about
`c`, because different containers can have the same element type.

> **For readers new to this.** An associated type is a *type family*: a
> function from types to types, defined piece by piece by instances. It lets
> a one-parameter class talk about a second, dependent type, such as a
> container's element type, without a second class parameter.
>
> **Coming from Rust.** This is a trait's associated type, `type Item;`.

`Iterator` and `Index` in the library use associated types for their element,
cursor, key and value types ([Built-in types and classes](builtins.md)).

## Equality constraints

**Syntax**

```ebnf
equality ::= type "~" type
```

A context can require an associated type to be a particular type. `Item s ~ Int`
says "the elements of `s` are `Int`s". Inside the function the two are
interchangeable, so the elements can be added, matched against `Int`
patterns, and so on:

<!-- run -->
```kotlin
fun total[Iterator s, Item s ~ Int](numbers : s) -> Int {
    var sum = 0
    for n in numbers {
        sum = sum + n
    }
    sum
}

fun main() {
    print(total([1, 2, 3]))
    print(total(Map.values(Map.new() : Map String Int)))
}
```

```text
6
0
```

The left side of `~` must be an associated type applied to a type, and the
right side may not mention that same application. A context may give each
associated type application at most one equality.

Before the body is checked, the compiler works out what the context's
equalities say together. Each associated type in them is reduced through the
instances first, so an equality may turn out to be about one of the function's
own type variables. Then that variable and the type are interchangeable inside
the function:

<!-- run -->
```kotlin
class Bag b {
    type Elem b
}

instance Bag (Option a) {
    type Elem = a
}

fun double[Elem (Option a) ~ Int](x : a) -> Int = x * 2

fun main() {
    print(double(21))
}
```

```text
42
```

`Elem (Option a)` is `a` by the instance, so the context says that `a` is
`Int`, and `x * 2` is an `Int`. A caller still has to prove the equality as
written: `double("s")` is refused, because `Elem (Option String)` is not `Int`.

The equalities also rewrite each other. `[F a ~ G a, G a ~ F a]` states one
fact twice, and `[F a ~ F (G b), G b ~ a]` says only that `G b` is `a`, since
the first equality follows from the second.

An equality that can never hold, whatever the caller picks, is an error at
the signature:

<!-- error: the equality 'Bag.Elem (Option a) ~ Option a' in this context can never hold; it would make 'a' contain itself -->
```kotlin
class Bag b {
    type Elem b
}

instance Bag (Option a) {
    type Elem = a
}

fun unwrap[Elem (Option a) ~ Option a](x : a) -> a = x

fun main() { }
```

A class method's own context may constrain the class variable. Every call of
the method has to prove the equality, so an instance's override may rely on
it. Here `Elem (Option a)` is `a`, so inside `Option`'s override the context
says that `a` is `Int`, and the override adds `Int`s:

<!-- run -->
```kotlin
class Box a {
    type Elem a
    fun open(a) -> Elem a
    fun code[Elem a ~ Int](x : a) -> Int = open(x)
}

instance Box (Option a) {
    type Elem = a
    fun open(o) = match o {
        Some(x) -> x
        None -> error("empty")
    }
    fun code(o) = open(o) + 100
}

fun main() {
    print(code(Some(3)))
    print(open(Some("s")))
}
```

```text
103
s
```

The instance is still an instance at every `a`. `open(Some("s"))` uses it at
`String`, where `code`'s equality is false, and that is not an error:
`code(Some("s"))` would be refused, so at `String` the override can never be
called.

The same equality at an instance whose head decides it can never hold makes
that instance's override uncallable altogether, so the override is accepted
without being checked:

<!-- run -->
```kotlin
class Box a {
    type Elem a
    fun open(a) -> Elem a
    fun code[Elem a ~ Int](x : a) -> Int = open(x)
}

type Flag = Flag(Bool)

instance Box Flag {
    type Elem = Bool
    fun open(Flag(b)) = b
    fun code(f) = 0
}

fun main() {
    print(open(Flag(True)))
}
```

```text
True
```

Here `Elem Flag` is `Bool`, so `code`'s context can never hold for a `Flag`,
and a call `code(Flag(True))` is refused.

An instance's context can hold equalities too. `Echo s` reads from a stream
and writes back what it read, so a failure to read and a failure to write both
come out of its `pull`. The equality says that `s` fails both ways with one
type:

<!-- run -->
```kotlin
class Source s {
    type Failure s
    fun pull(s) -> Either (Failure s) Int
}

class Sink s {
    type Trouble s
    fun push(s, Int) -> Either (Trouble s) Unit
}

type Echo s = Echo(s)

instance Source (Echo s) : Source s, Sink s, Failure s ~ Trouble s {
    type Failure = Failure s
    fun pull(Echo(inner)) {
        let n = pull(inner)?
        push(inner, n)?
        Right(n)
    }
}

type Pipe = Pipe(Int)

instance Source Pipe {
    type Failure = String
    fun pull(Pipe(n)) = if n < 0 { Left("closed") } else { Right(n) }
}

instance Sink Pipe {
    type Trouble = String
    fun push(_, n) = if n > 9 { Left("full") } else { Right(()) }
}

fun main() {
    print(pull(Echo(Pipe(3))))
    print(pull(Echo(Pipe(12))))
    print(pull(Echo(Pipe(-1))))
}
```

```text
Right(3)
Left(full)
Left(closed)
```

Inside the instance's methods `Failure s` and `Trouble s` are
interchangeable, so `push(inner, n)?` can return a write failure from `pull`.
Every use of the instance has to prove the equality at its own types:
`pull(Echo(p))` is refused for a `p` whose `Failure` and `Trouble` differ.

An instance's context may mention only the head's type variables, since a use
selects the instance by its head alone and nothing there could say what
another variable is:

<!-- error: 'b' is not bound by the instance head 'Box a' -->
```kotlin
class Bag b {
    type Elem b
}

type Box a = Box(a)

instance Show (Box a) : Show a, Elem b ~ a {
    fun show(Box(x)) = show(x)
}

fun main() { }
```

Equalities also appear in inferred types. A function that iterates over its
argument and treats the elements as `Op` values is inferred to have the
constraint `Item a ~ Op`.

The equalities a body needs rewrite each other as they are found, as a
context's do. Two that cannot hold together are an error where the second
arises, rather than an inferred context that no caller could satisfy:

<!-- error: expected 'Char', found 'Int' in the annotation on 'i' -->
```kotlin
class Glue a {
    type G a
    fun glue(a) -> G a
}

class Fold a {
    type F a
    fun fold(a) -> F a
}

instance Fold Bool {
    type F = Char
    fun fold(b) = 'c'
}

fun weird(x) {
    let g = glue(x)
    let b : Bool = g
    let i : Int = fold(g)
    i
}

fun main() { }
```

`b`'s annotation makes `G a` be `Bool`, so `fold(g)` is an `F Bool`, which is
a `Char`.

### Equality superclasses

An equality among a class's superclasses restricts the class to the
instances of its superclasses where an associated type is fixed. `class
Bytes s : Index s, Key s ~ Int, Value s ~ Byte` is the class of `Index` types
whose keys are `Int`s and whose values are `Byte`s. A function with a `Bytes s`
constraint may use both equalities, as if its own context had written them:

<!-- run -->
```kotlin
class Bytes s : Index s, Key s ~ Int, Value s ~ Byte {}

type Packet = Packet(Array Byte)

instance Index Packet {
    type Key = Int
    type Value = Byte
    fun get(Packet(bytes), i) = bytes[i]
}

instance Bytes Packet {}

fun firstTwo[Bytes s](bytes : s) -> Int = Byte.toInt(bytes[0]) + Byte.toInt(bytes[1])

fun main() {
    print(firstTwo(Packet(Array.map([7, 8], Byte.truncate))))
}
```

```text
15
```

The equalities hold wherever `Bytes s` does: in a default method of `Bytes`,
in the methods of an instance whose context has a `Bytes s`, and for a
subclass of `Bytes`. An inferred type with a `Bytes a` constraint leaves them
out, and a partial annotation's context need not list them. An equality
superclass follows the rules of an equality
in a context, and it may mention no type variable other than the class
parameter. The equalities a class inherits from its superclasses must agree
with its own, and a method's own context may not contradict them:

<!-- error: the equality 'Value s ~ Int' in this context contradicts the superclasses of 'Bytes'; it would make 'Byte' equal to 'Int' -->
```kotlin
class Bytes s : Index s, Value s ~ Byte {
    fun asInt[Value s ~ Int](x : s) -> Int = x[0]
}

fun main() { }
```

Each instance has to prove the class's equalities at its head, using the
equalities its own context grants. `instance Bytes (View s) : Bytes s`, for a
`View s` whose `Value` is `Value s`, proves `Value (View s) ~ Byte` from the
context's `Value s ~ Byte`. An instance where an equality is false, or where
its context does not decide it, is refused:

<!-- error: 'Bytes Words' requires 'Value Words ~ Byte', which can never hold: 'String' is not 'Byte' -->
```kotlin
class Bytes s : Index s, Value s ~ Byte {}

type Words = Words(Array String)

instance Index Words {
    type Key = Int
    type Value = String
    fun get(Words(ws), i) = ws[i]
}

instance Bytes Words {}

fun main() { }
```

An equality superclass and a method context that constrains the class
variable both say that an operation exists only where an associated type is
fixed. A method context keeps the operation in the class, so every instance
has it, and a default can serve them all. An equality superclass makes a new
class, so each type needs its own instance, even an empty one, and only the
types that have one have the operation.
