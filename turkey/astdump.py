"""A canonical, host-independent spelling of a parse tree.

`turkey ast` used to print the dataclass `repr`, which is Python's and cannot be
reproduced by anything else -- the same objection that moved the token dump to
`Token.canonical` (SPEC-DELTAS.md 59). This is the tree's version of that
format, and it exists to be diffed: the bootstrap compiler's parser prints the
same bytes from Turkey, and a milestone is the comparison (plan.txt item 9,
M20).

The shape is an indented tree, two spaces per level, one node per line:

    node@line:col atom atom
      child
      child

with three rules and no others:

* **Scalars are atoms** on the node's own line, in field order. A string is
  quoted with `lexer.quote`, so the escapes are the language's; a bool is
  `true` or `false`; a `Float` is spelled the way `PRIMITIVES.md` 3.3 says.
* **Sub-nodes are children**, on following lines, in field order.
* **A list is a `list N` line** with its N elements beneath it, and an absent
  optional is a `none` line. Both occupy the child position they would have,
  so the arity of a node is fixed and a reader never has to guess whether a
  line is a missing field or the next field.

Only what the *parser* writes is dumped. The fields inference fills in later --
`EVar.use`, `FunDecl.dicts`, `EIndex.get_fn` -- are not syntax and are absent.
The method references the parser *does* write (`EBinary.fn`, `EQuestion.
bind_fn`, `EForIn.iter_fn`) are dumped, because a port that forgot to write one
would otherwise pass M20 and fail much later.
"""

from __future__ import annotations

from . import ast
from .lexer import quote
from .types import float_to_string


def dump(program: ast.Program) -> str:
    out: list[str] = []
    _Writer(out).node(program)
    return "".join(out)


class _Writer:
    def __init__(self, out: list[str]):
        self.out = out
        self.depth = 0

    # -- emitting ---------------------------------------------------------

    def line(self, text: str) -> None:
        self.out.append("  " * self.depth + text + "\n")

    def head(self, tag: str, node: ast.Node, *atoms: str) -> None:
        where = f"{node.span.line}:{node.span.col}"
        self.line(" ".join([f"{tag}@{where}", *atoms]))

    def none(self) -> None:
        self.line("none")

    def maybe(self, node) -> None:
        if node is None:
            self.none()
        else:
            self.node(node)

    def items(self, nodes) -> None:
        self.line(f"list {len(nodes)}")
        self.depth += 1
        for item in nodes:
            self.node(item)
        self.depth -= 1

    def names(self, values) -> None:
        """A list of bare strings, which are leaves rather than nodes."""
        self.line(f"list {len(values)}")
        self.depth += 1
        for value in values:
            self.line(quote(value))
        self.depth -= 1

    def pairs(self, fields) -> None:
        """`[(label, node)]`, as it appears in a record literal or pattern."""
        self.line(f"list {len(fields)}")
        self.depth += 1
        for label, value in fields:
            self.line(f"field {quote(label)}")
            self.depth += 1
            self.node(value)
            self.depth -= 1
        self.depth -= 1

    def kids(self, *emit) -> None:
        self.depth += 1
        for thunk in emit:
            thunk()
        self.depth -= 1

    # -- the walk ---------------------------------------------------------

    def node(self, n) -> None:
        handler = _HANDLERS.get(type(n))
        if handler is None:  # pragma: no cover - a node added without a case
            raise AssertionError(f"no ast dump rule for {type(n).__name__}")
        handler(self, n)


def _literal(kind: str, value: object) -> str:
    if kind == "Float":
        return float_to_string(value)
    if kind == "Int":
        return str(value)
    return quote(value)


