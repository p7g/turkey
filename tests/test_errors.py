"""The shared recoverable-error channel (SPEC-DELTAS 69).

`Error` is the class a payload satisfies, `SomeError` is the one existential
that carries any of them, and `fail` is where a concrete error enters. This is
the library half of ERRORS.md step 3: stack capture is not here, and none of
these tests assume an error knows where it came from.

The representation underneath is `tests/test_existentials.py`'s subject; what
this file pins is the channel -- that unrelated payload types travel together,
that `?` moves them without converting anything, and that context keeps its
cause.
"""

from __future__ import annotations

from tests import lang


def outputs(source: str) -> str:
    return lang.output(source)


def fails(source: str) -> str:
    return lang.fails(source)


PAYLOADS = """
type ParseError = ParseError(String)
type IoError = IoError(Int)

instance Error ParseError { fun message(ParseError(s)) = "parse " + s }
instance Error IoError { fun message(IoError(n)) = "io " + Int.toString(n) }
"""


def test_unrelated_payloads_travel_in_one_channel():
    """The point of the channel: two types with nothing in common, one list."""
    source = PAYLOADS + """
fun main() {
    for e in [fail(ParseError("eof")), fail(IoError(2))] {
        print(Error.messageOf(e))
    }
}
"""
    assert outputs(source) == "parse eof\nio 2\n"


def test_propagation_converts_nothing():
    """`?` moves a `SomeError` that was packed at the source. No wrapper sum,
    no conversion at the intermediate frames -- which is decision 2."""
    source = PAYLOADS + """
fun source(ok : Bool) -> Either SomeError Int =
    if ok { Right(1) } else { Left(fail(IoError(7))) }

fun middle(ok : Bool) -> Either SomeError Int {
    let n = source(ok)?
    Right(n + 1)
}

fun outer(ok : Bool) -> Either SomeError Int {
    let n = middle(ok)?
    Right(n + 1)
}

fun main() {
    for ok in [True, False] {
        match outer(ok) {
            Right(n) -> print(n)
            Left(e) -> print(Error.messageOf(e))
        }
    }
}
"""
    assert outputs(source) == "3\nio 7\n"


def test_context_keeps_the_cause_and_its_message():
    """GHC lost annotations exactly here, by repacking on a rethrow. `context`
    builds around the inner error instead, so every layer stays reachable."""
    source = PAYLOADS + """
fun main() {
    let inner = fail(IoError(5))
    let outer = Error.context(inner, ParseError("config"))
    print(Error.messageOf(outer))
    match Error.causeOf(outer) {
        None -> print("lost it")
        Some(found) -> print(Error.messageOf(found))
    }
    print(Error.describe(outer))
}
"""
    assert outputs(source) == "parse config\nio 5\nparse config: io 5\n"


def test_a_chain_is_walked_to_the_bottom():
    source = PAYLOADS + """
fun depth(e : SomeError) -> Int = match Error.causeOf(e) {
    None -> 1
    Some(inner) -> 1 + depth(inner)
}

fun main() {
    var e = fail(IoError(0))
    for n in [1, 2, 3] {
        e = Error.context(e, ParseError(Int.toString(n)))
    }
    print(depth(e))
    print(Error.describe(e))
}
"""
    assert outputs(source) == "4\nparse 3: parse 2: parse 1: io 0\n"


def test_showing_an_error_shows_the_whole_chain():
    source = PAYLOADS + """
fun main() {
    print(Error.context(fail(IoError(1)), ParseError("top")))
}
"""
    assert outputs(source) == "parse top: io 1\n"


def test_a_payload_must_implement_error():
    source = """
type Plain = Plain(Int)

fun main() { print(Error.messageOf(fail(Plain(1)))) }
"""
    assert "no instance for 'Error Plain'" in fails(source)


def test_a_packed_payload_is_read_by_opening_it_not_by_field():
    """Restriction 3. The field's type is the hidden one, so there is no type
    at which to read it -- and the message has to say that rather than claim
    `SomeError` is a multi-variant type, which it is not."""
    source = PAYLOADS + """
fun main() { print(fail(IoError(1)).payload) }
"""
    message = fails(source)
    assert "is existential" in message
    assert "only opening the value in a 'match' arm can name" in message


def test_the_channel_comes_from_the_prelude():
    """No import: `Error`, `SomeError` and `fail` are Prelude names, and the
    rest of the module is reached through the `Error` alias."""
    source = PAYLOADS + """
fun main() { print(Error.describe(fail(IoError(3)))) }
"""
    assert outputs(source) == "io 3\n"


CASTABLE = PAYLOADS + """
type Code = Code(Int)
instance Error Code { fun message(Code(n)) = "code " + Int.toString(n) }

fun asParse(e : SomeError) -> Option ParseError = Error.cast(e)
fun asIo(e : SomeError) -> Option IoError = Error.cast(e)
fun asCode(e : SomeError) -> Option Code = Error.cast(e)
"""


def test_a_payload_comes_back_as_the_type_it_is():
    source = CASTABLE + """
fun main() {
    match asParse(fail(ParseError("eof"))) {
        Some(ParseError(s)) -> print("parse " + s)
        None -> print("no")
    }
}
"""
    assert outputs(source) == "parse eof\n"


def test_a_payload_does_not_come_back_as_another_type():
    """The negative case is the one that matters: the packed rep and the
    wanted rep disagree, so nothing is converted."""
    source = CASTABLE + """
fun main() {
    match asIo(fail(ParseError("eof"))) {
        Some(IoError(n)) -> print(n)
        None -> print("not an io error")
    }
}
"""
    assert outputs(source) == "not an io error\n"


