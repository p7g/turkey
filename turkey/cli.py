"""Command line entry point: `python -m turkey <command> FILE`."""

from __future__ import annotations

import argparse
import sys

from .driver import check, report_warnings, run
from .errors import TurkeyError, TurkeyPanic
from .lexer import tokenize
from .parser import parse
from .types import show_scheme


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="turkey", description="turkey-lite prototype")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [
        ("run", "type-check and execute a program"),
        ("types", "print the inferred type of each top-level binding"),
        ("tokens", "dump the token stream"),
        ("ast", "dump the parse tree"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("file")

    args = parser.parse_args(argv)
    try:
        src = open(args.file, encoding="utf-8").read()
    except OSError as exc:
        print(f"turkey: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "tokens":
            for token in tokenize(src):
                print(token)
        elif args.command == "ast":
            print(parse(src))
        elif args.command == "types":
            checked = check(src)
            report_warnings(checked.warnings, args.file)
            for name, scheme in checked.signatures:
                print(f"{name} : {show_scheme(scheme)}")
        else:
            run(src, args.file)
    except TurkeyError as exc:
        print(exc.render(args.file), file=sys.stderr)
        return 1
    except TurkeyPanic as exc:
        print(f"panic: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
