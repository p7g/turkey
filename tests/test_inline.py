"""Whether a small callee is inlined does not depend on what anything is called.

A call's arguments are evaluated in the caller's scope and bound to the
callee's parameters all at once, but the inliner writes them down as a chain of
`let`s, one inside the next. The caller's locals and the callee's parameters
are both short names -- `x`, `y`, `i` -- so an argument that mentions a name
one of the callee's earlier parameters also has is the ordinary case, not a
coincidence: `x * x` is `mul(x := x, y := x)`, and `xs[i] = ys[i]` is
`set(xs, i := i, x := ys[i])`.

Each program below is checked twice: its `.opt` dump has no call left to the
small function, and it prints what it should, since inlining that binds a name
wrongly is a miscompile that still inlines.
"""

from __future__ import annotations

import pytest

from tests import lang


def _body(dump: str, name: str) -> str:
    """The printed body of the top-level binding `name`: the lines from its
    head, `name : type =`, to the next head. A pattern match printed inside an
    expression can start a line at the margin too, so the margin alone does
    not end a body."""
    def is_head(line: str) -> bool:
        return (bool(line) and not line[0].isspace()
                and " : " in line and line.endswith("="))

    lines = dump.splitlines()
    for at, line in enumerate(lines):
        if line.startswith(name + " :"):
            body = []
            for inner in lines[at + 1:]:
                if is_head(inner):
                    break
                body.append(inner)
            return "\n".join(body)
    raise AssertionError(f"{name} is not in the dump:\n{dump}")


VEC3 = """
type Vec3 = Vec3(Float, Float, Float)
fun f(Vec3({binders})) = {body}
fun main() {{ print(f(Vec3(1.0, 2.0, 3.0))) }}
"""

# `Float`'s `mul` is `fun mul(x, y)` and its `add` is `fun add(x, y)`.
@pytest.mark.parametrize("binders,body,answer", [
    ("x, y, z", "x*y", "2.0"),
    ("x, y, z", "x*x", "1.0"),
    ("x, y, z", "x*x + y*y + z*z", "14.0"),
    ("x, y, z", "z*z + y*y + x*x", "14.0"),
    ("a, b, c", "a*a + b*b + c*c", "14.0"),
    ("y, x, z", "y*x", "2.0"),
], ids=["x*y", "x*x", "xyz", "zyx", "abc", "swapped"])
def test_an_operator_on_its_own_parameter_names_is_inlined(binders, body, answer):
    src = VEC3.format(binders=binders, body=body)
    dump = lang.dump("opt", src)
    assert dump.code == 0, dump.stderr
    f = _body(dump.stdout, "Main#f")
    assert "%inst." not in f, f
    assert lang.output(src) == answer + "\n"


COPY = """
fun copy(xs : Array Int, ys : Array Int) {
    for var i = 0; i < len(xs); i = i + 1 { xs[i] = ys[i] }
}
fun main() {
    let xs = [0, 0, 0]
    copy(xs, [7, 8, 9])
    print(xs)
}
"""

FILL = """
fun fill(a : Array Int) {
    for var i = 0; i < len(a); i = i + 1 { a[i] = i }
}
fun main() {
    let a = [5, 5, 5]
    fill(a)
    print(a)
}
"""


@pytest.mark.parametrize("src,name,answer", [
    (COPY, "Main#copy", "[7, 8, 9]"),
    (FILL, "Main#fill", "[0, 1, 2]"),
], ids=["xs[i] = ys[i]", "a[i] = i"])
def test_an_index_named_like_sets_own_is_inlined(src, name, answer):
    """`Index (Array a)`'s `set(xs, i, x)`, given a loop counter called `i`.

    The counter is a `var`, so each argument reads it, and neither is a value
    that could simply be substituted: `x`'s argument mentions the caller's `i`
    while the chain has already bound the callee's.
    """
    dump = lang.dump("opt", src)
    assert dump.code == 0, dump.stderr
    body = _body(dump.stdout, name)
    assert "%inst." not in body, body
    assert lang.output(src) == answer + "\n"


SWAP = """
fun pair(a : Int, b : Int) -> Int = a * 10 + b
fun swapper(a : Int, b : Int) -> Int = pair(b + 1, a + 2)
fun main() { print(swapper(1, 5)) }
"""


def test_arguments_that_mention_each_others_parameters_are_inlined():
    """`pair(a := b + 1, b := a + 2)`: the second argument reads the caller's
    `a`, which `let a = b + 1` would hide. 63 is `pair(6, 3)`; reading the
    callee's `a` instead would print 68."""
    dump = lang.dump("opt", SWAP)
    assert dump.code == 0, dump.stderr
    assert "Main#pair(" not in _body(dump.stdout, "Main#swapper")
    assert lang.output(SWAP) == "63\n"
