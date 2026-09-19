# Grammar

This chapter collects the syntax of the whole language in one place. Each
production also appears, with its meaning, in the chapter that covers it. The
notation is described in the [README](README.md#notation).

Comma-separated lists accept a trailing comma. `separator` is a line break
that the [line-break rule](lexical.md#line-breaks-and-semicolons) keeps, or a
`;`. Where field lists use `field-sep`, a comma, a kept line break, or both
separate two fields.

## Tokens

```ebnf
IDENT   ::= [a-z_] [A-Za-z0-9_']*
CONID   ::= [A-Z] [A-Za-z0-9_']*
INT     ::= [0-9]+
FLOAT   ::= [0-9]+ "." [0-9]+ ( [eE] [+-]? [0-9]+ )?
STRING  ::= '"' ( character | escape )* '"'
CHAR    ::= "'" ( character | escape ) "'"
escape  ::= "\n" | "\t" | "\r" | "\0" | "\\" | "\"" | "\'" | "\u{" hex hex? hex? hex? hex? hex? "}"
```

Keywords: `as break class continue do else export for foreign fun hiding if
import in instance let loop match module return type var while`.

Comments: `--` to the end of the line, and `{- ... -}`, which nests.

## Modules

```ebnf
module      ::= header? top-level*
header      ::= "module" modname ("(" export ("," export)* ")")?
modname     ::= CONID ("." CONID)*
export      ::= IDENT
              | CONID
              | CONID "(" ".." ")"
              | CONID "(" CONID ("," CONID)* ")"
              | "module" modname
top-level   ::= import | type-decl | class-decl | instance-decl
              | fun-decl | let-decl | var-decl
import      ::= "import" modname ("as" CONID)? import-list?
import-list ::= "(" item ("," item)* ")"
              | "hiding" "(" item ("," item)* ")"
item        ::= IDENT | CONID | CONID "(" ".." ")" | CONID "(" CONID ("," CONID)* ")"
```

Top-level items are separated by `separator`s.

## Types

```ebnf
type            ::= atype atype*
atype           ::= IDENT
                  | qualified-CONID
                  | "fun" "(" (type ("," type)*)? ")" "->" type
                  | "(" type ")"
                  | "(" type ("," type)+ ")"
qualified-CONID ::= CONID ("." CONID)*
```

In `atype atype*`, the head must be a type constructor or a type variable.

## Type declarations

```ebnf
type-decl    ::= "type" CONID IDENT* "=" type-rhs
type-rhs     ::= constructor ("|" constructor)*
               | type                                      -- alias
constructor  ::= CONID existential? payload?
existential  ::= "[" binder ("," binder)* "]"
binder       ::= IDENT | qualified-CONID atype
payload      ::= "(" type ("," type)* ")"
               | "{" (field (field-sep field)*)? "}"
field        ::= IDENT ":" type
```

Whether `type T = C args` is an alias or a data type is decided as described
in [Type aliases](types.md#type-aliases).

## Classes and instances

```ebnf
class-decl    ::= "class" CONID IDENT (":" superclass ("," superclass)*)?
                  "{" (class-item (separator class-item)*)? "}"
superclass    ::= qualified-CONID atype
class-item    ::= "fun" IDENT context? "(" (type ("," type)*)? ")" "->" type
                | fun-decl
                | "type" CONID IDENT
instance-decl ::= "instance" qualified-CONID atype (":" constraint ("," constraint)*)?
                  "{" (instance-item (separator instance-item)*)? "}"
instance-item ::= fun-decl
                | "type" CONID "=" type
context       ::= "[" constraint ("," constraint)* "]"
constraint    ::= qualified-CONID atype
                | type "~" type
```

## Declarations

```ebnf
fun-decl  ::= "fun" IDENT context? "(" (pattern ("," pattern)*)? ")" ("->" type)? fun-body
fun-body  ::= "=" expression
            | block
let-decl  ::= "let" pattern "=" expression
var-decl  ::= "var" pattern "=" expression
```

## Statements

```ebnf
block      ::= "{" (statement (separator statement)*)? "}"
statement  ::= let-decl | var-decl | fun-decl
             | assignment
             | expression
assignment ::= IDENT "=" expression
             | postfix "." IDENT "=" expression
             | postfix "[" expression "]" "=" expression
```

## Expressions

Binary operators, from loosest to tightest. All are left-associative.

```ebnf
expression ::= or-expr (":" type)?
or-expr    ::= and-expr ("||" and-expr)*
and-expr   ::= eq-expr ("&&" eq-expr)*
eq-expr    ::= rel-expr (("==" | "!=") rel-expr)*
rel-expr   ::= add-expr (("<" | "<=" | ">" | ">=") add-expr)*
add-expr   ::= mul-expr (("+" | "-") mul-expr)*
mul-expr   ::= unary (("*" | "/" | "%") unary)*
unary      ::= ("-" | "!") unary
             | postfix
postfix    ::= atom ( "(" (expression ("," expression)*)? ")"
                    | "[" expression "]"
                    | "." IDENT
                    | "." INT
                    | "?" )*
```

```ebnf
atom ::= INT | FLOAT | STRING | CHAR
       | IDENT
       | qualified-CONID
       | CONID ("." CONID)* "." IDENT                            -- qualified variable
       | qualified-CONID "{" (field-init (field-sep field-init)*)? "}"
       | "(" ")"
       | "(" expression ")"
       | "(" expression ("," expression)+ ")"
       | "[" (expression ("," expression)*)? "]"
       | block
       | "do" block
       | "fun" "(" (pattern ("," pattern)*)? ")" ("->" type)? fun-body
       | if-expr
       | "while" condition block
       | "for" pattern "in" condition block
       | "for" statement? ";" expression ";" statement? block
       | "loop" block
       | "match" condition "{" match-arm (separator match-arm)* "}"
       | "return" expression?
       | "break" expression?
       | "continue"

field-init ::= IDENT "=" expression
             | IDENT
if-expr    ::= "if" condition block ("else" (block | if-expr))?
condition  ::= expression                                        -- no bare record expression
             | ("let" | "var") pattern "=" expression            -- only after "if" and "while"
match-arm  ::= "|"? pattern ("|" pattern)* "->" expression
```

In a `condition`, and in the collection of `for ... in`, a record expression
must be parenthesized. `let` and `var` conditions are accepted only after
`if` and `while`.

## Patterns

```ebnf
pattern       ::= pattern-atom (":" type)?
pattern-atom  ::= IDENT
                | "_"
                | INT | FLOAT | STRING | CHAR
                | "(" pattern ")"
                | "(" pattern ("," pattern)+ ")"
                | qualified-CONID
                | qualified-CONID "(" (pattern ("," pattern)*)? ")"
                | qualified-CONID "{" field-pattern (field-sep field-pattern)* (field-sep "..")? "}"
                | qualified-CONID "{" ".." "}"
field-pattern ::= IDENT "=" pattern
                | IDENT
```
