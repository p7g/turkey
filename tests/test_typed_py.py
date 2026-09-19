"""The Python class table's refusal to derive `Typed Prim.Ptr`.

Internal: split from `test_typed.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations


PROXIES = """
import Data.Typed (TypeRep(..), Proxy(..), describe)

type Box a = Box(a)

fun shown[Typed a](p : Proxy a) -> String = describe(typeRep(p))
fun repOf[Typed a](p : Proxy a) -> TypeRep = typeRep(p)

fun intP() -> Proxy Int = Proxy
fun strP() -> Proxy String = Proxy
fun boxIntP() -> Proxy (Box Int) = Proxy
"""


def test_a_raw_pointer_has_no_derived_rep():
    """`Typed Prim.Ptr` is refused, and that is what keeps a pointer out of an
    existential.

    A nullary `TCon` is otherwise derived without consulting its variants, so
    `Prim.Ptr` would have got one for free. It must not: `Typed` is what admits
    a value to packing and to `cast`, `addr` is not a packable layout, and a
    pointer recovered from a dynamic value is one the checker never vouched
    for. Excluding it is cheap now and impossible once a program depends on it.
    """
    from turkey import classes as classes_mod, decls as decls_mod
    from turkey.types import Pred

    table = decls_mod.DeclTable()
    classes = classes_mod.ClassTable(table)
    ptr = table.head("Prim.Ptr")
    assert classes.derive_typed(Pred(classes_mod.TYPED_CLASS, [ptr])) is None
    # And the exclusion is the pointer's, not the nullary branch's: `Int` is
    # registered the same way and does get one.
    integer = table.head("Int")
    assert classes.derive_typed(Pred(classes_mod.TYPED_CLASS, [integer])) is not None
