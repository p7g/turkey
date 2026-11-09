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
INT     ::= decimal | "0x" hex ( "_"? hex )* | "0o" [0-7] ( "_"? [0-7] )*
          | "0b" [01] ( "_"? [01] )*
FLOAT   ::= decimal ( "." digits exponent? | exponent )
exponent ::= [eE] [+-]? digits
decimal ::= "0" | [1-9] ( "_"? [0-9] )*
digits  ::= [0-9] ( "_"? [0-9] )*
hex     ::= [0-9a-fA-F]
INDEX   ::= "0" | [1-9] [0-9]*
STRING  ::= '"' ( character | escape )* '"'
CHAR    ::= "'" ( character | escape ) "'"
escape  ::= "\n" | "\t" | "\r" | "\0" | "\\" | "\"" | "\'" | "\u{" hex hex? hex? hex? hex? hex? "}"
OPERATOR ::= [!#$%&*+\-/<=>@^|~]+                            -- the longest run, never containing "--"
```

Keywords: `as break class continue do else export for foreign fun if import
in instance let loop match module return type var while`.

Comments: `--` to the end of the line, and `{- ... -}`, which nests.

## Modules

```ebnf
module      ::= export-decl? top-level*
export-decl ::= "export" "(" ".." ")"
              | "export" "(" (export ("," export)*)? ")"
modname     ::= CONID ("." CONID)*
export      ::= IDENT
              | CONID
              | CONID "(" ".." ")"
              | CONID "(" CONID ("," CONID)* ")"
              | "(" OPERATOR ")"
top-level   ::= import | type-decl | class-decl | instance-decl
              | fun-decl | let-decl | var-decl | foreign-decl | infix-decl
import      ::= "import" modname ("as" CONID)? import-list?
import-list ::= "(" (entry ("," entry)*)? ")"
entry       ::= item ("as" (IDENT | CONID | "_"))? | ".."
item        ::= IDENT | CONID | CONID "(" ".." ")" | CONID "(" CONID ("," CONID)* ")"
              | "(" OPERATOR ")"
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
type-decl    ::= "type" CONID IDENT* (":" derive ("," derive)*)? "=" type-rhs
derive       ::= "!"? qualified-CONID
type-rhs     ::= constructor ("|" constructor)*
               | type                                      -- alias
constructor  ::= CONID existential? payload?
existential  ::= "[" binder ("," binder)* "]"
binder       ::= IDENT | qualified-CONID atype
payload      ::= "(" type ("," type)* ")"
               | "{" (field (field-sep field)*)? "}"
field        ::= "var"? IDENT ":" type
```

Whether `type T = C args` is an alias or a data type is decided as described
in [Type aliases](types.md#type-aliases). An alias cannot have a derive list.

## Classes and instances

```ebnf
class-decl    ::= "class" CONID IDENT (":" superclass ("," superclass)*)?
                  "{" (class-item (separator class-item)*)? "}"
superclass    ::= qualified-CONID atype
                | type "~" type
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
let-decl  ::= "let" pattern "=" expression ("else" block)?
var-decl  ::= "var" pattern "=" expression ("else" block)?
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

A chain of binary operators is read flat and grouped by the operators'
precedence groups ([Operators](expressions.md#operators)). The `OPERATOR` of
a chain is any operator but `=`, `->`, `~` and `!`.

```ebnf
expression ::= chain (":" type)?
chain      ::= unary (OPERATOR unary)*
unary      ::= ("-" | "!") unary
             | postfix
postfix    ::= atom ( "(" (expression ("," expression)*)? ")"
                    | "[" expression "]"
                    | "." IDENT
                    | "." INDEX
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
       | "_"                                                     -- placeholder
       | "\" expression                                          -- placeholder function
       | if-expr
       | "while" conditions block
       | "for" pattern "in" condition block
       | "for" statement? ";" expression ";" statement? block
       | "loop" block
       | "match" condition "{" match-arm (separator match-arm)* "}"
       | "return" expression?
       | "break" expression?
       | "continue"

field-init ::= IDENT "=" expression
             | IDENT
if-expr    ::= "if" conditions block ("else" (block | if-expr))?
conditions ::= condition ("," condition)*                        -- no two expressions in a row
condition  ::= expression                                        -- no bare record expression
             | ("let" | "var") pattern "=" expression                -- only after "if" and "while"
match-arm  ::= pattern "->" expression
```

In a `condition`, and in the collection of `for ... in`, a record expression
must be parenthesized. `let` and `var` conditions, and lists of more than one
condition, are accepted only after `if` and `while`. A list of more than one
condition must contain a `let` or `var`, and two expressions cannot be next
to each other in it.

## Operator declarations

```ebnf
infix-decl ::= "infix" CONID "(" OPERATOR ")" "=" target
target     ::= IDENT | CONID ("." CONID)* "." IDENT
```

`infix` is not a reserved word; it begins an `infix-decl` only at the start of
a top-level item. The `CONID` is a precedence group, and the `OPERATOR` is
none of `&&`, `||`, `!`, `=`, `->` and `~` ([Declared
operators](expressions.md#declared-operators)).

## Patterns

```ebnf
pattern       ::= alternative ("|" alternative)*
alternative   ::= pattern-atom (":" type)?
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

## Foreign functions

```ebnf
foreign-decl ::= "foreign" foreign-sig body?
               | "foreign" STRING INT "{" foreign-sig (separator foreign-sig)* "}"
foreign-sig ::= STRING "fun" IDENT "(" foreign-params? ")" "->" type
foreign-params ::= foreign-param ("," foreign-param)* ("," "..." foreign-param ("," foreign-param)*)?
foreign-param ::= (IDENT ":")? type
body ::= "=" expression | block
```

The placement and signature restrictions are described under
[Foreign functions](declarations.md#foreign-functions).
