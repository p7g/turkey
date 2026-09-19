"""The Python parser's projection nodes.

Internal: split from `test_projection.py`, whose behavioral tests run through
`boot`. This goes with the Python compiler (TIX-96).
"""

from __future__ import annotations


from turkey import ast
from turkey.parser import parse


def test_parser_distinguishes_numeric_projection_from_fields_and_floats():
    program = parse("fun f(x) = x.0.name.01\nfun n() = 1.25")
    body = program.decls[0].decl.body
    assert isinstance(body, ast.EProject) and body.index == 1
    assert isinstance(body.obj, ast.EField) and body.obj.name == "name"
    assert isinstance(body.obj.obj, ast.EProject) and body.obj.obj.index == 0
    assert isinstance(program.decls[1].decl.body, ast.ELit)


