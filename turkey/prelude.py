"""The compiler's half of the prelude.

The prelude itself is ordinary source and lives in `turkey/lib/Prelude.tl`,
loaded like any other module (M11a). What stays here is the handful of names
the *compiler* has to know: which method an operator desugars to, and what a
`for` loop is written in terms of. Those are not source, and a program cannot
change them by shadowing a name -- see `turkey/resolve.py` for why the
desugared node is marked rather than looked up.
"""

from __future__ import annotations


# Operator -> the method it desugars to. `&&`, `||`, `!` and `++` are absent
# deliberately: the first two short-circuit, which no function call does, and
# `++` is concatenation on `String` and has no class to belong to yet.
BINARY_METHOD: dict[str, str] = {
    "+": "add", "-": "sub", "*": "mul", "/": "div", "%": "rem",
    "==": "eq", "!=": "ne",
    "<": "lt", "<=": "lte", ">": "gt", ">=": "gte",
}

UNARY_METHOD: dict[str, str] = {"-": "neg"}

# What `for x in xs` is written in terms of: a cursor from `iter`, advanced by
# `next` until it answers `None`. The two constructor names are here because
# the evaluator has to recognize the answer, and one place should say so.
# What `?` is written in terms of (delta 46). `bind` is the method it desugars
# to; `pure` is the one the lowering needs on its own, to lift a branch that was
# never monadic into the monad the rest of the block is in. Both are marked
# method references, so a program that defines its own `bind` does not capture
# the one a `?` means -- the same reason `BINARY_METHOD` is a table here rather
# than a lookup.
MONAD_CLASS = "Monad"
MONAD_BIND = "bind"
MONAD_PURE = "pure"

# What a `return`, `break` or `continue` becomes when it crosses a `?` (delta
# 47). Declared in the Prelude and exported by nothing, like `ArrayCursor`, so
# only the lowering can write one.
FLOW = "Prelude#Flow"
FLOW_FALL = "Prelude#Fall"
FLOW_BRK = "Prelude#Brk"
FLOW_CONT = "Prelude#Cont"
FLOW_RET = "Prelude#Ret"

# `error` diverges, so it can stand in for a `Flow` arm the language's own rules
# make unreachable -- a `break` where there is no loop -- which the exhaustive-
# ness checker has no way to know is unreachable.
ERROR = "Prelude#error"

ITER_CLASS = "Iterator"
ITER_ITEM = "Item"
ITER_CURSOR = "Cursor"
ITER_ITER = "iter"
ITER_NEXT = "next"

# Internal names (`turkey/modules.py`): `Option` and `Bool` are declared by
# library modules now, so the compiler names them the way resolution does.
OPTION = "Data.Option#Option"
OPTION_NONE = "Data.Option#None"
OPTION_SOME = "Data.Option#Some"

BOOL_TRUE = "Data.Bool#True"
BOOL_FALSE = "Data.Bool#False"