def test_a_scalar_payload_casts():
    """A single-field type is held as a scalar rather than a pointer, so this
    is the case where the packed layout and a pointer-shaped result differ --
    which is exactly the combination the rep check has to rule out."""
    source = CASTABLE + """
fun main() {
    match asCode(fail(Code(42))) {
        Some(Code(n)) -> print(n)
        None -> print("not a code")
    }
    match asParse(fail(Code(42))) {
        Some(ParseError(s)) -> print("wrong " + s)
        None -> print("not a parse error")
    }
}
"""
    assert outputs(source) == "42\nnot a parse error\n"


def test_cast_looks_at_the_outer_payload_only():
    """Not a chain search. Go's `errors.As` walks the cause chain; this does
    not, and the chain stays reachable through `causeOf` for a caller that
    wants it."""
    source = CASTABLE + """
fun main() {
    let wrapped = Error.context(fail(IoError(9)), Code(1))
    match asIo(wrapped) {
        Some(IoError(n)) -> print(n)
        None -> print("outer is not an io error")
    }
    match Error.causeOf(wrapped) {
        None -> print("no cause")
        Some(inner) -> match asIo(inner) {
            Some(IoError(n)) -> print(n)
            None -> print("cause is not an io error")
        }
    }
}
"""
    assert outputs(source) == "outer is not an io error\n9\n"


PARAMETERIZED = PAYLOADS + """
type Tagged a = Tagged(a)

instance Error (Tagged a) : Show a, Typed a {
    fun message(Tagged(x)) = "tagged " + show(x)
}

fun asTaggedInt(e : SomeError) -> Option (Tagged Int) = Error.cast(e)
fun asTaggedString(e : SomeError) -> Option (Tagged String) = Error.cast(e)
"""


def test_a_parameterized_payload_casts_at_its_argument():
    """`Tagged Int` and `Tagged String` are one constructor and one derived
    instance, whose head is general, so the argument's rep is the only thing
    telling the two apart -- and a cast that compared constructors alone would
    hand a `String` back as an `Int`."""
    source = PARAMETERIZED + """
fun main() {
    match asTaggedInt(fail(Tagged(7))) {
        Some(Tagged(n)) -> print(n + 1)
        None -> print("not a tagged int")
    }
    match asTaggedString(fail(Tagged(7))) {
        Some(Tagged(s)) -> print("wrong " + s)
        None -> print("not a tagged string")
    }
}
"""
    assert outputs(source) == "8\nnot a tagged string\n"


def test_promote_packs_once_and_keeps_the_payload_reachable():
    """`promote` is `Either.mapLeft` through `fail`, so it is the same entry to
    the channel a source would write by hand: one wrapper, no cause, and the
    concrete payload still there for `cast`."""
    source = PAYLOADS + """
fun openPort(n : Int) -> Either IoError Int =
    if n > 0 { Right(n) } else { Left(IoError(13)) }

fun dial(n : Int) -> Either SomeError Int {
    let port = Error.promote(openPort(n))?
    Right(port)
}

fun depth(e : SomeError) -> Int = match Error.causeOf(e) {
    None -> 1
    Some(inner) -> 1 + depth(inner)
}

fun main() {
    match dial(80) {
        Right(n) -> print(n)
        Left(e) -> print(Error.messageOf(e))
    }
    match dial(0) {
        Right(n) -> print(n)
        Left(e) -> {
            print(Error.messageOf(e))
            print(depth(e))
            match Error.cast(e) {
                Some(IoError(code)) -> print(code)
                None -> print("not an IoError")
            }
        }
    }
}
"""
    assert outputs(source) == "80\nio 13\n1\n13\n"


def test_promote_demands_an_error_payload():
    """The `[Error e]` context is what makes promotion safe to be generic: a
    left half that is not an error has nothing to pack."""
    message = fails(PAYLOADS + """
type Plain = Plain(Int)

fun main() {
    let x : Either Plain Int = Left(Plain(1))
    match Error.promote(x) {
        Right(n) -> print(n)
        Left(e) -> print(Error.messageOf(e))
    }
}
""")
    assert "Error Plain" in message


def test_bifunctor_reaches_the_left_that_functor_cannot():
    """`Functor (Either l)` fixes the left and varies the right. `bimap` is the
    one that reaches both, and `Data.Bifunctor.first` is the half of it this
    library had no name for.

    The Prelude does not re-export `Data.Bifunctor`, in either tier, so even
    the qualifier has to be imported."""
    source = """
import Data.Bifunctor as Bifunctor

fun main() {
    let bad : Either Int String = Left(3)
    let good : Either Int String = Right("ok")
    print(Bifunctor.bimap(bad, fun(n) = n + 1, String.byteLength))
    print(Bifunctor.bimap(good, fun(n) = n + 1, String.byteLength))
    print(Bifunctor.first(bad, fun(n) = n + 1))
    print(Either.mapLeft(bad, fun(n) = n + 1))
}
"""
    assert outputs(source) == "Left(4)\nRight(2)\nLeft(4)\nLeft(4)\n"


def test_second_agrees_with_map():
    """The law nothing checks. `Either` is both a `Functor` in its right half
    and a `Bifunctor`, and the two had better say the same thing."""
    source = """
import Data.Bifunctor as Bifunctor

fun main() {
    for e in [Left(1), Right(2)] {
        print(Bifunctor.second(e, fun(n) = n * 10) == map(e, fun(n) = n * 10))
    }
}
"""
    assert outputs(source) == "True\nTrue\n"
