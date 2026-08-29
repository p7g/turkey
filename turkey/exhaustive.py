"""Pattern-matching exhaustiveness checking (design.md section 5.1).

This is Maranget's usefulness algorithm ("Warnings for pattern matching", JFP
2007) in its witness-producing form, so a warning can name a value the match
does not handle rather than merely saying one exists.

Non-exhaustive matches are a warning, not an error -- section 5.1 makes an
unhandled case a runtime panic. The check runs after inference, when every
scrutinee type is known.
"""

from __future__ import annotations

from . import ast
from .decls import DeclTable
from .types import TCon, TTuple, Type, prune, unify

# A pattern is one of:
#   ("wild",)
#   ("con", name, [pattern, ...])
#   ("tuple", [pattern, ...])
#   ("lit", kind, value)
WILD = ("wild",)


def _normalize(pat: ast.Pattern, decls: DeclTable):
    """Reduce surface patterns to the four shapes the algorithm understands."""
    if isinstance(pat, (ast.PVar, ast.PWild)):
        return WILD
    if isinstance(pat, ast.PAnnot):
        return _normalize(pat.pat, decls)
    if isinstance(pat, ast.PLit):
        return ("lit", pat.kind, pat.value)
    if isinstance(pat, ast.PTuple):
        return ("tuple", [_normalize(p, decls) for p in pat.elems])
    if isinstance(pat, ast.PCon):
        return ("con", pat.name, [_normalize(p, decls) for p in pat.args])
    if isinstance(pat, ast.PRecord):
        # Reorder the fields into declaration order and fill in the ones the
        # pattern left out, so every row for a constructor has the same width.
        info = decls.con(pat.name)
        supplied = {label: sub for label, sub in pat.fields}
        args = [
            _normalize(supplied[name], decls) if name in supplied else WILD
            for name in info.field_names
        ]
        return ("con", pat.name, args)
    raise AssertionError(f"unhandled pattern {type(pat).__name__}")


def _key(pattern):
    """The head constructor a pattern tests for, or None for a wildcard."""
    if pattern[0] == "wild":
        return None
    if pattern[0] == "con":
        return ("con", pattern[1])
    if pattern[0] == "tuple":
        return ("tuple", len(pattern[1]))
    return ("lit", pattern[1], pattern[2])


def _arity(pattern) -> int:
    if pattern[0] == "con":
        return len(pattern[2])
    if pattern[0] == "tuple":
        return len(pattern[1])
    return 0


class Checker:
    def __init__(self, decls: DeclTable):
        self.decls = decls

    # -- type-directed signature information -------------------------------

    def signature(self, t: Type):
        """Every head constructor of `t`, or None when there are too many.

        Int, String and friends have unboundedly many values, so a column of
        them is only covered by a wildcard.
        """
        t = prune(t)
        if isinstance(t, TTuple):
            return [("tuple", len(t.elems))]
        if isinstance(t, TCon):
            if t.name == "Bool":
                return [("lit", "Bool", True), ("lit", "Bool", False)]
            info = self.decls.tycons.get(t.name)
            if info is not None and info.variants:
                return [("con", v.name) for v in info.variants]
        return None

    def sub_types(self, key, t: Type) -> list[Type]:
        """The column types produced by specializing a column of type `t` on `key`."""
        if key[0] == "tuple":
            return list(prune(t).elems)
        if key[0] == "lit":
            return []
        con = self.decls.instantiate_con(key[1], 1, None)
        # The pattern already type-checked, so this only propagates the
        # scrutinee's arguments into the constructor's field types.
        unify(con.ret, t)
        return list(con.params)

    # -- the algorithm ------------------------------------------------------

    def witness(self, matrix: list[list], types: list[Type]) -> list | None:
        """A row of patterns no row of `matrix` covers, or None if it is exhaustive."""
        if not types:
            return None if matrix else []

        head_type, rest_types = types[0], types[1:]
        used = {_key(row[0]) for row in matrix if _key(row[0]) is not None}
        signature = self.signature(head_type)

        if signature is not None and all(key in used for key in signature):
            # Complete signature: the match is exhaustive only if it is
            # exhaustive under every constructor.
            for key in signature:
                specialized, arg_types = self.specialize(matrix, key, head_type)
                found = self.witness(specialized, arg_types + rest_types)
                if found is not None:
                    width = len(arg_types)
                    return [self.rebuild(key, found[:width])] + found[width:]
            return None

        # Incomplete: a value built from a constructor nobody tested reaches the
        # default matrix, so recurse there and report one of the missing heads.
        default = [row[1:] for row in matrix if _key(row[0]) is None]
        found = self.witness(default, rest_types)
        if found is None:
            return None
        if signature is None:
            missing = WILD
        else:
            key = next(k for k in signature if k not in used)
            missing = self.rebuild(key, [WILD] * self.key_arity(key, head_type))
        return [missing] + found

    def specialize(self, matrix, key, head_type):
        arg_types = self.sub_types(key, head_type)
        width = len(arg_types)
        rows = []
        for row in matrix:
            head = row[0]
            if _key(head) is None:
                rows.append([WILD] * width + row[1:])
            elif _key(head) == key:
                args = head[2] if head[0] == "con" else (head[1] if head[0] == "tuple" else [])
                rows.append(list(args) + row[1:])
        return rows, arg_types

    def key_arity(self, key, head_type) -> int:
        if key[0] == "tuple":
            return key[1]
        if key[0] == "lit":
            return 0
        return self.decls.con(key[1]).arity

    @staticmethod
    def rebuild(key, args):
        if key[0] == "tuple":
            return ("tuple", list(args))
        if key[0] == "lit":
            return ("lit", key[1], key[2])
        return ("con", key[1], list(args))

    # -- reporting ----------------------------------------------------------

    def check(self, match: ast.EMatch, scrutinee: Type) -> str | None:
        matrix = [
            [_normalize(pattern, self.decls)]
            for arm in match.arms
            for pattern in arm.patterns
        ]
        found = self.witness(matrix, [scrutinee])
        if found is None:
            return None
        return render(found[0])


def render(pattern) -> str:
    if pattern[0] == "wild":
        return "_"
    if pattern[0] == "lit":
        if pattern[1] == "Bool":
            return "true" if pattern[2] else "false"
        return repr(pattern[2])
    if pattern[0] == "tuple":
        return "(" + ", ".join(render(p) for p in pattern[1]) + ")"
    if not pattern[2]:
        return pattern[1]
    # The paren form is self-delimiting, so a nested witness needs no extra
    # grouping: `Some(Some(_))`, not `Some (Some _)`.
    return pattern[1] + "(" + ", ".join(render(p) for p in pattern[2]) + ")"
