/*
postfix:    call, index, field, tuple-index
prefix:     - !
multiplicative: * /
additive:   + -
comparison: == != < <= > >=
logical:    &&
logical:    ||

postfix        left-associative
prefix         right-associative
* / + -        left-associative
comparisons    non-associative
&& ||          left-associative

type X = Something    // alias
type X = | Something  // single-variant type
 */
