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
