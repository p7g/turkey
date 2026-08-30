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
from .builtins import BUILTINS, PRIM_NAMES
from .deps import pattern_vars
from .errors import Span, TypeError_
from .parser import parse

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
class Module:
    name: str
    program: ast.Program
    library: bool
    # Surface name -> internal name, for every value name the module may write.
    scope: dict[str, str] = field(default_factory=dict)
    # The part of that an importer gets, by short name.
    exports: dict[str, str] = field(default_factory=dict)


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
        display = path.name if library else str(path)
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

        module.scope = self._scope(module)
        module.exports = self._exports(module)
        self.modules[name] = module
        self.order.append(module)
        return module

    # -- scope and exports -------------------------------------------------

    def _scope(self, module: Module) -> dict[str, str]:
        # The built-in names are spelled the way they are stored, so they map
        # to themselves. `Prim.*` is reachable only from a library module,
        # which is what keeps the machine operations out of the language.
        scope = {name: name for name in BUILTINS}
        if module.library:
            scope.update({name: name for name in PRIM_NAMES})
        # A class method is global and unqualified; see turkey/resolve.py.
        scope.update({name: name for name in self.methods})

        if module.name != PRELUDE:
            self._bring(scope, self.modules[PRELUDE], PRELUDE,
                        qualified=False, span=None)
        for imp in module.program.imports:
            dep = self.modules[imp.name]
            self._bring(scope, dep, imp.alias or imp.name, imp.qualified,
                        imp.span, items=imp.items, hiding=imp.hiding)

        # Last, so a module's own declarations shadow everything imported.
        for short in own_names(module.program):
            scope[short] = internal(module.name, short)
        return scope

    def _bring(self, scope: dict[str, str], dep: Module, alias: str,
               qualified: bool, span: Span | None,
               items: list[str] | None = None,
               hiding: list[str] | None = None) -> None:
        chosen = dict(dep.exports)
        if items is not None:
            wanted = {_short(i) for i in items}
            missing = sorted(n for n in wanted
                             if n not in chosen and n[:1].islower())
            if missing:
                raise TypeError_(
                    f"module '{dep.name}' does not export "
                    f"{', '.join(repr(n) for n in missing)}", span)
            chosen = {n: v for n, v in chosen.items() if n in wanted}
        if hiding is not None:
            hidden = {_short(i) for i in hiding}
            chosen = {n: v for n, v in chosen.items() if n not in hidden}

        for short, internal in chosen.items():
            scope[f"{alias}.{short}"] = internal
            if not qualified:
                scope[short] = internal

    def _exports(self, module: Module) -> dict[str, str]:
        own = {short: internal(module.name, short)
               for short in own_names(module.program)}
        header = module.program.header
        if header is None or header.exports is None:
            return own
        out: dict[str, str] = {}
        for item in header.exports:
            short = _short(item)
            if not short[:1].islower():
                # A type or a class. Both are global in this milestone, so an
                # export list neither adds nor withholds anything for them;
                # see SPEC-DELTAS.md entry 41.
                continue
            target = own.get(short) or module.scope.get(short)
            if target is None:
                raise TypeError_(
                    f"module '{module.name}' exports '{short}', which is not "
                    f"defined or imported here", header.span)
            out[short] = target
        return out


def own_names(program: ast.Program) -> list[str]:
    """The value names a module declares at its top level, in source order."""
    out: list[str] = []
    for decl in program.decls:
        if isinstance(decl, ast.SFun):
            out.append(decl.decl.name)
        elif isinstance(decl, (ast.SLet, ast.SVar)):
            out.extend(sorted(pattern_vars(decl.pat)))
    return out


def _short(item: str) -> str:
    """`Point(..)` and `Eq(..)` name a type or a class; keep just the head."""
    return item.split("(", 1)[0]
