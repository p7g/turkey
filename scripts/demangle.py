"""Rewrites Turkey symbols to their display form, as `-g`'s DWARF names them.

    sample prog 5 -file report.txt && python3 scripts/demangle.py report.txt
    perf script | python3 scripts/demangle.py
    python3 scripts/demangle.py < crash.ips

Reads the files named, or standard input, and writes them with every Turkey
symbol in its display form: `Main#area@Rectangle` is `Main.area[Rectangle]`,
`%inst.Data.Hashable#Hash.Int#hashInto@FNV1a` is
`Hash(Int).hashInto[FNV1a]`. A Mach-O symbol's leading `_` goes with it.
Everything else passes through unchanged.

For a profiler or a crash report that reads no DWARF, or a build without
`-g`. `display_name` is `Turkey.Demangle.displayName` written again, and
`tests/test_debug_info.py` checks the two against each other on every
function `boot -g` names; the rules are in `src/Turkey/Demangle.gob`.
"""
import re
import sys

# A Turkey symbol in running text: a module's name and `#`, or a name the
# compiler made, starting with `%`; then what may follow in a symbol. `+`, a
# space and a quote end it, as they end a symbol in `perf script` and
# `sample` output and in a crash report.
SYMBOL = re.compile(
    r"(?<![\w.#%@~'])_?"
    r"(?:%[A-Za-z]|[A-Z][\w']*(?:\.[A-Z][\w']*)*#)"
    r"[\w'.#%@~(),\[\]{}<>!=*/|&^$-]*")

OPERATOR = set("!#$%&*+-/<=>@^|~")
SEPARATORS = set("#%~@")


def _is_name_char(c: str) -> bool:
    return c.isascii() and (c.isalnum() or c in "_'") or not c.isascii()


class _Scan:
    def __init__(self, name: str):
        self.chars = name
        self.at = 0
        self.out: list[str] = []

    def peek(self, ahead: int = 0) -> str | None:
        k = self.at + ahead
        return self.chars[k] if k < len(self.chars) else None

    def ident(self) -> str:
        start = self.at
        while self.at < len(self.chars) and _is_name_char(self.chars[self.at]):
            self.at += 1
        return self.chars[start:self.at]

    def rest(self) -> None:
        while self.at < len(self.chars):
            c = self.chars[self.at]
            self.at += 1
            if c == "#":
                self.out.append(".")
                self.operator()
            elif c == "%":
                self.made()
            elif c == "@":
                self.specialization()
            else:
                self.out.append(c)

    def made(self) -> None:
        if self.out and self.out[-1] != ".":
            self.out.append(".")
        self.out.append("<" + self.ident() + ">")

    def specialization(self) -> None:
        if self.peek() == "[":
            self.at += 1
            start = self.at
            while self.at < len(self.chars) and self.chars[self.at] != "]":
                self.at += 1
            self.out.append("{" + self.chars[start:self.at] + "}")
            self.at += 1
        else:
            args = self.type_arguments()
            if args:
                self.out.append("[" + args + "]")

    def type_arguments(self) -> str:
        start = self.at
        depth = 0
        while self.at < len(self.chars):
            c = self.chars[self.at]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif depth == 0 and c in SEPARATORS:
                break
            self.at += 1
        return self.chars[start:self.at]

    def operator(self) -> None:
        while self.at < len(self.chars):
            c = self.chars[self.at]
            if c not in OPERATOR:
                return
            following = self.peek(1)
            if c in SEPARATORS and following is not None and (
                    _is_name_char(following) or following in "(["):
                return
            self.out.append(c)
            self.at += 1

    def instance(self) -> None:
        cls = self.class_name()
        self.out.append(cls + "(" + self.instance_type() + ")")

    def class_default(self) -> None:
        start = self.at
        cls = self.class_name()
        prefix = self.chars[start:self.at]
        self.out.append(cls + ".<default>.")
        if self.chars.startswith(prefix, self.at):
            self.at += len(prefix)

    def class_name(self) -> str:
        if self.peek() == "%":
            self.at += 1
            head = self.ident()
            self.at += 1
            label = self.ident()
            self.at += 1
            return head + "." + label
        last = self.ident()
        while self.peek() in (".", "#"):
            hash_ = self.peek() == "#"
            self.at += 1
            last = self.ident()
            if hash_:
                break
        self.at += 1
        return last

    def instance_type(self) -> str:
        last = self.ident()
        while self.peek() in (".", "#") and (self.peek(1) or "").isascii() \
                and (self.peek(1) or "").isupper():
            self.at += 1
            last = self.ident()
        # A derived `Typed` instance's count of arguments.
        if self.peek() == "/":
            self.at += 1
            while (self.peek() or "").isdigit():
                self.at += 1
        if self.peek() == "@" and self.peek(1) != "[":
            self.at += 1
            args = self.type_arguments()
            if args:
                return last + "(" + args + ")"
        return last


def display_name(name: str) -> str:
    """Returns the display form of the internal name `name`, which is a
    symbol without a Mach-O symbol's leading `_`."""
    s = _Scan(name)
    if name.startswith("%inst."):
        s.at = len("%inst.")
        s.instance()
    elif name.startswith("%default."):
        s.at = len("%default.")
        s.class_default()
    s.rest()
    return "".join(s.out)


def _trimmed(symbol: str) -> tuple[str, str]:
    """Splits off the closing brackets and commas the pattern took from the
    text after a symbol: those its own opening brackets leave unmatched."""
    end = len(symbol)
    while end > 0 and symbol[end - 1] in ",)]}":
        body = symbol[:end]
        if symbol[end - 1] == "," or any(
                body.count(o) < body.count(c) for o, c in ("()", "[]", "{}")):
            end -= 1
        else:
            break
    return symbol[:end], symbol[end:]


def _replace(match: re.Match) -> str:
    symbol, after = _trimmed(match.group(0))
    if symbol.startswith("_"):
        symbol = symbol[1:]
    return display_name(symbol) + after


def demangle(text: str) -> str:
    """Returns `text` with every Turkey symbol in it in its display form."""
    return SYMBOL.sub(_replace, text)


def main(argv: list[str]) -> None:
    files = [open(path, encoding="utf-8", errors="replace") for path in argv] or [sys.stdin]
    for f in files:
        for line in f:
            sys.stdout.write(demangle(line))


if __name__ == "__main__":
    main(sys.argv[1:])
