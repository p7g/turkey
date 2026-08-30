"""A `fun` with a complete annotation states its type (SPEC-DELTAS.md 38).

Two things follow, and they are the two halves of this file. The declared type
is *checked*, so a body can no longer narrow it to fit -- the defect delta 13
recorded and deferred. And it is *known before the body is solved*, so a
recursive occurrence instantiates it instead of sharing one monomorphic
placeholder, which is polymorphic recursion.

Inference is untouched. A `fun` that does not state its type completely takes
exactly the path it took before, which is what the last section here pins.
"""

import pytest

from turkey.driver import check, run
from turkey.errors import TurkeyError
from turkey.types import show_scheme

#: The message a skolem escape raises (delta 40), quoted rather than matched
#: on a fragment: it is the whole of what a reader gets, so a test that
#: accepted half of it would not be pinning the diagnostic.
ESCAPES = (
    "the type 'a' cannot escape the signature that declares it: it stands "
    "for whatever a caller of that function chooses, so nothing outside can "
    "be equal to it"
)

ITER = """
type Two a = Two { fst : a, snd : a }
type TwoCur = TwoCur { taken : Int }

instance Iterator (Two a) {
    type Item = a
    type Cursor = TwoCur

    fun iter(t) = TwoCur { taken = 0 }

    fun next(t, cur) {
        let k = cur.taken
        cur.taken = k + 1
        if k == 0 { return Some(t.fst) }
        if k == 1 { return Some(t.snd) }
        None
    }
}
"""


def output(src: str, capsys) -> list[str]:
    run(src)
    return capsys.readouterr().out.splitlines()


def fails(src: str) -> str:
    with pytest.raises(TurkeyError) as exc:
        check(src)
    return exc.value.message


def scheme(src: str, name: str) -> str:
    checked = check(src)
    return next(show_scheme(s) for n, s in checked.signatures if n == name)


# -- a declared type is kept --------------------------------------------------


def test_a_recursive_call_no_longer_rewrites_the_declared_type():
    """The delta 13 regression, in the direction delta 13 did not record.

    Before delta 38 this reported `fun(Array a) -> Int`: the recursive call
    unified the annotation's own variable with `Array _`, and no error said so.
    """
    src = """
    fun g[Iterator a](xs : a) -> Int {
        var n = 0
        for x in xs {
            n = n + 1
            if n < 0 { n = n + g([1, 2]) }
        }
        n
    }
    """
    assert scheme(src, "g") == "[Iterator a] fun(a) -> Int"


def test_a_declared_type_is_reported_as_written():
    src = "fun id(x : a) -> a = x"
    assert scheme(src, "id") == "fun(a) -> a"


def test_a_body_less_general_than_its_signature_is_rejected():
    """`xs[0]` needs an `Array`, but the signature promised any `a`."""
    assert "found Array" in fails("fun first(xs : a) -> Int = xs[0]")


def test_a_signature_may_not_narrow_its_return_type():
    assert fails("fun f(x : Int) -> a = x") == "expected a, found Int in the function body"


# -- polymorphic recursion ----------------------------------------------------


def test_a_signature_admits_recursion_at_another_instantiation(capsys):
    """Undecidable to infer, ordinary to check -- so it is allowed once stated."""
    src = ITER + """
    fun size[Iterator a](xs : a) -> Int {
        var n = 0
        for x in xs { n = n + 1 }
        n
    }
    fun both[Iterator a](xs : a) -> Int = size(xs) + size(Two { fst = 1, snd = 2 })
    fun main() { print(Int.toString(both([7, 8, 9]))) }
    """
    assert scheme(src, "both") == "[Iterator a] fun(a) -> Int"
    assert output(src, capsys) == ["5"]


def test_recursion_at_another_instantiation_still_passes_its_dictionary(capsys):
    """The elaboration half: the recursive use needs its *own* dictionary."""
    src = ITER + """
    fun count[Iterator a](xs : a) -> Int {
        var n = 0
        for x in xs { n = n + 1 }
        if n == 3 { return n + count(Two { fst = 1, snd = 2 }) }
        n
    }
    fun main() { print(Int.toString(count([1, 2, 3]))) }
    """
    assert output(src, capsys) == ["5"]


def test_inference_still_refuses_polymorphic_recursion():
    """Without a signature the placeholder is monomorphic, as it must be."""
    src = """
    fun count(xs) {
        var n = 0
        for x in xs { n = n + 1 }
        if n == 0 { return count([1, 2]) }
        n
    }
    """
    assert scheme(src, "count") == (
        "[OneOf b {Int, Float}, OneOf a {Int, Float}, Add b, Eq b] "
        "fun(Array a) -> b"
    )


# -- the context a signature declares -----------------------------------------


def test_a_declared_context_is_a_fact_the_body_may_use():
    src = """
    fun size[Iterator a](xs : a) -> Int {
        var n = 0
        for x in xs { n = n + 1 }
        n
    }
    """
    assert scheme(src, "size") == "[Iterator a] fun(a) -> Int"


def test_a_context_the_signature_omits_is_not_granted():
    """A stated type is the whole of the type, contexts included."""
    src = """
    fun size(xs : a) -> Int {
        var n = 0
        for x in xs { n = n + 1 }
        n
    }
    """
    assert fails(src) == "no instance for 'Iterator a'"


def test_a_context_over_a_variable_the_type_omits_is_rejected_where_written():
    src = "fun f[Iterator b](x : Int) -> Int = x"
    assert fails(src) == (
        "'Iterator b' constrains a type that the declared type of 'f' does "
        "not mention, so no use of 'f' could decide it"
    )


