"""Loading a program's modules, and deciding what each one can see.

A program is no longer a file; it is a graph of them. This module walks that
graph and produces, for each module, the two dictionaries the rest of the
compiler needs: a *scope* mapping every surface name the module may write to
the internal name it means, and an *export* map that is the part of that scope
its importers get to share. `turkey/resolve.py` does the rewriting; nothing
here touches an AST beyond parsing it.

Three rules from design.md section 9 are what the scope is built from, in this
order, later winning: what the Prelude exports, then each `import`, then the
module's own top-level declarations. So a local definition shadows an import,
and an import shadows the Prelude -- which is what makes a user program free to
define `add` or `push` at last (`plan.txt` item 3).

The import graph must be acyclic. Two modules that need each other are one
module in the prototype: the checker solves a whole module's bindings as one
dependency-ordered pass, and there is nothing that would interleave two.

Classes, instances and type constructors are *not* scoped here. They are
global, registered into one shared `ClassTable`/`DeclTable` as each module is
checked. Instance coherence is why (SPEC-DELTAS.md entry 41): a predicate is
solved once, and it must not matter which module asked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import ast
from .builtins import PRIM_NAMES
from .deps import pattern_vars
from .errors import Span, TypeError_
from .parser import BUILTIN_TYCONS, parse

LIB = Path(__file__).resolve().parent / "lib"

# What a module's top-level binding is called once resolution has run. `#` is
# not a character any surface name may contain, so an internal name can never
# collide with one a program could write -- and a diagnostic can strip the
# prefix back off without having to guess where a module name ends (an
# ordinary `.` would be ambiguous against `Array.push`). See `errors.short`.
SEP = "#"


def internal(module: str, name: str) -> str:
    return f"{module}{SEP}{name}"


PRELUDE = "Prelude"
ENTRY = "Main"


@dataclass
class Scope:
    """One module's view of the world, in three namespaces.

    They have to be three. A value is lowercase while a type and a constructor
    are both uppercase, so `type Point = Point(Int, Int)` puts the same
    spelling in two of them. Each maps a name the module may *write* -- bare,
    or qualified by an import's alias -- to the internal name it means.

    Classes are absent on purpose: they are global (delta 41), because instance
    coherence depends on a predicate meaning the same thing everywhere.
    """

    values: dict[str, str] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)
    cons: dict[str, str] = field(default_factory=dict)

    def spaces(self) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        return (self.values, self.types, self.cons)


@dataclass
class Module:
    name: str
    program: ast.Program
    library: bool
    scope: Scope = field(default_factory=Scope)
    # The part of that an importer gets.
    exports: Scope = field(default_factory=Scope)


class ModuleLoader:
    def __init__(self, search: list[Path] | None = None):
        # The entry file's own directory first, then the shipped library.
        self.search = list(search or []) + [LIB]
        self.modules: dict[str, Module] = {}
        self.order: list[Module] = []
        # Everything already known, threaded into later parses. Type names go
        # to the parser (section 7's alias-vs-data pre-pass cannot see another
        # file); method names go to every module's scope, since a class method
        # is global.
        self.tycons: set[str] = set()
        self.methods: set[str] = set()
        # Internal type name -> its constructors' short names, so that a
        # `T(..)` in an export or import list can be answered for a type this
        # module did not itself declare.
        self.variants: dict[str, list[str]] = {}

    # -- the graph ---------------------------------------------------------

    def load_entry(self, src: str, file: str | None = None,
                   name: str = ENTRY) -> Module:
        """Load the Prelude, then the program the driver was handed."""
        self._load(PRELUDE, [])
        return self._add(name, src, file, library=False, stack=[name])

    def _load(self, name: str, stack: list[str]) -> Module:
        existing = self.modules.get(name)
        if existing is not None:
            return existing
        if name in stack:
            cycle = " -> ".join([*stack[stack.index(name):], name])
            raise TypeError_(
                f"imports form a cycle: {cycle}. Two modules that need each "
                f"other are one module here.",
                None,
            )
        path = self._resolve(name)
        src = path.read_text(encoding="utf-8")
        library = path.is_relative_to(LIB)
        display = str(path.relative_to(LIB)) if library else str(path)
        return self._add(name, src, display, library=library,
                         stack=[*stack, name])

    def _resolve(self, name: str) -> Path:
        relative = Path(*name.split(".")).with_suffix(".tl")
        for root in self.search:
            candidate = root / relative
            if candidate.is_file():
                return candidate
        where = ", ".join(str(root) for root in self.search)
        raise TypeError_(f"cannot find module '{name}' (searched {where})", None)

    def _add(self, name: str, src: str, file: str | None, library: bool,
             stack: list[str]) -> Module:
        program = parse(src, frozenset(self.tycons), file)
        before = frozenset(self.tycons)
        for imp in program.imports:
            self._load(imp.name, stack)
        if frozenset(self.tycons) != before:
            # An imported type name changes how section 7 reads this file, and
            # the pre-pass that decides it already ran. Read it again, knowing.
            program = parse(src, frozenset(self.tycons), file)

        module = Module(name, program, library)
        self.tycons |= {d.name for d in program.decls
                        if isinstance(d, ast.TypeDecl)}
        self.methods |= {m.name for d in program.decls
                         if isinstance(d, ast.ClassDecl) for m in d.methods}
        for d in program.decls:
            if isinstance(d, ast.TypeDecl) and d.variants is not None:
                self.variants[internal(name, d.name)] = [v.name for v in d.variants]

        module.scope = self._scope(module)
        module.exports = self._exports(module)
        self.modules[name] = module
        self.order.append(module)
        return module

    # -- scope and exports -------------------------------------------------

    def _scope(self, module: Module) -> Scope:
        scope = Scope()
        # `Prim.*` is spellable only from a library module, which is what keeps
        # the machine operations out of the language: the environment holds
        # them for every module, and this is the stage that says no.
        if module.library:
            scope.values.update({name: name for name in PRIM_NAMES})
        # A class method is global and unqualified; see turkey/resolve.py.
        scope.values.update({name: name for name in self.methods})
        # So are the built-in type constructors, which no module declares.
        scope.types.update({name: name for name in BUILTIN_TYCONS})

        # A module the Prelude itself imports is loaded before the Prelude
        # exists, and needs nothing from it.
        if PRELUDE in self.modules:
            self._bring(scope, self.modules[PRELUDE], PRELUDE,
                        qualified=False, span=None)
        for imp in module.program.imports:
            dep = self.modules[imp.name]
            self._bring(scope, dep, imp.alias or imp.name, imp.qualified,
                        imp.span, items=imp.items, hiding=imp.hiding)

        # Last, so a module's own declarations shadow everything imported.
        for space, names in zip(scope.spaces(), own_declared(module.program)):
            for short in names:
                space[short] = internal(module.name, short)
        return scope

    def _bring(self, scope: Scope, dep: Module, alias: str, qualified: bool,
               span: Span | None, items: list[ast.ExportItem] | None = None,
               hiding: list[ast.ExportItem] | None = None) -> None:
        chosen = [dict(space) for space in dep.exports.spaces()]
        if items is not None:
            wanted = {i.name for i in items}
            for item in items:
                # `T(..)` in an import list asks for the constructors too.
                wanted |= set(_subs_of(item, self._variants_of(
                    dep.exports.types.get(item.name))))
            named = set().union(*(set(space) for space in dep.exports.spaces()))
            missing = sorted(n for n in wanted if n not in named)
            if missing:
                raise TypeError_(
                    f"module '{dep.name}' does not export "
                    f"{', '.join(repr(n) for n in missing)}", span)
            chosen = [{n: v for n, v in space.items() if n in wanted}
                      for space in chosen]
        if hiding is not None:
            hidden = {i.name for i in hiding}
            chosen = [{n: v for n, v in space.items() if n not in hidden}
                      for space in chosen]

        for space, incoming in zip(scope.spaces(), chosen):
            for short, target in incoming.items():
                space[f"{alias}.{short}"] = target
                if not qualified:
                    space[short] = target

    def _exports(self, module: Module) -> Scope:
        mine = Scope(*[{short: internal(module.name, short) for short in names}
                       for names in own_declared(module.program)])
        header = module.program.header
        if header is None or header.exports is None:
            return mine

        classes = {d.name for d in module.program.decls
                   if isinstance(d, ast.ClassDecl)}
        out = Scope()
        for item in header.exports:
            if item.kind == "module":
                self._reexport(module, item, out)
            elif item.name[:1].islower():
                out.values[item.name] = self._exported(
                    module, mine.values, item, "values")
            elif item.name in classes:
                # A class is global (delta 41): naming it withholds nothing and
                # adds nothing, but it is legal to say so.
                continue
            else:
                target = self._exported(module, mine.types, item, "types")
                out.types[item.name] = target
                for con in _subs_of(item, self._variants_of(target)):
                    target = mine.cons.get(con) or module.scope.cons.get(con)
                    if target is None:
                        raise TypeError_(
                            f"'{con}' is not a constructor of '{item.name}'",
                            item.span)
                    out.cons[con] = target
        return out

    def _variants_of(self, type_internal: str | None) -> list[str]:
        return self.variants.get(type_internal or "", [])

    def _reexport(self, module: Module, item: ast.ExportItem, out: Scope) -> None:
        """`module M`: everything in scope here under that qualification,
        passed on *still qualified*. That is what lets the Prelude hand every
        program `Array.push` without claiming the bare `push`."""
        prefix = item.name + "."
        found = False
        for space, source in zip(out.spaces(), module.scope.spaces()):
            for surface, target in source.items():
                if surface.startswith(prefix):
                    space[surface] = target
                    found = True
        if not found:
            raise TypeError_(
                f"module '{module.name}' re-exports '{item.name}', which is "
                f"not imported here", item.span)

    def _exported(self, module: Module, own: dict[str, str],
                  item: ast.ExportItem, space: str) -> str:
        target = own.get(item.name) or getattr(module.scope, space).get(item.name)
        if target is None:
            raise TypeError_(
                f"module '{module.name}' exports '{item.name}', which is not "
                f"defined or imported here", item.span)
        return target


def own_declared(program: ast.Program) -> tuple[list[str], list[str], list[str]]:
    """What a module declares at its top level: values, types, constructors."""
    values: list[str] = []
    types: list[str] = []
    cons: list[str] = []
    for decl in program.decls:
        if isinstance(decl, ast.SFun):
            values.append(decl.decl.name)
        elif isinstance(decl, (ast.SLet, ast.SVar)):
            values.extend(sorted(pattern_vars(decl.pat)))
        elif isinstance(decl, ast.TypeDecl):
            types.append(decl.name)
            cons.extend(v.name for v in (decl.variants or []))
    return values, types, cons


def _subs_of(item: ast.ExportItem, all_cons: list[str]) -> list[str]:
    """`T` names the type alone; `T(..)` every constructor; `T(A, B)` those."""
    if item.subs is None:
        return []
    return all_cons if item.subs in ([".."], [".", "."]) else item.subs

