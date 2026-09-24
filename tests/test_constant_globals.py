"""An atomic top-level binding is replaced by its value wherever it is read.

`tests/programs/constant_globals.opt` pins the monomorphic cases. The generic
case is here instead, because the program that shows it is polymorphically
recursive and its `.opt` dump is the specializer's whole unrolling -- thousands
of lines, of which the claim is two facts.
"""

from __future__ import annotations

import re

from tests import lang
from tests.test_programs import PROGRAMS_DIR

POLYREC = PROGRAMS_DIR / "constant_globals_polyrec.gob"


def _bindings(dump: str) -> dict[str, str]:
    """Each top-level binding's printed body, by the line that names it."""
    out: dict[str, str] = {}
    for block in dump.split("\n\n"):
        head, _, body = block.strip().partition("\n")
        if head and not head.startswith("--"):
            out[head] = body
    return out


def test_generic_constant_is_replaced_everywhere() -> None:
    result = lang.dump("opt", POLYREC)
    assert result.code == 0, result.stderr
    assert "Main#none" not in result.stdout


def test_generic_copy_reads_the_constant_at_its_own_type() -> None:
    """The copy the cap leaves generic returns `None` at `Option a`."""
    bindings = _bindings(lang.dump("opt", POLYREC).stdout)
    generic = [body for head, body in bindings.items()
               if re.match(r"Main#deepest@\[ptr\] : forall a\.", head)]
    assert len(generic) == 1, list(bindings)
    # As a value, not only as the pattern the recursive result is matched on.
    assert "(Data.Option.Type#None)" in generic[0]
