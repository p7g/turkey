# Lexical structure

A Turkey source file is UTF-8 text, and the compiler reads it as a sequence of
tokens. This chapter covers how the text is split into tokens: comments,
identifiers, keywords, literals, and the rule that decides when a line break
ends a statement.

## Source files

A source file has the extension `.gob` and holds one [module](modules.md).
Spaces, tabs and carriage returns separate tokens and are otherwise ignored.
Line breaks are different: some of them end a statement, as described
[below](#line-breaks-and-semicolons).

## Comments

A line comment starts with `--` and runs to the end of the line. A block
comment starts with `{-` and ends with `-}`, and block comments nest, so one
can comment out code that already contains one.

<!-- run -->
```kotlin
-- A line comment.
{- A block comment,
   {- with another one inside, -}
   still commented out. -}
fun main() {
    print("comments are whitespace") -- trailing comment
}
```

```text
comments are whitespace
```

## Identifiers

```ebnf
IDENT ::= [a-z_] [A-Za-z0-9_']*
CONID ::= [A-Z] [A-Za-z0-9_']*
```

The first letter decides what a name refers to:

* An **`IDENT`** starts with a lowercase letter or `_`. In an expression it
  names a variable or a function. In a pattern it binds a variable. In a type
  it is a type variable.
* A **`CONID`** starts with an uppercase letter. In an expression or a pattern
  it names a value constructor (`Some`, `True`). In a type it names a type
  constructor (`Int`, `Option`) or a class (`Eq`). Module names are `CONID`s
  too.

Identifiers are ASCII. After the first character they may contain letters,
digits, `_` and `'`, so `x'` and `count_2` are both identifiers.

`_` on its own is not a variable. It is the wildcard [pattern](patterns.md),
which matches anything and binds nothing, and using it as an expression is an
error.

Because the first letter decides, a parameter list cannot mistake a type for
a variable. In `fun(Int) -> Int = ...`, the `Int` inside the parentheses is a
constructor pattern, not a parameter's type.

## Keywords

These words are reserved and cannot be used as identifiers:

```text
as        break     class     continue  do        else      export
for       foreign   fun       hiding    if        import    in
instance  let       loop      match     module    return    type
var       while
```

`True`, `False`, `None` and `Some` are not keywords. They are constructors of
ordinary library types ([Built-in types and classes](builtins.md)).

## Operators and punctuation

```text
+   -   *   /   %   ==  !=  <   <=  >   >=  &&  ||  !
=   ->  :   ,   .   ..  |   ~   ?   ;
(   )   [   ]   {   }
```

The lexer takes the longest operator that matches, so `<=` is one token and
not `<` followed by `=`. What each operator means is covered under
[Expressions](expressions.md#operators).

## Literals

### Integer literals

```ebnf
INT ::= [0-9]+
```

An integer literal is a sequence of decimal digits. There are no hexadecimal,
octal or binary forms and no digit separators. A literal has no sign: `-5` is
unary minus applied to `5`.

An integer literal can be used as either an `Int` or a `Float`. Which one it
becomes is decided by type inference, as described in
[Numeric literals](inference.md#numeric-literals). A literal that no numeric
type can hold exactly is an error. `9223372036854775808` is one past the
largest `Int`:

<!-- error: this numeric literal is not representable in any numeric type -->
```kotlin
fun main() {
    print(9223372036854775808)
}
```

### Floating-point literals

```ebnf
FLOAT ::= [0-9]+ "." [0-9]+ ( [eE] [+-]? [0-9]+ )?
```

A floating-point literal needs digits on both sides of the `.`, so `1.` and
`.5` are not literals. An exponent is allowed only after a fractional part:
`1.5e3` is a `Float`, while `1e3` is the integer `1` followed by the
identifier `e3`. A floating-point literal is always a `Float`.

<!-- run -->
```kotlin
fun main() {
    print(1.5e3)
    print(2.5E-2)
    print(0.1 + 0.2)
}
```

```text
1500.0
0.025
0.30000000000000004
```

A `.` followed by a digit continues a number only when a digit comes before
it as well. After a `.` that selects a field, digits are a
[tuple projection](expressions.md#tuple-projection), so `pair.1.0` is two
projections and not the number `1.0`.

### String literals

```ebnf
STRING ::= '"' ( character | escape )* '"'
escape ::= "\n" | "\t" | "\r" | "\0" | "\\" | "\"" | "\'" | "\u{" hex{1,6} "}"
```

A string literal is written in double quotes and cannot span lines. Its value
is a `String`: an immutable sequence of bytes that is always valid UTF-8
([Types](types.md#string)).

The escapes are:

| Escape | Character |
|---|---|
| `\n` | line feed |
| `\t` | tab |
| `\r` | carriage return |
| `\0` | NUL |
| `\\` | backslash |
| `\"` | double quote |
| `\'` | single quote |
| `\u{H}` | the Unicode scalar value with hexadecimal code `H`, one to six digits |

`\u{...}` must name a Unicode scalar value. That excludes the surrogates
`D800` to `DFFF` and anything above `10FFFF`, and there is no escape for a raw
byte. Together these rules mean every string literal is valid UTF-8.

<!-- run -->
```kotlin
fun main() {
    print("tab:\t| quote:\" | smile:\u{1F600}")
}
```

```text
tab:	| quote:" | smile:😀
```

<!-- error: is not a Unicode scalar value -->
```kotlin
fun main() {
    print("\u{D800}")
}
```

### Character literals

```ebnf
CHAR ::= "'" ( character | escape ) "'"
```

A character literal is exactly one Unicode scalar value in single quotes,
written directly or as one of the escapes above. Its type is `Char`.

<!-- run -->
```kotlin
fun main() {
    print('a')
    print('\u{41}')
}
```

```text
a
A
```

### Other literal forms

Turkey has no boolean literal keywords. `True` and `False` are the
constructors of the library type `Bool`. The unit value is written `()`, and
an empty array is written `[]`. There is no literal syntax for `Byte`.

## Line breaks and semicolons

Turkey has no mandatory statement terminator. A line break ends a statement
when the statement looks finished and the next line looks like the start of a
new one; otherwise the line break is just whitespace. `;` always ends a
statement, so it can put several statements on one line.

Precisely, a line break is a separator when all three of these hold:

1. **The token before it can end a statement**: an identifier, a constructor
   name, a literal, `)`, `]`, `}`, `return`, `break`, `continue`, or `?`.
2. **The token after it can start a statement**: an identifier, a constructor
   name, a literal, `(`, `[`, `{`, `-`, `!`, `..`, or one of the keywords
   `class`, `continue`, `break`, `do`, `for`, `foreign`, `fun`, `if`,
   `import`, `instance`, `let`, `loop`, `match`, `module`, `return`, `type`,
   `var`, `while`.
3. **The innermost bracket around it is not `(` or `[`.** Inside parentheses
   and square brackets a line break never separates anything. Inside braces,
   rules 1 and 2 decide.

Blank lines count as a single line break. A run of line breaks that contains a
`;` always separates.

The rule makes common layouts work without continuation markers:

<!-- run -->
```kotlin
fun describe(n) {
    let total = n +
        1                 -- a trailing operator continues the line
    let doubled = total
        * 2               -- so does a leading binary operator
    let pair = (doubled,
                total)    -- line breaks inside ( ) are ignored
    if doubled > 10 {
        print("big")
    }
    else {                -- `else` on its own line continues the `if`
        print("small")
    }
    print(pair); print(pair.0)
}

fun main() {
    describe(5)
}
```

```text
big
(12, 6)
12
```

Two consequences are worth knowing, because they are the places where a line
break changes the meaning of a program:

* A line that starts with `-` or `!` begins a new statement, because both can
  start an expression. To continue an expression with a subtraction, end the
  previous line with the `-` instead.
* A line that starts with `(` or `[` begins a new statement. It is not a call
  or an index applied to the line before.

In a record literal, record pattern or record declaration, a line break that
separates is a field separator, so commas between fields are optional when
each field is on its own line ([Records](types.md#records)).
