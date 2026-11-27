"""A method call on a value of a type internal to the library finds its
function in the one public library module that exports the type. The compiler
checks that against the modules a program loads; this checks it against the
whole library: no two public modules both have one of its types from an
internal module, or pass one to each other."""

from tests import bootc, lang

LIB = bootc.REPO_ROOT / "lib"


def _public_modules() -> list[str]:
    names = []
    for path in sorted(LIB.rglob("*.gob")):
        parts = path.relative_to(LIB).with_suffix("").parts
        if parts[0] in ("Turkey", "Prelude"):
            continue
        names.append(".".join(parts))
    return names


def test_every_internal_library_type_has_one_home() -> None:
    src = "".join(f"import {name} ()\n" for name in _public_modules())
    src += "\nfun main() {}\n"
    warnings = lang.check(src)
    assert "can have only one public module" not in warnings
