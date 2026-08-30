"""Ties the stages together: load, resolve, check, run.

A program is a graph of modules (M11a). `turkey/modules.py` loads it and works
out what each module can see; `turkey/resolve.py` rewrites each module's names
so that the whole program shares one flat namespace again. What is left here is
what was always here: for each module, in dependency order, generate a
constraint, solve it, check exhaustiveness, elaborate.

The Prelude is checked, not trusted: `class Add a` and `instance Add Int` go
through exactly the machinery a user's would, which is the point -- an operator
is a class method and nothing about it is privileged. It is simply the first
module in the order, and the only one implicitly imported.

The tables are shared across modules and rebuilt per check. `DeclTable` and
`ClassTable` are shared because types, classes and instances are global (see
SPEC-DELTAS.md entry 41); the type environment is shared because names are
unique after resolution, so there is nothing left for a second one to separate.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from . import ast
from .builtins import initial_type_env, initial_values
from .classes import ClassTable
from .constraints import Env, Solver
from .decls import DeclTable
from .deps import pattern_vars
from .errors import short
from .eval import Evaluator
from .evidence import Elaborator
from .infer import Generator
from .modules import ENTRY, SEP, Module, ModuleLoader
from .resolve import Resolver
from .types import Scheme


@dataclass
class Checked:
    program: ast.Program  # the entry module's
    ordered: list[ast.Stmt]  # every module's, in evaluation order
    decls: DeclTable
    classes: ClassTable
    env: Env
    modules: list[Module]
    scope: dict[str, str]  # the entry module's: surface name -> internal name
    main: str  # what `main` is called internally, if the entry defines one
    signatures: list[tuple[str, Scheme]]  # the entry module's, in source order
    warnings: list[str]


def check(src: str, file: str | None = None,
          search: list[Path] | None = None) -> Checked:
    """Check a whole program, given the source of its entry module."""
    loader = ModuleLoader(search)
    entry = loader.load_entry(src, file)

    decls = DeclTable()
    # Every builtin, `Prim.*` included: what a module may *write* is settled by
    # its scope, one stage earlier, so the environment no longer has to.
    env = initial_type_env().child()
    classes: ClassTable | None = None
    ordered: list[ast.Stmt] = []
    warnings: list[str] = []
    generator = None

    for module in loader.order:
        Resolver(module.scope).program(module.program)
        # Generation builds the module's constraint and decides nothing;
        # solving is what assigns ranks, generalizes and fills in `env`.
        # Splitting them this way is the point of the HM(X) shape.
        generator = Generator(decls, env, classes)
        classes = generator.classes
        items, constraint = generator.generate(module.program)
        solver = Solver(decls, env, classes)
        solver.run(constraint)
        generator.check_exhaustiveness()
        # Elaboration is last because it is the only stage that needs the
        # answers: which instance covers `Semigroup a` is not a question until
        # the solver has decided what `a` is. See turkey/evidence.py.
        Elaborator(classes).run(solver.uses)
        ordered.extend(items)
        warnings.extend(generator.warnings)

    assert generator is not None and classes is not None
    return Checked(
        entry.program, ordered, decls, classes, env, loader.order, entry.scope,
        entry.scope.get("main", "main"),
        _signatures(entry, env, classes), warnings,
    )


def _signatures(entry: Module, env: Env,
                classes: ClassTable) -> list[tuple[str, Scheme]]:
    """What `turkey types` prints: the entry module's own bindings.

    Reported under the names the author wrote, not the qualified ones
    resolution gave them -- the module a name came from is not news to the
    person who wrote the file.
    """
    out: list[tuple[str, Scheme]] = []
    for item in entry.program.decls:
        if isinstance(item, ast.ClassDecl):
            # A method's scheme is stated by its class rather than inferred,
            # but it is still the thing a reader wants to see, and the class
            # variable's discovered kind shows up nowhere else.
            info = classes.classes[item.name]
            out.extend((m.name, info.methods[m.name].scheme) for m in item.methods)
            continue
        if not isinstance(item, ast.Stmt):
            continue
        names = ([item.decl.name] if isinstance(item, ast.SFun)
                 else sorted(pattern_vars(item.pat)))
        for name in names:
            binding = env.lookup(name)
            if binding is not None:
                out.append((name.rpartition(SEP)[2], binding.scheme))
    return out


def run(src: str, filename: str = "<input>") -> None:
    search = [Path(filename).resolve().parent] if filename != "<input>" else None
    checked = check(src, None if filename == "<input>" else filename, search)
    report_warnings(checked.warnings, filename)
    Evaluator(checked.decls, initial_values()).run(checked.ordered, checked.main)


def report_warnings(warnings: list[str], filename: str) -> None:
    for warning in warnings:
        print(f"{filename}:{short(warning)}", file=sys.stderr)


__all__ = ["Checked", "ENTRY", "check", "run", "report_warnings"]
