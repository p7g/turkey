"""Ties the stages together: parse, check, run."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from . import ast
from .builtins import initial_type_env, initial_values
from .classes import ClassTable
from .constraints import Binding, Env, Solver
from .decls import DeclTable
from .deps import pattern_vars
from .errors import Unsupported
from .eval import Evaluator
from .evidence import Elaborator
from .infer import Generator
from .parser import parse
from .prelude import SOURCE as PRELUDE_SOURCE
from .types import Scheme


@dataclass
class Checked:
    program: ast.Program
    ordered: list[ast.Stmt]
    prelude_ordered: list[ast.Stmt]
    decls: DeclTable
    classes: ClassTable
    env: Env
    signatures: list[tuple[str, Scheme]]  # in source order
    warnings: list[str]


def prelude() -> tuple[DeclTable, ClassTable, list[ast.Stmt], dict[str, Binding]]:
    """Check the prelude, and hand back what a program starts from.

    It is checked, not trusted: `class Add a` and `instance Add Int` go through
    exactly the machinery a user's would, which is the point -- an operator is
    a class method and nothing about it is privileged.

    Three things come out and one deliberately does not. The declarations, the
    classes and the prelude's own top-level bindings are what a program starts
    with; its *environment* is not, which is what keeps `Prim.*` -- the machine
    operations the instances are written in terms of -- out of the surface
    language. `env.names` is exactly the prelude's own scope, so `print` and
    `write` are exported and `Prim.print` is not.

    Rebuilt per check rather than cached, because the tables are mutable and
    the program about to be checked will add to them.
    """
    decls = DeclTable()
    # `Prim.*` is in scope here and only here (see turkey/builtins.py).
    env = initial_type_env(prims=True).child()
    generator = Generator(decls, env)
    ordered, constraint = generator.generate(parse(PRELUDE_SOURCE))
    solver = Solver(decls, env, generator.classes)
    solver.run(constraint)
    Elaborator(generator.classes).run(solver.uses)
    exports = {}
    for item in ordered:
        names = ([item.decl.name] if isinstance(item, ast.SFun)
                 else sorted(pattern_vars(item.pat)))
        for name in names:
            binding = env.lookup(name)
            if binding is not None:
                exports[name] = binding
    return decls, generator.classes, ordered, exports


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

    decls, classes, prelude_ordered, exports = prelude()
    # A sibling of the prelude's scope rather than a child: the declarations
    # and classes are shared, and so is what the prelude itself defined, but
    # the primitives those definitions are written in terms of are not.
    outer = initial_type_env()
    for name, binding in exports.items():
        outer.define(name, binding)
    env = outer.child()

    # Generation builds the whole program's constraint and decides nothing;
    # solving is what assigns ranks, generalizes and fills in `env`. Splitting
    # them this way is the point of the HM(X) shape -- see constraints.py.
    generator = Generator(decls, env, classes)
    ordered, constraint = generator.generate(program)
    solver = Solver(decls, env, generator.classes)
    solver.run(constraint)
    generator.check_exhaustiveness()
    # Elaboration is last because it is the only stage that needs the answers:
    # which instance covers `Semigroup a` is not a question until the solver
    # has decided what `a` is. See turkey/evidence.py.
    Elaborator(generator.classes).run(solver.uses)

    signatures = []
    for item in program.decls:
        if isinstance(item, ast.ClassDecl):
            # A method's scheme is stated by its class rather than inferred, but
            # it is still the thing a reader wants to see, and the class
            # variable's discovered kind shows up nowhere else.
            info = generator.classes.classes[item.name]
            signatures.extend((m.name, info.methods[m.name].scheme) for m in item.methods)
            continue
        if not isinstance(item, ast.Stmt):
            continue
        names = [item.decl.name] if isinstance(item, ast.SFun) else sorted(pattern_vars(item.pat))
        for name in names:
            binding = env.lookup(name)
            if binding is not None:
                signatures.append((name, binding.scheme))

    return Checked(program, ordered, prelude_ordered, decls, generator.classes, env, signatures,
                   generator.warnings)


def run(src: str, filename: str = "<input>") -> None:
    checked = check(src)
    report_warnings(checked.warnings, filename)
    # The prelude's bindings first: `print` is one of them now.
    Evaluator(checked.decls, initial_values()).run(
        checked.prelude_ordered + checked.ordered)


def report_warnings(warnings: list[str], filename: str) -> None:
    for warning in warnings:
        print(f"{filename}:{warning}", file=sys.stderr)
