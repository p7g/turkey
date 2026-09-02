"""Command line entry point: `python -m turkey <command> FILE`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import llvmgen, pygen
from .driver import check, report_warnings, run
from .errors import TurkeyError, TurkeyPanic
from .astdump import dump as dump_ast
from .core import show_program
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
        ("core", "dump the typed Core the elaboration produces"),
        ("mono", "dump that Core with its polymorphism specialized away"),
        ("opt", "dump that Core with the optimizations applied"),
        ("python", "print the generated Python source without running it"),
        ("llvm", "print verified LLVM IR without running it"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("file")
        if name == "run":
            p.add_argument("--backend", choices=("llvm", "python"),
                           default="llvm")
            # Everything after the file is the *program's*, not turkey's, so it
            # is taken verbatim rather than parsed: a compiler written in
            # Turkey wants to be handed `-o out.c` without argparse claiming
            # the `-o` first. A leading `--` is the separator and is dropped.
            # `--backend` therefore has to come before the file, which is the
            # ordinary shape of a flag anyway.
            p.add_argument("args", nargs=argparse.REMAINDER,
                           help="arguments passed to the program, after `--`")

    args = parser.parse_args(argv)
    try:
        src = open(args.file, encoding="utf-8").read()
    except OSError as exc:
        print(f"turkey: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "tokens":
            for token in tokenize(src):
                print(token.canonical())
        elif args.command == "ast":
            print(dump_ast(parse(src)), end="")
        elif args.command == "core":
            checked = check(src, args.file, [Path(args.file).resolve().parent])
            report_warnings(checked.warnings, args.file)
            print(show_program(checked.core, checked.module), end="")
        elif args.command == "mono":
            checked = check(src, args.file, [Path(args.file).resolve().parent])
            report_warnings(checked.warnings, args.file)
            print(show_program(checked.mono, checked.module), end="")
        elif args.command == "opt":
            checked = check(src, args.file, [Path(args.file).resolve().parent])
            report_warnings(checked.warnings, args.file)
            print(show_program(checked.opt, checked.module), end="")
        elif args.command == "python":
            checked = check(src, args.file, [Path(args.file).resolve().parent])
            report_warnings(checked.warnings, args.file)
            print(pygen.generate(checked.opt, checked.decls, checked.main), end="")
        elif args.command == "llvm":
            checked = check(src, args.file, [Path(args.file).resolve().parent])
            report_warnings(checked.warnings, args.file)
            print(llvmgen.generate(checked.opt, checked.decls, checked.main), end="")
        elif args.command == "types":
            checked = check(src, args.file, [Path(args.file).resolve().parent])
            report_warnings(checked.warnings, args.file)
            for name, scheme in checked.signatures:
                print(f"{name} : {show_scheme(scheme)}")
        else:
            rest = list(args.args)
            if rest and rest[0] == "--":
                rest = rest[1:]
            run(src, args.file, rest, args.backend)
    except TurkeyError as exc:
        print(exc.render(args.file), file=sys.stderr)
        return 1
    except TurkeyPanic as exc:
        print(exc.render(args.file), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