def test_a_context_reaching_the_type_through_a_family_is_accepted():
    """`Show (Item a)` mentions no variable of the type but reaches one."""
    src = """
    fun describe[Iterator a, Show (Item a)](xs : a) -> Int {
        var n = 0
        for x in xs { n = n + String.length(show(x)) }
        n
    }
    """
    assert scheme(src, "describe") == "[Iterator a, Show (Item a)] fun(a) -> Int"


# -- what counts as a signature -----------------------------------------------


def test_an_omitted_return_type_leaves_the_function_inferred():
    """One missing piece and the whole annotation is soft again."""
    src = """
    fun size(xs : a) {
        var n = 0
        for x in xs { n = n + 1 }
        n
    }
    """
    assert scheme(src, "size") == \
        "[OneOf b {Int, Float}, Iterator a, Add b] fun(a) -> b"


def test_an_unannotated_parameter_leaves_the_function_inferred():
    src = """
    fun size(xs, extra : Int) -> Int {
        var n = extra
        for x in xs { n = n + 1 }
        n
    }
    """
    assert scheme(src, "size") == "[Iterator a] fun(a, Int) -> Int"


def test_an_unannotated_function_is_unchanged():
    assert scheme("fun p(x) { print(x) }", "p") == "[Show a] fun(a) -> Unit"


# -- groups mixing the two ----------------------------------------------------


def test_an_inferred_caller_instantiates_an_annotated_callee(capsys):
    """The ordinary mixture: two groups, and the scheme crosses between them.

    `other` uses `size` at two element types in one body, which is only
    possible because `size` is bound to its declared scheme rather than to a
    placeholder shared with its caller.
    """
    src = ITER + """
    fun size[Iterator a](xs : a) -> Int {
        var n = 0
        for x in xs { n = n + 1 }
        n
    }
    fun other(xs) = size(xs) + size(Two { fst = 1, snd = 2 })
    fun main() { print(Int.toString(other([1, 2, 3]))) }
    """
    assert scheme(src, "size") == "[Iterator a] fun(a) -> Int"
    assert scheme(src, "other") == "[Iterator a] fun(a) -> Int"
    assert output(src, capsys) == ["5"]


def test_mutual_recursion_across_the_two_paths_reports_the_escape():
    """Rejected, and now for the reason it is actually rejected -- delta 40.

    `size` states its type, so its parameter is a skolem; `other` shares its
    SCC and is inferred, so it is checked against one monomorphic placeholder,
    which the skolem flows into. That is a real error rather than a gap:
    `other` is used at two element types, so it would have to be polymorphic,
    and a member the group infers cannot be -- polymorphic recursion again,
    undecidable in inference. The remedy is a signature on `other` too, which
    is what the message points at and what Haskell asks for here.
    """
    src = ITER + """
    fun size[Iterator a](xs : a) -> Int {
        var n = 0
        for x in xs { n = n + 1 }
        if n == 99 { return other(xs) }
        n
    }
    fun other(xs) = size(xs) + size(Two { fst = 1, snd = 2 })
    """
    assert fails(src) == ESCAPES


def test_mutual_recursion_checks_when_both_members_state_their_types(capsys):
    """And with the signature that message asks for, the same group goes
    through: every recursive occurrence instantiates a declared scheme."""
    src = ITER + """
    fun size[Iterator a](xs : a) -> Int {
        var n = 0
        for x in xs { n = n + 1 }
        if n == 99 { return other(xs) }
        n
    }
    fun other[Iterator b](xs : b) -> Int =
        size(xs) + size(Two { fst = 1, snd = 2 })
    fun main() { print(Int.toString(other([1, 2, 3]))) }
    """
    assert scheme(src, "size") == "[Iterator a] fun(a) -> Int"
    assert scheme(src, "other") == "[Iterator a] fun(a) -> Int"
    assert output(src, capsys) == ["5"]


# -- rigidity is not a leak ---------------------------------------------------


def test_a_call_is_not_an_escape():
    """The ordinary route out of a signature, which is no leak at all: the
    scheme is the declared one and quantifies `a`, so a use site instantiates
    it and nothing rigid crosses the boundary."""
    src = """
    fun f(x : a) -> a = x
    fun g() -> Int = f(1)
    """
    assert scheme(src, "g") == "fun() -> Int"


def test_a_skolem_cannot_be_stored_in_an_enclosing_binding():
    """The escape that would make a checked signature unsound -- delta 40.

    `f` promises to work for whatever type a caller picks, so inside its body
    `a` is a constant nobody outside has seen. Letting it fix the element type
    of `cell`, which was born outside, would record an equation between a real
    type and one that has no values yet -- and `f(3)` and `f("x")` would then
    both be well typed and both push into the same array.
    """
    src = """
    fun main() {
        let cell = []
        fun f(x : a) -> Int { Array.push(cell, x); 1 }
        print(Int.toString(f(3)))
    }
    """
    assert fails(src) == ESCAPES


def test_a_skolem_may_move_within_the_body_it_belongs_to():
    """The other half, and the case a blunter check would break: rigidity is
    not a ban on the skolem *moving*, only on its being equated with something
    older. Collecting it, handing it to a polymorphic function and returning
    it where the signature wrote `a` are all fine.
    """
    src = """
    fun id2(x) = x
    fun f(x : a, y : a) -> a { let both = [x, y]; id2(both[0]) }
    fun main() { print(f("l", "r")) }
    """
    assert scheme(src, "f") == "fun(a, a) -> a"
