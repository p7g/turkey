"""Regenerate the golden `.expected` files under tests/programs.

Run from the repository root: `python3 -m tests.regenerate_expected`. Each
program is compiled and run as `tests/harness/` runs it: by `boot` from its
own directory, then linked and run there, its output being stdout then
stderr. A program that says `--@ xfail` is left alone, since its golden
records what it is expected *not* to print yet; writing what it does print
would delete that.

Review the diff before committing: a changed golden is either a fix or a
regression, and only you can tell which. Nothing else checks these files now;
there is no second compiler that has to agree with them.
"""

from __future__ import annotations

import pathlib

from tests import lang

PROGRAMS = lang.CORPUS


def sources() -> list[pathlib.Path]:
    """Every program: a single `.gob` file, or a directory's `Main.gob`."""
    bundles = [p / "Main.gob" for p in sorted(PROGRAMS.iterdir())
               if p.is_dir() and (p / "Main.gob").is_file()]
    return sorted(PROGRAMS.glob("*.gob")) + bundles


def name_of(source: pathlib.Path) -> str:
    return source.parent.name if source.name == "Main.gob" else source.stem


def directives(program: pathlib.Path) -> dict[str, str]:
    """The program's `--@ name: value` lines, as the harness reads them."""
    found = {}
    for line in program.read_text().splitlines():
        if line.startswith("--@"):
            name, _, value = line[3:].partition(":")
            found[name.strip()] = value.strip()
    return found


def conformance(program: pathlib.Path) -> tuple[str, int]:
    """What the program printed, stdout then stderr, and its status."""
    try:
        result = lang.run(program)
    except lang.CompileError as error:
        return error.rendered, error.code
    return result.stdout + result.stderr, result.code


def main() -> int:
    for source in sources():
        said = directives(source)
        if "xfail" in said:
            print(f"{name_of(source)}: kept, since it says --@ xfail: {said['xfail']}")
            continue
        output, code = conformance(source)
        source.with_suffix(".expected").write_text(output, encoding="utf-8")
        if "error" in said or "panic" in said:
            ended_as_said = code != 0
        else:
            ended_as_said = code == int(said.get("exit", "0"))
        if not ended_as_said:
            print(f"warning: {name_of(source)} exited {code}, which its directives do not say")
        print(f"{name_of(source)}: exit {code}, {len(output.splitlines())} line(s)")

    # Only programs that already have a `.types`, `.core`, `.mono` or `.opt`
    # get one regenerated; adding a new one is a deliberate act, not a side
    # effect of running this.
    for suffix, command in ((".types", "types"), (".core", "core"),
                            (".mono", "mono"), (".opt", "opt")):
        for golden in sorted(PROGRAMS.glob(f"*{suffix}")):
            result = lang.dump(command, golden.with_suffix(".gob"))
            golden.write_text(result.stdout + result.stderr, encoding="utf-8")
            print(f"{golden.name}: exit {result.code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
