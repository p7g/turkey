"""Ties the stages together: parse, check, run."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from . import ast
from .builtins import initial_type_env, initial_values
from .constraints import Env, Solver
from .decls import DeclTable
from .deps import pattern_vars
from .errors import Unsupported
from .eval import Evaluator
from .infer import Generator
from .parser import parse
from .types import Scheme


@dataclass
class Checked:
    program: ast.Program
    ordered: list[ast.Stmt]
    decls: DeclTable
    env: Env
    signatures: list[tuple[str, Scheme]]  # in source order
    warnings: list[str]


def check(src: str) -> Checked:
    program = parse(src)
    if program.header is not None or program.imports:
        what = "module headers" if program.header is not None else "imports"
        span = program.header.span if program.header else program.imports[0].span
        raise Unsupported(
            f"{what} are not supported in v0 -- a program is a single file, and "
            f"Array, String and the other built-ins are already in scope "
            f"(see SPEC-DELTAS.md entry 9)",
            span,
        )

    decls = DeclTable()
    env = initial_type_env().child()

    # Generation builds the whole program's constraint and decides nothing;
    # solving is what assigns ranks, generalizes and fills in `env`. Splitting
    # them this way is the point of the HM(X) shape -- see constraints.py.
    generator = Generator(decls, env)
    ordered, constraint = generator.generate(program)
    Solver(decls, env).run(constraint)
    generator.check_exhaustiveness()

    signatures = []
    for item in program.decls:
        if isinstance(item, ast.TypeDecl):
            continue
        names = [item.decl.name] if isinstance(item, ast.SFun) else sorted(pattern_vars(item.pat))
        for name in names:
            binding = env.lookup(name)
            if binding is not None:
                signatures.append((name, binding.scheme))

    return Checked(program, ordered, decls, env, signatures, generator.warnings)


def run(src: str, filename: str = "<input>") -> None:
    checked = check(src)
    report_warnings(checked.warnings, filename)
    Evaluator(checked.decls, initial_values()).run(checked.ordered)


def report_warnings(warnings: list[str], filename: str) -> None:
    for warning in warnings:
        print(f"{filename}:{warning}", file=sys.stderr)
