"""Module interfaces govern names, constructors, and record-field evidence."""
import pytest
from tests import lang

OPAQUE = {
    "Tok.gob": """module Tok (Token, make, read)
type Token = Token { value : Int }
fun make(n : Int) -> Token = Token { value = n }
fun read(t : Token) -> Int = t.value
"""
}


@pytest.mark.parametrize("body", [
    "fun f() = make(1).value",
    "fun f() { let t = make(1); t.value = 2 }",
    "fun get(x) = x.value\nfun f() = get(make(1))",
])
def test_abstract_fields_require_visible_constructor(body):
    assert "field 'value' of abstract type 'Token'" in lang.fails(
        "import Tok\n" + body, OPAQUE)


def test_abstract_record_can_be_used_through_its_interface():
    assert lang.output("import Tok\nfun main() { print(read(make(7))) }", OPAQUE) == "7\n"


@pytest.mark.parametrize("selection", ["Token(..)", "Token(Token)"])
def test_hidden_constructor_import_is_rejected_even_if_type_has_same_name(selection):
    assert lang.fails(f"import Tok ({selection})", OPAQUE) == (
        "module 'Tok' does not export constructor 'Token' of type 'Token'")


@pytest.mark.parametrize("selection", ["A(B)", "A(C)"])
def test_constructor_import_belongs_to_requested_type(selection):
    modules = {"M.gob": "module M (A(..), B(..), C)\ntype A = A\ntype B = B\ntype C = C"}
    assert "does not export constructor" in lang.fails(f"import M ({selection})", modules)


def test_unrelated_constructor_cannot_be_exported_under_another_type():
    assert "'B' is not a constructor of 'A'" in lang.fails("import M", {
        "M.gob": "module M (A(B))\ntype A = A\ntype B = B"})


def test_hidden_constructor_cannot_be_reexported():
    assert "'Token' is not a constructor of 'Token'" in lang.fails("import Relay", {
        **OPAQUE, "Relay.gob": "module Relay (Token(..))\nimport Tok"})


PEERS = {name + ".gob": f'module {name} (f)\nfun f() -> String = "{name}"' for name in ("A", "B")}


@pytest.mark.parametrize("imports", ["import A\nimport B", "import B\nimport A"])
def test_ambiguous_import_is_reported_at_use(imports):
    assert lang.fails(imports + "\nfun main() { print(f()) }", PEERS) == (
        "ambiguous name 'f': 'A.f', 'B.f'")


def test_unused_ambiguity_and_qualified_access_are_legal():
    assert lang.output("import A\nimport B\nfun main() { print(A.f()); print(B.f()) }", PEERS) == "A\nB\n"


@pytest.mark.parametrize("body", [
    'fun f() -> String = "local"\nfun main() { print(f()) }',
    'fun main() { let f = fun() = "local"; print(f()) }',
])
def test_local_bindings_shadow_ambiguous_imports(body):
    assert lang.output("import A\nimport B\n" + body, PEERS) == "local\n"


def test_repeated_import_of_same_declaration_is_unambiguous():
    assert lang.output("import A\nimport Relay\nfun main() { print(f()) }", {
        **PEERS, "Relay.gob": "module Relay (f)\nimport A"}) == "A\n"


def test_ambiguous_name_cannot_be_reexported():
    assert "ambiguous name 'f'" in lang.fails("import Relay", {
        **PEERS, "Relay.gob": "module Relay (f)\nimport A\nimport B"})


@pytest.mark.parametrize("body", ["fun f(x : T) = x", "fun f() = C", "fun f(x) = match x { C -> 1 }"])
def test_type_and_constructor_names_have_ambiguity_checks(body):
    modules = {n + ".gob": f"module {n} (T(..))\ntype T = C" for n in ("A", "B")}
    assert "ambiguous name" in lang.fails("import A\nimport B\n" + body, modules)


def test_qualified_record_constructor_allows_field_access():
    modules = {"Tok.gob": OPAQUE["Tok.gob"].replace("(Token, make, read)", "(Token(..), make, read)")}
    assert lang.output("import Tok as T\nfun main() { let t = T.make(3); t.value = 4; print(t.value) }", modules) == "4\n"


def test_field_evidence_cannot_cross_module_boundary_to_reveal_abstract_record():
    assert "field 'value' of abstract type 'Token'" in lang.fails(
        "import Tok\nimport Access\nfun f() = get(make(1))",
        {**OPAQUE, "Access.gob": "module Access (get)\nfun get(x) = x.value"})


def test_class_names_are_ambiguous_independently_of_types():
    modules = {n + ".gob": f"module {n} (C(..))\nclass C a {{ fun get(a) -> Int }}" for n in ("A", "B")}
    assert "ambiguous name 'C'" in lang.fails(
        "import A\nimport B\nfun f[C a](x : a) = get(x)", modules)


def test_ambiguous_import_reports_the_use_location():
    with pytest.raises(lang.CompileError) as error:
        lang.check("import A\nimport B\nfun f2() = f()", PEERS)
    assert error.value.rendered.startswith("Main.gob:3:")


def test_importing_only_type_does_not_import_same_named_constructor():
    assert "unknown constructor 'Token'" in lang.fails(
        "import Tok (Token)\nfun f() = Token { value = 1 }", {
            "Tok.gob": OPAQUE["Tok.gob"].replace("(Token, make, read)", "(Token(..), make, read)")})


def test_reexport_cannot_substitute_same_named_constructor_of_another_type():
    modules = {
        "A.gob": "module A (T(..))\ntype T = C",
        "B.gob": "module B (U(..))\ntype U = C",
        "Relay.gob": "module Relay (T(..))\nimport A (T)\nimport B (U(..))",
    }
    assert "'C' is not a constructor of 'T'" in lang.fails("import Relay", modules)


@pytest.mark.parametrize("selection", ["T(C)", "C"])
def test_exported_constructor_can_be_imported_selectively(selection):
    assert lang.output(f"import M ({selection})\nfun main() {{ print(match C {{ C -> 7; _ -> 0 }}) }}", {
        "M.gob": "module M (T(C))\ntype T = C | D"}) == "7\n"


def test_selecting_only_record_type_withholds_field_access():
    modules = {"Tok.gob": OPAQUE["Tok.gob"].replace("(Token, make, read)", "(Token(..), make, read)")}
    assert "constructor 'Token' is not in scope" in lang.fails(
        "import Tok (Token, make)\nfun f() = make(1).value", modules)


@pytest.mark.parametrize("imports", ["import A as Q\nimport B as Q", "import B as Q\nimport A as Q"])
def test_field_visibility_does_not_depend_on_order_of_ambiguous_constructor_imports(imports):
    modules = {
        name + ".gob": f"module {name} (T(..), make{name})\n"
        f"type T = T {{ value : Int }}\nfun make{name}() -> T = T {{ value = 1 }}"
        for name in ("A", "B")
    }
    assert lang.output(imports + "\nimport A (makeA)\nimport B (makeB)\n"
                       "fun main() { print(makeA().value + makeB().value) }", modules) == "2\n"
