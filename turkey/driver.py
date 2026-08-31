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

from . import ast, coretc, desugar, joins, lower, mono
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
from .core import CProgram
from .typed import TypeTable
from .types import Scheme


@dataclass
class Checked:
    program: ast.Program  # the entry module's
    ordered: list[ast.Stmt]  # every module's, in evaluation order
    decls: DeclTable
    classes: ClassTable
    env: Env
    modules: list[Module]
    scope: dict[str, str]  # the entry module's values: surface -> internal
    main: str  # what `main` is called internally, if the entry defines one
    signatures: list[tuple[str, Scheme]]  # the entry module's, in source order
    warnings: list[str]
    types: TypeTable  # every expression's type, for the lowering (M13a)
    core: CProgram  # the elaboration, as a checked datatype (M13b)
    mono: CProgram  # the elaboration specialized, checked the same way (M14a)
    opt: CProgram  # and optimized, checked again (M15b)
    module: str  # the entry module's name, for `turkey core`


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
    # Shared across modules for the same reason `decls` and `env` are: after
    # resolution there is one flat namespace and one program, so there is
    # nothing for a second table to separate.
    types = TypeTable()

    for module in loader.order:
        Resolver(module.scope).program(module.program)
        # `?` and `do` die here, before anything looks at the tree. Desugaring
        # after resolution means the code the pass moves into lambdas has
        # already had its names settled, so moving it cannot change what one
        # means -- and the code the pass writes may name internal constructors
        # directly. See turkey/desugar.py.
        desugar.program(module.program)
        # Generation builds the module's constraint and decides nothing;
        # solving is what assigns ranks, generalizes and fills in `env`.
        # Splitting them this way is the point of the HM(X) shape.
        generator = Generator(decls, env, classes, module.name, types)
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
    # Lowering and checking are the last stage, and the check is not optional.
    # "Evidence checkable rather than trusted" (`plan.txt` item 5) is not true
    # of a check nobody runs, so it runs the way exhaustiveness does: always.
    program_core = lower.Lowerer(decls, classes, env, types).program(ordered)
    coretc.check_program(program_core, decls, classes, coretc.globals_of(env))
    # Specialization is checked for the same reason the lowering is, and by the
    # same checker: it rewrites every type in every body it copies, so "the
    # copy still typechecks" is the property that a substitution went wrong
    # would break first. See turkey/mono.py.
    main = entry.scope.values.get("main", "main")
    program_mono = mono.monomorphize(program_core, decls, classes, main)
    coretc.check_program(program_mono, decls, classes, coretc.globals_of(env))
    # And the optimizations, checked the same way and for the sharpest reason
    # yet: join-point discovery decides that a call is in tail position, and
    # the checker decides the same thing independently by threading a join
    # scope. A pass that called a position a tail when the rule does not emits
    # a jump with nowhere to go, and this is where that is caught -- on every
    # program in the suite, on every run. See turkey/joins.py.
    program_opt = joins.discover(program_mono)
    coretc.check_program(program_opt, decls, classes, coretc.globals_of(env))
    return Checked(
        entry.program, ordered, decls, classes, env, loader.order,
        entry.scope.values, main,
        _signatures(entry, env, classes), warnings, types,
        program_core, program_mono, program_opt, entry.name,
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
    # The *optimized* Core is the program that runs (M15b), for the reason the
    # specialized one was made to run at M14b: a pass whose output is only ever
    # inspected is a pass nothing tests. `checked.core` and `checked.mono` are
    # kept because they are what `turkey core` and `turkey mono` print and what
    # each later stage is read against, but nothing evaluates them.
    Evaluator(checked.decls, initial_values()).run(checked.opt, checked.main)


def report_warnings(warnings: list[str], filename: str) -> None:
    for warning in warnings:
        print(f"{filename}:{short(warning)}", file=sys.stderr)


__all__ = ["Checked", "ENTRY", "check", "run", "report_warnings"]
