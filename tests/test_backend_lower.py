from turkey.backend_ir import format_module
from turkey.backend_lower import lower
from turkey.driver import check


def test_scalar_program_lowers_to_checked_cfg():
    checked = check("fun main() { print(1 + 2) }")
    text = format_module(lower(checked.opt, checked.decls, checked.main))
    assert "prim.intAdd" in text
    assert "prim.intToString" in text
    assert "prim.print" in text


def test_recursive_core_join_lowers_to_a_back_edge():
    checked = check("""
fun count(n : Int) -> Int {
    var i = 0
    while i < n { i = i + 1 }
    i
}
fun main() { print(count(10)) }
""")
    text = format_module(lower(checked.opt, checked.decls, checked.main))
    assert "join" in text
    # A Core join is represented by an ordinary branch edge, not a call.
    assert any("jump " in line and "block_join" in line
               for line in text.splitlines())


def test_two_names_never_become_one_symbol():
    """`mangle` is injective, and the escape is why it has to be spelled so.

    An underscore doubles rather than passing through. Left alone, `_25_` in a
    symbol could have come from a `%` in the name or from those four
    characters appearing in it, and a lifted lambda is named after the binding
    it came out of: `Main#f`'s first lambda was `turkeyfn_Main_23_f_lambda_0`,
    and so was a Turkey function honestly called `f_lambda_0`. Two definitions
    claiming one symbol, reported by llvmlite as a `DuplicatedNameError` from
    somewhere that names neither.
    """
    from turkey.backend_lower import mangle

    names = [
        "Main#f", "Main#f_lambda_0", "Main#f%lambda0", "Main#f_25_lambda0",
        "a_b", "a__b", "a_2b", "a", "_", "__", "Main#f_", "x_23_y", "x#y",
        "%inst.Std.Classes#Eq.Int", "Data.Array#outOfBounds",
    ]
    symbols = {}
    for name in names:
        symbol = mangle(name)
        assert symbol not in symbols, (
            f"{name!r} and {symbols[symbol]!r} both mangle to {symbol}")
        symbols[symbol] = name


def test_a_lifted_lambda_cannot_be_named_by_a_turkey_program():
    """The lifted name carries `%`, which the lexer will not accept.

    So the symbol it mangles to is one no Turkey binding can reach, whatever
    it is called -- which is the property, rather than the weaker one that
    nothing in the suite happens to collide today.
    """
    from turkey.backend_lower import mangle
    from turkey.errors import TurkeyError

    lifted = mangle("Main#f") + "_25_lambda0"
    assert lifted != mangle("Main#f_lambda_0")
    assert lifted != mangle("Main#f_25_lambda0")

    # And `%` really is unwritable, so nothing can mangle into that shape.
    try:
        check("fun f%lambda0() { print(1) }")
    except TurkeyError:
        pass
    else:
        raise AssertionError("'%' should not be accepted in an identifier")