# Each rule writes the node's own line, then its children in field order.
# Written as a table rather than as methods so that a node type with no rule is
# a `KeyError` at the one place that looks one up, rather than a silently
# inherited base-class case.
def _rules():
    def program(w: _Writer, n: ast.Program) -> None:
        w.head("program", n)
        w.kids(lambda: w.maybe(n.header),
               lambda: w.items(n.imports),
               lambda: w.items(n.decls))

    def header(w: _Writer, n: ast.ModuleHeader) -> None:
        w.head("header", n, quote(n.name))
        w.kids(lambda: w.items(n.exports) if n.exports is not None else w.none())

    def export(w: _Writer, n: ast.ExportItem) -> None:
        w.head("export", n, quote(n.name), quote(n.kind))
        w.kids(lambda: w.names(n.subs) if n.subs is not None else w.none())

    def import_(w: _Writer, n: ast.ImportDecl) -> None:
        w.head("import", n, quote(n.name), _bool(n.qualified))
        w.kids(lambda: w.line(quote(n.alias)) if n.alias is not None else w.none(),
               lambda: w.items(n.items) if n.items is not None else w.none(),
               lambda: w.items(n.hiding) if n.hiding is not None else w.none())

    # -- types ------------------------------------------------------------

    def tevar(w: _Writer, n: ast.TEVar) -> None:
        w.head("tevar", n, quote(n.name))

    def tecon(w: _Writer, n: ast.TECon) -> None:
        w.head("tecon", n, quote(n.name))
        w.kids(lambda: w.items(n.args))

    def teapp(w: _Writer, n: ast.TEApp) -> None:
        w.head("teapp", n)
        w.kids(lambda: w.node(n.fn), lambda: w.items(n.args))

    def tetuple(w: _Writer, n: ast.TETuple) -> None:
        w.head("tetuple", n)
        w.kids(lambda: w.items(n.elems))

    def tefun(w: _Writer, n: ast.TEFun) -> None:
        w.head("tefun", n)
        w.kids(lambda: w.items(n.params), lambda: w.node(n.ret))

    # -- patterns ---------------------------------------------------------

    def pvar(w: _Writer, n: ast.PVar) -> None:
        w.head("pvar", n, quote(n.name))

    def pwild(w: _Writer, n: ast.PWild) -> None:
        w.head("pwild", n)

    def pcon(w: _Writer, n: ast.PCon) -> None:
        w.head("pcon", n, quote(n.name))
        w.kids(lambda: w.items(n.args))

    def precord(w: _Writer, n: ast.PRecord) -> None:
        w.head("precord", n, quote(n.name))
        w.kids(lambda: w.pairs(n.fields))

    def plit(w: _Writer, n: ast.PLit) -> None:
        w.head("plit", n, quote(n.kind), _literal(n.kind, n.value))

    def ptuple(w: _Writer, n: ast.PTuple) -> None:
        w.head("ptuple", n)
        w.kids(lambda: w.items(n.elems))

    def pannot(w: _Writer, n: ast.PAnnot) -> None:
        w.head("pannot", n)
        w.kids(lambda: w.node(n.pat), lambda: w.node(n.type_expr))

    # -- expressions ------------------------------------------------------

    def elit(w: _Writer, n: ast.ELit) -> None:
        w.head("elit", n, quote(n.kind), _literal(n.kind, n.value))

    def eunit(w: _Writer, n: ast.EUnit) -> None:
        w.head("eunit", n)

    def evar(w: _Writer, n: ast.EVar) -> None:
        w.head("evar", n, quote(n.name), _bool(n.method))

    def econ(w: _Writer, n: ast.ECon) -> None:
        w.head("econ", n, quote(n.name))

    def etuple(w: _Writer, n: ast.ETuple) -> None:
        w.head("etuple", n)
        w.kids(lambda: w.items(n.elems))

    def earray(w: _Writer, n: ast.EArray) -> None:
        w.head("earray", n)
        w.kids(lambda: w.items(n.elems))

    def erecord(w: _Writer, n: ast.ERecord) -> None:
        w.head("erecord", n, quote(n.con))
        w.kids(lambda: w.pairs(n.fields))

    def elambda(w: _Writer, n: ast.ELambda) -> None:
        w.head("elambda", n)
        w.kids(lambda: w.items(n.params),
               lambda: w.maybe(n.ret),
               lambda: w.node(n.body))

    def ecall(w: _Writer, n: ast.ECall) -> None:
        w.head("ecall", n)
        w.kids(lambda: w.node(n.fn), lambda: w.items(n.args))

    def eindex(w: _Writer, n: ast.EIndex) -> None:
        w.head("eindex", n)
        w.kids(lambda: w.node(n.arr), lambda: w.node(n.index))

    def efield(w: _Writer, n: ast.EField) -> None:
        w.head("efield", n, quote(n.name))
        w.kids(lambda: w.node(n.obj))

    def eproject(w: _Writer, n: ast.EProject) -> None:
        w.head("eproject", n, str(n.index))
        w.kids(lambda: w.node(n.obj))

    def eunary(w: _Writer, n: ast.EUnary) -> None:
        w.head("eunary", n, quote(n.op))
        w.kids(lambda: w.node(n.operand), lambda: w.maybe(n.fn))

    def ebinary(w: _Writer, n: ast.EBinary) -> None:
        w.head("ebinary", n, quote(n.op))
        w.kids(lambda: w.node(n.left),
               lambda: w.node(n.right),
               lambda: w.maybe(n.fn))

    def eannot(w: _Writer, n: ast.EAnnot) -> None:
        w.head("eannot", n)
        w.kids(lambda: w.node(n.expr), lambda: w.node(n.type_expr))

    def eif(w: _Writer, n: ast.EIf) -> None:
        w.head("eif", n)
        w.kids(lambda: w.node(n.cond),
               lambda: w.node(n.then),
               lambda: w.maybe(n.otherwise))

    def ewhile(w: _Writer, n: ast.EWhile) -> None:
        w.head("ewhile", n)
        w.kids(lambda: w.node(n.cond), lambda: w.node(n.body))

    def eforin(w: _Writer, n: ast.EForIn) -> None:
        w.head("eforin", n)
        w.kids(lambda: w.node(n.pat),
               lambda: w.node(n.iterable),
               lambda: w.node(n.body),
               lambda: w.maybe(n.iter_fn),
               lambda: w.maybe(n.next_fn))

    def eforc(w: _Writer, n: ast.EForC) -> None:
        w.head("eforc", n)
        w.kids(lambda: w.maybe(n.init),
               lambda: w.node(n.cond),
               lambda: w.maybe(n.step),
               lambda: w.node(n.body))

    def eloop(w: _Writer, n: ast.ELoop) -> None:
        w.head("eloop", n)
        w.kids(lambda: w.node(n.body))

    def arm(w: _Writer, n: ast.MatchArm) -> None:
        w.head("arm", n)
        w.kids(lambda: w.items(n.patterns), lambda: w.node(n.body))

    def ematch(w: _Writer, n: ast.EMatch) -> None:
        w.head("ematch", n)
        w.kids(lambda: w.node(n.scrutinee), lambda: w.items(n.arms))

    def ereturn(w: _Writer, n: ast.EReturn) -> None:
        w.head("ereturn", n)
        w.kids(lambda: w.maybe(n.value))

    def ebreak(w: _Writer, n: ast.EBreak) -> None:
        w.head("ebreak", n)
        w.kids(lambda: w.maybe(n.value))

    def econtinue(w: _Writer, n: ast.EContinue) -> None:
        w.head("econtinue", n)

    def eblock(w: _Writer, n: ast.EBlock) -> None:
        w.head("eblock", n)
        w.kids(lambda: w.items(n.stmts))

    def equestion(w: _Writer, n: ast.EQuestion) -> None:
        w.head("equestion", n)
        w.kids(lambda: w.node(n.expr), lambda: w.maybe(n.bind_fn))

    def edo(w: _Writer, n: ast.EDo) -> None:
        w.head("edo", n)
        w.kids(lambda: w.node(n.body))

    # -- statements -------------------------------------------------------

    def slet(w: _Writer, n: ast.SLet) -> None:
        w.head("slet", n)
        w.kids(lambda: w.node(n.pat), lambda: w.node(n.value))

    def svar(w: _Writer, n: ast.SVar) -> None:
        w.head("svar", n)
        w.kids(lambda: w.node(n.pat), lambda: w.node(n.value))

    def sfun(w: _Writer, n: ast.SFun) -> None:
        w.head("sfun", n)
        w.kids(lambda: w.node(n.decl))

    def sassign(w: _Writer, n: ast.SAssign) -> None:
        w.head("sassign", n)
        w.kids(lambda: w.node(n.target), lambda: w.node(n.value))

    def sexpr(w: _Writer, n: ast.SExpr) -> None:
        w.head("sexpr", n)
        w.kids(lambda: w.node(n.expr))

    # -- declarations -----------------------------------------------------

    def classpred(w: _Writer, n: ast.ClassPred) -> None:
        w.head("pred", n, quote(n.name))
        w.kids(lambda: w.node(n.arg))

    def eqpred(w: _Writer, n: ast.EqPred) -> None:
        w.head("eqpred", n)
        w.kids(lambda: w.node(n.left), lambda: w.node(n.right))

    def fundecl(w: _Writer, n: ast.FunDecl) -> None:
        w.head("fun", n, quote(n.name))
        w.kids(lambda: w.items(n.params),
               lambda: w.maybe(n.ret),
               lambda: w.maybe(n.body),
               lambda: w.items(n.context))

    def condecl(w: _Writer, n: ast.ConDecl) -> None:
        w.head("con", n, quote(n.name))
        w.kids(lambda: w.items(n.args),
               lambda: w.pairs(n.fields) if n.fields is not None else w.none())

    def typedecl(w: _Writer, n: ast.TypeDecl) -> None:
        w.head("type", n, quote(n.name))
        w.kids(lambda: w.names(n.params),
               lambda: w.items(n.variants) if n.variants is not None else w.none(),
               lambda: w.maybe(n.alias))

    def famdecl(w: _Writer, n: ast.FamDecl) -> None:
        w.head("family", n, quote(n.name), quote(n.param))

    def fambind(w: _Writer, n: ast.FamBind) -> None:
        w.head("fambind", n, quote(n.name))
        w.kids(lambda: w.node(n.body))

    def classdecl(w: _Writer, n: ast.ClassDecl) -> None:
        w.head("class", n, quote(n.name), quote(n.param))
        w.kids(lambda: w.items(n.supers),
               lambda: w.items(n.methods),
               lambda: w.items(n.families))

    def instancedecl(w: _Writer, n: ast.InstanceDecl) -> None:
        w.head("instance", n, quote(n.cls))
        w.kids(lambda: w.node(n.head),
               lambda: w.items(n.context),
               lambda: w.items(n.methods),
               lambda: w.items(n.families))

    return {
        ast.Program: program, ast.ModuleHeader: header,
        ast.ExportItem: export, ast.ImportDecl: import_,
        ast.TEVar: tevar, ast.TECon: tecon, ast.TEApp: teapp,
        ast.TETuple: tetuple, ast.TEFun: tefun,
        ast.PVar: pvar, ast.PWild: pwild, ast.PCon: pcon,
        ast.PRecord: precord, ast.PLit: plit, ast.PTuple: ptuple,
        ast.PAnnot: pannot,
        ast.ELit: elit, ast.EUnit: eunit, ast.EVar: evar, ast.ECon: econ,
        ast.ETuple: etuple, ast.EArray: earray, ast.ERecord: erecord,
        ast.ELambda: elambda, ast.ECall: ecall, ast.EIndex: eindex,
        ast.EField: efield, ast.EProject: eproject, ast.EUnary: eunary,
        ast.EBinary: ebinary, ast.EAnnot: eannot, ast.EIf: eif,
        ast.EWhile: ewhile, ast.EForIn: eforin, ast.EForC: eforc,
        ast.ELoop: eloop, ast.MatchArm: arm, ast.EMatch: ematch,
        ast.EReturn: ereturn, ast.EBreak: ebreak, ast.EContinue: econtinue,
        ast.EBlock: eblock, ast.EQuestion: equestion, ast.EDo: edo,
        ast.SLet: slet, ast.SVar: svar, ast.SFun: sfun,
        ast.SAssign: sassign, ast.SExpr: sexpr,
        ast.ClassPred: classpred, ast.EqPred: eqpred,
        ast.FunDecl: fundecl, ast.ConDecl: condecl, ast.TypeDecl: typedecl,
        ast.FamDecl: famdecl, ast.FamBind: fambind,
        ast.ClassDecl: classdecl, ast.InstanceDecl: instancedecl,
    }


def _bool(flag: bool) -> str:
    return "true" if flag else "false"


_HANDLERS = _rules()

__all__ = ["dump"]
