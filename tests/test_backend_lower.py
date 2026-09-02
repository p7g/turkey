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
