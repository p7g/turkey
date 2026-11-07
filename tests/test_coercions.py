"""Equality evidence costs nothing at run time.

An equality in a context, `Elem b ~ Int`, is passed in Core as a coercion,
which is erased before the optimizer runs. The low IR shows that none is left:
no parameter of type `unit` where one was, no curried layer that returns a
closure, and a method slot whose machine type is the method's own.
"""

import re

from tests import lang


def _functions(ssa: str, prefix: str) -> list[str]:
    found = re.findall(rf"^fun @{re.escape(prefix)}.*?^}}$", ssa, re.M | re.S)
    assert found, f"no function {prefix} in the dump"
    return found


def _ssa(src: str) -> str:
    result = lang.dump("ssa", src)
    assert result.code == 0, result.stderr
    return result.stdout


# Polymorphic recursion: `deep` is called at `Option b` from inside itself, so
# it is never specialized by type and each copy is generic.
RECURSIVE = """
class Box b {
    type Elem b
}

instance Box Int {
    type Elem = Int
}

instance Box (Option a) {
    type Elem = Elem a
}

fun deep[Elem b ~ Int](n : Int, y : Elem b, o : b) -> Int =
    if n == 0 { y + 1 } else { deep(n - 1, y + n, Some(o)) }

fun main() {
    print(deep(3, 10, 0))
    print(deep(4, 10, Some(0)))
}
"""


def test_a_coercion_takes_no_parameter_in_a_generic_copy():
    assert lang.output(RECURSIVE) == "17\n21\n"
    ssa = _ssa(RECURSIVE)
    copies = _functions(ssa, "Main#deep@")
    for body in copies:
        assert ":unit" not in body.splitlines()[0], body
    # The environment, then `n`, `y` and `o`, and nothing between them.
    assert copies[0].startswith("fun @Main#deep@[i64,i64](%0:ptr*, %1:i64, %2:i64, %3:i64)"), \
        copies[0]


# A local function with only an equality in its context, and a method with
# only one, called through a dictionary.
CALLED = """
class Box b {
    type Elem b
    fun code[Elem b ~ Int](b) -> Int
}

instance Box Int {
    type Elem = Int
    fun code(x) = x
}

instance Box (Option a) : Box a {
    type Elem = Elem a
    fun code(o) = match o {
        Some(x) -> code(x) + 1
        None -> 0
    }
}

fun deep[Box b, Elem b ~ Int](n : Int, o : b) -> Int =
    if n == 0 { code(o) } else { deep(n - 1, Some(o)) }

fun main() {
    var k = 0
    fun inner[Elem b ~ Int](y : Elem b, o : b) -> Int = y + k
    k = 5
    var t = 0
    for i in [0, 1, 2] { t = t + inner(i, Some(1)) }
    print(t)
    print(deep(3, 10))
}
"""


def test_a_coercion_adds_no_curried_layer():
    assert lang.output(CALLED) == "18\n13\n"
    ssa = _ssa(CALLED)
    for body in _functions(ssa, "Main#main"):
        assert "closure.new" not in body, body
    # The method's slot answers its `Int`, not a closure that would.
    code = _functions(ssa, "%inst.Main#Box.Int#code")
    assert code[0].startswith("fun @%inst.Main#Box.Int#code(%0:ptr*, %1:i64) -> i64"), code[0]
    for body in _functions(ssa, "%inst.Main#Box.Data.Option#Option#code"):
        assert ":unit" not in body.splitlines()[0], body


# A default method with an equality in its context, used at a known instance
# and through a dictionary that is a parameter.
DEFAULTED = """
class Box b {
    type Elem b
    fun open(b) -> Elem b
    fun code[Elem b ~ Int](x : b) -> Int = open(x) * 2
}

instance Box Int {
    type Elem = Int
    fun open(x) = x
}

instance Box (Option a) : Box a {
    type Elem = Elem a
    fun open(o) = match o {
        Some(x) -> open(x)
        None -> error("empty")
    }
}

fun deep[Box b, Elem b ~ Int](n : Int, o : b) -> Int {
    var t = 0
    for i in [1, 2, 3] { t = t + code(o) }
    if n == 0 { t } else { t + deep(n - 1, Some(o)) }
}

fun main() {
    var t = 0
    for i in [1, 2, 3] { t = t + code(i) }
    print(t)
    print(deep(5, 1))
}
"""


def _allocations(body: str) -> int:
    return len(re.findall(r"object\.new|closure\.new|stack\.closure|stack\.object", body))


def test_a_default_method_s_coercion_costs_nothing():
    assert lang.output(DEFAULTED) == "12\n36\n"
    ssa = _ssa(DEFAULTED)
    # At a known instance the default is inlined into the loop, as one without
    # the equality is.
    main = _functions(ssa, "Main#main")[0]
    assert "mul" in main and "#code" not in main, main
    # Through a dictionary, it costs what the same default without the
    # equality does.
    plain = DEFAULTED.replace("fun code[Elem b ~ Int](x : b) -> Int", "fun code(x : b) -> Int") \
                     .replace("fun open(b) -> Elem b", "fun open(b) -> Int") \
                     .replace("fun deep[Box b, Elem b ~ Int]", "fun deep[Box b]")
    assert lang.output(plain) == "12\n36\n"
    without = _ssa(plain)
    prefix = "%inst.Main#Box.Data.Option#Option#code@"
    assert [_allocations(f) for f in _functions(ssa, prefix)] == \
        [_allocations(f) for f in _functions(without, prefix)]
