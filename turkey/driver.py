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
import threading
from dataclasses import dataclass
from pathlib import Path

from . import ast, builtins, coretc, desugar, joins, lower, mono, opt, pygen
from .builtins import initial_type_env
from .classes import ClassTable
from .constraints import Env, Solver
from .decls import DeclTable
from .deps import free_names, pattern_vars, sccs
from .errors import short
from .evidence import Elaborator
from .infer import Generator
from .modules import ENTRY, SEP, Module, ModuleLoader
from .resolve import Resolver
from .core import CProgram
from .typed import TypeTable
from .types import Scheme, show, show_kind, show_pred, show_scheme


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
        desugar.program(module.program, module.scope.methods)
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
    # Reduction exposes local continuations for join discovery; discovery in
    # turn exposes constructor-valued jumps for join specialization. Each pass
    # therefore gets one look at the representation it knows how to simplify.
    program_opt = opt.reduce_program(
        joins.discover(opt.reduce_program(program_mono)))
    coretc.check_program(program_opt, decls, classes, coretc.globals_of(env))
    return Checked(
        entry.program, ordered, decls, classes, env, loader.order,
        entry.scope.values, main,
        _signatures(entry, env, classes), warnings, types,
        program_core, program_mono, program_opt, entry.name,
    )


def desugared(src: str, file: str | None = None,
              search: list[Path] | None = None) -> list[Module]:
    """Every module of a program, loaded, resolved and desugared -- and no
    further. This is the front end's halfway point made observable: the two
    passes it runs are exactly the two `check` runs before inference, in the
    same order and with the same arguments, so the tree it hands back is the
    tree the checker would have seen. `turkey desugar` prints it, and the
    bootstrap compiler's `desugar` is diffed against that.
    """
    loader = ModuleLoader(search)
    loader.load_entry(src, file)
    for module in loader.order:
        Resolver(module.scope).program(module.program)
        desugar.program(module.program, module.scope.methods)
    return loader.order


def declared(src: str, file: str | None = None,
             search: list[Path] | None = None) -> tuple[DeclTable, list[Module]]:
    """The program's declaration table, and nothing after it.

    The stage between desugaring and inference made observable: every type
    constructor with the kind that was inferred for it, every value constructor
    with the scheme it was generalized to, in registration order. One table for
    the whole program, as `check` builds it -- which is also what makes the
    clash rule (delta 43) visible, since it takes two modules to trip.
    """
    modules = desugared(src, file, search)
    decls = DeclTable()
    for module in modules:
        decls.register_all([d for d in module.program.decls
                            if isinstance(d, ast.TypeDecl)])
    return decls, modules


def show_declarations(decls: DeclTable) -> str:
    """The declaration table, one line per entity, in registration order."""
    out: list[str] = []
    for name in decls.tycons:
        info = decls.tycons[name]
        params = "".join(f" {p}" for p in info.params)
        alias = " = alias" if info.is_alias else ""
        out.append(f"type {name}{params} :: {show_kind(info.kind)}{alias}\n")
        for con in info.variants:
            fields = ("" if con.field_names is None
                      else " {" + ", ".join(con.field_names) + "}")
            out.append(f"  con {con.name}/{con.arity}{fields} : "
                       f"{show_scheme(con.scheme)}\n")
    return "".join(out)


def binding_groups(module: Module) -> list[tuple[list[str], list[str]]]:
    """One module's top-level binding groups, in the order they are checked.

    The graph is keyed by *item*, not by bound name: a single binding may
    introduce several names (`let (a, b) = ...`), and keying by name would split
    one item across two components and infer -- and evaluate -- its right-hand
    side twice. `turkey/infer.py` builds exactly this and then hangs a
    constraint on it; this is the same computation with nothing hung on it, so
    that the ordering can be diffed on its own.
    """
    items = [d for d in module.program.decls if isinstance(d, ast.Stmt)]
    keys = {id(item): f"item{i}" for i, item in enumerate(items)}
    names_of = {keys[id(item)]: _names_of(item) for item in items}
    owner: dict[str, str] = {}
    for item in items:
        for name in names_of[keys[id(item)]]:
            owner.setdefault(name, keys[id(item)])
    graph = {
        keys[id(item)]: {owner[n] for n in _deps_of(item) if n in owner}
        for item in items
    }
    return [(component, sorted(n for key in component for n in names_of[key]))
            for component in sccs(graph)]


def _names_of(item: ast.Stmt) -> list[str]:
    if isinstance(item, ast.SFun):
        return [item.decl.name]
    return sorted(pattern_vars(item.pat))


def _deps_of(item: ast.Stmt) -> set[str]:
    if isinstance(item, ast.SFun):
        return free_names(item.decl.body, frozenset(
            n for p in item.decl.params for n in pattern_vars(p)))
    return free_names(item.value)


def show_binding_groups(modules: list[Module]) -> str:
    out: list[str] = []
    for module in modules:
        out.append(f"module {module.name}\n")
        for component, bound in binding_groups(module):
            out.append(f"  group {' '.join(component)} : {', '.join(bound)}\n")
    return "".join(out)


