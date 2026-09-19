"""Solver-derived `Typed` instances (ERRORS.md step 5).

A type as a value, so that a packed payload can be asked what it is. This is
the half `cast` rests on: the rep is what a cast compares, so the two things
that matter are that a derived rep names the type exactly -- delta 43's
qualified constructor name, with its arguments -- and that no program can write
an instance of its own, because one that lied would make a cast return a value
of a type it is not.
"""

from __future__ import annotations


from tests import lang


def outputs(source: str) -> str:
    return lang.output(source)


def fails(source: str) -> str:
    return lang.fails(source)


PROXIES = """
import Data.Typed (TypeRep(..), Proxy(..), describe)

type Box a = Box(a)

fun shown[Typed a](p : Proxy a) -> String = describe(typeRep(p))
fun repOf[Typed a](p : Proxy a) -> TypeRep = typeRep(p)

fun intP() -> Proxy Int = Proxy
fun strP() -> Proxy String = Proxy
fun boxIntP() -> Proxy (Box Int) = Proxy
"""


def test_a_rep_names_the_qualified_constructor():
    """Delta 43 made the name unique, which is the whole basis for comparing
    two reps: a bare `Box` could be two different types from two modules."""
    source = PROXIES + """
fun main() {
    print(shown(intP()))
    print(shown(boxIntP()))
}
"""
    assert outputs(source) == "Int\nMain#Box Int\n"


def test_arguments_come_from_the_dictionaries_passed_in():
    """The instance head is general -- `Box a`, not `Box Int` -- so the rep of
    an argument cannot be known to it. It comes from the caller's dictionary,
    which is what makes one derived instance serve every use."""
    source = PROXIES + """
fun deepP() -> Proxy (Box (Box (Box Int))) = Proxy
fun main() { print(shown(deepP())) }
"""
    assert outputs(source) == "Main#Box (Main#Box (Main#Box Int))\n"


def test_a_tuple_carries_its_elements():
    """`spine` answers no arguments for a tuple, so a rep built off the spine
    would call every pair `Tuple2` and compare them all equal."""
    source = PROXIES + """
fun pairP() -> Proxy (Int, String) = Proxy
fun otherP() -> Proxy (String, Int) = Proxy
fun main() {
    print(shown(pairP()))
    print(repOf(pairP()) == repOf(otherP()))
}
"""
    assert outputs(source) == "Tuple2 Int Data.String.Type#String\nFalse\n"


def test_identity_is_structural():
    source = PROXIES + """
fun boxStrP() -> Proxy (Box String) = Proxy
fun main() {
    print(repOf(intP()) == repOf(intP()))
    print(repOf(intP()) == repOf(strP()))
    print(repOf(boxIntP()) == repOf(boxStrP()))
}
"""
    assert outputs(source) == "True\nFalse\nFalse\n"


def test_a_program_may_not_write_a_typed_instance():
    """The one class that is named but not instanced. A hand-written instance
    is the forged evidence a checked cast has no way to detect, because the
    comparison it performs *is* the check."""
    source = """
type Evil = Evil(Int)

instance Typed Evil { }

fun main() { print(1) }
"""
    message = fails(source)
    assert "derived by the compiler" in message
    assert "may not declare one" in message


def test_a_type_with_no_constructor_has_no_rep():
    """A derived instance reads its parameters off a constructor's scheme, so a
    type with none is refused rather than guessed at."""
    source = """
import Data.Typed (Proxy(..), describe)

fun shown[Typed a](p : Proxy a) -> String = describe(typeRep(p))
fun fnP() -> Proxy (fun(Int) -> Int) = Proxy
fun main() { print(shown(fnP())) }
"""
    assert "no instance for" in fails(source)


def test_a_packed_payload_can_be_asked_what_it_is():
    """Why `Typed` is a superclass of `Error`.

    The dictionary an existential packs is the `Error` one, and `Typed` rides
    inside it as a superclass field. So an arm that opens a `SomeError` can ask
    the payload for its rep without ever having seen the type -- which is the
    whole of what `cast` will do, minus the comparison.
    """
    source = """
import Data.Typed (Proxy(..), describe)

type ParseError = ParseError(String)
type IoError = IoError(Int)

instance Error ParseError { fun message(ParseError(s)) = "parse " + s }
instance Error IoError { fun message(IoError(n)) = "io " + Int.toString(n) }

fun proxyOf[Typed a](x : a) -> Proxy a = Proxy

fun payloadRep(e : SomeError) -> String = match e {
    SomeError { payload = p, cause = _ } -> describe(typeRep(proxyOf(p)))
}

fun main() {
    print(payloadRep(fail(ParseError("x"))))
    print(payloadRep(fail(IoError(2))))
}
"""
    assert outputs(source) == "Main#ParseError\nMain#IoError\n"

