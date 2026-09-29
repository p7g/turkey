"""`src/Turkey/Mono.gob`: which bindings are specialized, and where it stops.

Two things stop it, and nothing else: a binding on an expanding cycle -- one
whose specializations would never end -- is never copied, and the whole program
may gain at most a fixed multiple of its own size. These pin both, and that
neither refuses a copy an ordinary program asks for.
"""

import re

from tests import lang


def _copies(dump: str, name: str) -> int:
    """How many specialized copies of `Main#name` a `mono` dump defines."""
    return len(re.findall(rf"^Main#{name}@\S* :", dump, re.M))


def test_polymorphic_recursion_is_left_generic_and_nothing_else_is():
    # `depth` calls itself at a larger type, so its copies would never end;
    # `twice` is used at forty types, each of which gets its copy.
    count = 40
    lines = ["type Pair a = Pair(a, a)",
             "fun depth(x : a, n : Int) -> Int {",
             "    if n <= 0 { return 0 }",
             "    return 1 + depth(Pair(x, x), n - 1)",
             "}",
             "fun twice(x : a) -> (a, a) = (x, x)"]
    for i in range(count):
        lines.append(f"type T{i} = T{i} {{ x : Int }}")
    uses = [f"twice(T{i} {{ x = {i} }}).1.x" for i in range(count)]
    lines.append(f"fun main() {{ print(depth(1, 5) + {' + '.join(uses)}) }}")
    src = "\n".join(lines) + "\n"
    assert lang.output(src) == f"{5 + sum(range(count))}\n"
    result = lang.dump("mono", src)
    assert result.code == 0, result.stderr
    assert _copies(result.stdout, "depth") == 0, result.stdout
    assert _copies(result.stdout, "twice") >= count, result.stdout


def _exponential(levels: int) -> str:
    """Each function calling the next at two new types: `2^levels` types at
    the last, with no recursion anywhere. A `Weight` dictionary is built for
    each, so the copies past the ceiling take theirs as arguments."""
    lines = ["class Weight a {", "    fun weight(a) -> Int", "}",
             "instance Weight Int {", "    fun weight(x) = x", "}",
             "type L a = L(a)", "type R a = R(a)",
             "instance Weight (L a) : Weight a {",
             "    fun weight(v) = match v { L(y) -> weight(y) }", "}",
             "instance Weight (R a) : Weight a {",
             "    fun weight(v) = match v { R(y) -> weight(y) + 1 }", "}"]
    for k in range(levels):
        lines.append(f"fun f{k}[Weight a](x : a) -> Int = "
                     f"f{k + 1}(L(x)) + f{k + 1}(R(x))")
    lines.append(f"fun f{levels}[Weight a](x : a) -> Int = weight(x)")
    lines.append("fun main() { print(f0(0)) }")
    return "\n".join(lines) + "\n"


def test_an_exponential_program_stops_at_the_ceiling():
    # 2^13 types at the last level, far past the ceiling: the levels it reaches
    # are copied, the rest stay generic, and the answer is the same -- every
    # path's count of `R`s, summed.
    levels = 13
    src = _exponential(levels)
    assert lang.output(src) == f"{levels * 2 ** (levels - 1)}\n"
    result = lang.dump("mono", src)
    assert result.code == 0, result.stderr
    assert _copies(result.stdout, "f1") == 2, result.stdout
    assert _copies(result.stdout, f"f{levels}") < 2 ** levels, result.stdout