def registered(src: str, file: str | None = None,
               search: list[Path] | None = None) -> tuple[DeclTable, ClassTable]:
    """The declaration and class tables, and nothing after them.

    Everything `check` does before it generates a single constraint: types,
    constructors, classes, their methods' schemes, their families, and the
    instance table. One pair of tables for the whole program, in the order
    `check` builds them.
    """
    decls = DeclTable()
    classes = ClassTable(decls)
    for module in desugared(src, file, search):
        decls.register_all([d for d in module.program.decls
                            if isinstance(d, ast.TypeDecl)])
        classes.register_all(
            [d for d in module.program.decls if isinstance(d, ast.ClassDecl)],
            [d for d in module.program.decls if isinstance(d, ast.InstanceDecl)],
            module.name)
    return decls, classes


def show_classes(decls: DeclTable, classes: ClassTable) -> str:
    """The class table, in registration order.

    Each class names the variable it abstracts over `a`, and its superclasses,
    families and methods are printed against that one naming -- so the `a` in
    `[Semigroup a]` and the `a` in `fun(a, a) -> a` are visibly the same
    variable. An instance does the same over its head's variables.
    """
    out: list[str] = []
    for name, info in classes.classes.items():
        names = {info.var.id: "a"}
        out.append(f"class {name} {info.param} :: {show_kind(info.kind)}\n")
        for sup in info.supers:
            out.append(f"  super {show_pred(sup, names, free_prefix='')}\n")
        for fam in info.families:
            fi = decls.families[fam]
            out.append(f"  family {fam} :: {show_kind(fi.arg_kind)} -> "
                       f"{show_kind(fi.res_kind)}\n")
        for mname, m in info.methods.items():
            out.append(f"  method {mname} : {show_scheme(m.scheme)}\n")
    for cls, insts in classes.instances.items():
        for inst in insts:
            names: dict[int, str] = {}
            out.append(f"instance {cls} "
                       f"{show(inst.head, names, free_prefix='')} "
                       f"[{inst.module}]\n")
            for p in inst.context:
                out.append(f"  context {show_pred(p, names, free_prefix='')}\n")
            for fam, body in inst.families.items():
                out.append(f"  type {fam} = "
                           f"{show(body, names, free_prefix='')}\n")
    return "".join(out)


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
            out.extend(((m.name.rpartition(".")[2].rpartition(SEP)[2] or m.name),
                        info.methods[m.name].scheme) for m in item.methods)
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


# A Turkey program's recursion depth is its own business, and CPython's is not
# a fact about the language. The default 1000 frames is reached by an ordinary
# recursive walk over a few hundred nodes -- which is to say, by any compiler
# reading a source file of any size (plan.txt item 9). The generated program
# therefore runs on a thread of its own with a large stack.
#
# This is not a workaround that gets thrown away: the C backend's answer is the
# same shape, a pthread with an explicit stack size, because a native program
# has exactly the same problem and exactly the same fix.
STACK_BYTES = 512 * 1024 * 1024
RECURSION_LIMIT = 200_000


def run_deep(thunk):
    """Run `thunk` with room to recurse, and re-raise what it raised.

    A thread's exception does not reach its parent, so it is caught and
    carried across by hand. `SystemExit` is carried too -- `Prim.exit` is a
    primitive, so a program's chosen status has to survive the trip.
    """
    box: list = []

    def body() -> None:
        previous = sys.getrecursionlimit()
        sys.setrecursionlimit(max(previous, RECURSION_LIMIT))
        try:
            thunk()
        except BaseException as exc:  # re-raised on the caller's thread below
            box.append(exc)
        finally:
            sys.setrecursionlimit(previous)

    previous_size = threading.stack_size()
    try:
        threading.stack_size(STACK_BYTES)
    except (ValueError, RuntimeError):
        # A host that will not grow a thread stack still runs the program;
        # it just runs it with whatever depth it has.
        pass
    try:
        worker = threading.Thread(target=body, name="turkey")
        worker.start()
        worker.join()
    finally:
        threading.stack_size(previous_size)
    if box:
        raise box[0]


def run(src: str, filename: str = "<input>", args: list[str] | None = None) -> None:
    search = [Path(filename).resolve().parent] if filename != "<input>" else None
    checked = check(src, None if filename == "<input>" else filename, search)
    report_warnings(checked.warnings, filename)
    # What the program will see through `Prim.args`: its own arguments, with
    # neither the interpreter nor the file name in front of them. The C backend
    # hands over `argv + 1`, so the two hosts agree on element zero -- which
    # M26's stage2/stage3 comparison needs, since the compiler reads its own
    # arguments to decide what to write.
    builtins.set_args(args or [])
    # The *optimized* Core is compiled to Python and run (M17).  The old
    # evaluator remains a differential oracle in the tests, but keeping it on
    # this path would hide code-generator bugs and make the optimizer's payoff
    # unmeasurable.
    run_deep(lambda: pygen.execute(
        checked.opt, checked.decls, checked.main, filename))


def report_warnings(warnings: list[str], filename: str) -> None:
    for warning in warnings:
        print(f"{filename}:{short(warning)}", file=sys.stderr)


__all__ = ["Checked", "ENTRY", "check", "run", "run_deep",
           "report_warnings"]
