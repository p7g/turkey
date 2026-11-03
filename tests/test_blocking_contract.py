"""Every C call that can wait outside the program is carried, or excused.

`Turkey.Workers`' comment, under "C calls that may block", states the
contract: such a call goes through one of that module's blocking entries, so
that the monitor can hand a waiting thread's run permit to another. The
comment lists the `Turkey.Libc` functions that never wait, and the waiting
calls that other modules may make inline. This reads both lists from that
comment and checks every call to `Turkey.Libc` in `lib/` against them, so a
new inline call to a function that waits fails here, rather than stopping a
program that runs with one permit.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests import bootc

LIB = bootc.REPO_ROOT / "lib"
LIBC = LIB / "Turkey" / "Libc.gob"
WORKERS = LIB / "Turkey" / "Workers.gob"

NEVER_WAIT = "**Calls that never wait**"
INLINE = "**Calls that can wait, made inline**"

# The names a bullet begins with: "`a`, `b` and `c`: the reason".
_LEADING_NAMES = re.compile(r"^((?:`[^`]+`(?:, | and )?)+):\s")
_FOREIGN = re.compile(r'^foreign\s+"[^"]+"\s+fun\s+(\w+)', re.MULTILINE)
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_IMPORT = re.compile(r"^import\s+Turkey\.Libc\b(.*)$")
_CALL = re.compile(r"\bLibc\.(\w+)")


def _bullets(heading: str) -> list[str]:
    """The bullets of the list under `heading` in `Turkey.Workers`' comment,
    each with its continuation lines joined to it. The list ends at the first
    blank comment line after a bullet."""
    lines = WORKERS.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.startswith("-- " + heading)), None)
    assert start is not None, f"no {heading!r} in {WORKERS}"
    bullets: list[str] = []
    for line in lines[start + 1:]:
        if not line.startswith("--"):
            break
        text = line[2:].strip()
        if text.startswith("* "):
            bullets.append(text[2:])
        elif text and bullets:
            bullets[-1] += " " + text
        elif not text and bullets:
            break
    return bullets


def _names(heading: str) -> list[str]:
    """Every name the bullets under `heading` begin with."""
    names = []
    for bullet in _bullets(heading):
        match = _LEADING_NAMES.match(bullet)
        assert match, f"a bullet under {heading!r} names nothing: {bullet!r}"
        names += re.findall(r"`([^`]+)`", match.group(1))
    assert names, f"nothing listed under {heading!r}"
    return names


def _libc() -> set[str]:
    return set(_FOREIGN.findall(LIBC.read_text(encoding="utf-8")))


def _never_wait() -> set[str]:
    return set(_names(NEVER_WAIT))


def _inline() -> set[tuple[str, str]]:
    """The excused calls, as (module, function)."""
    out = set()
    for name in _names(INLINE):
        module, _, function = name.rpartition(".")
        out.add((module, function))
    return out


def _module(path: Path) -> str:
    return ".".join(path.relative_to(LIB).with_suffix("").parts)


def _calls() -> list[tuple[str, str, str]]:
    """Every call to `Turkey.Libc` in `lib/` outside `Turkey.Libc` and
    `Turkey.Workers`: (module, function, where)."""
    calls = []
    for path in sorted(LIB.rglob("*.gob")):
        module = _module(path)
        if module in ("Turkey.Libc", "Turkey.Workers"):
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        imported = False
        for number, line in enumerate(lines, 1):
            code = _STRING.sub('""', line).split("--", 1)[0]
            if (found := _IMPORT.match(code)):
                assert found.group(1).strip() == "as Libc", (
                    f"{path}:{number}: import `Turkey.Libc` as `Libc`, so that "
                    "this test can find the module's calls to it")
                imported = True
        if not imported:
            continue
        for number, line in enumerate(lines, 1):
            code = _STRING.sub('""', line).split("--", 1)[0]
            for function in _CALL.findall(code):
                where = f"{path.relative_to(bootc.REPO_ROOT)}:{number}"
                calls.append((module, function, where))
    return calls


def test_the_lists_name_only_libc_functions():
    libc = _libc()
    assert libc, f"no foreign declarations found in {LIBC}"
    never = _never_wait()
    assert never <= libc, f"not in Turkey.Libc: {sorted(never - libc)}"
    for module, function in _inline():
        path = LIB.joinpath(*module.split(".")).with_suffix(".gob")
        assert path.is_file(), f"no module {module} for {module}.{function}"
        assert function in libc, f"{module}.{function}: not in Turkey.Libc"
        assert function not in never, (
            f"{module}.{function}: `{function}` is also listed as never waiting")


def test_every_call_that_can_wait_is_carried_or_excused():
    libc = _libc()
    never = _never_wait()
    inline = _inline()
    unknown = [f"{where}: Libc.{f}" for _, f, where in _calls() if f not in libc]
    assert not unknown, "calls to no Turkey.Libc function:\n" + "\n".join(unknown)
    waiting = [f"{where}: {module} calls `{f}` inline"
               for module, f, where in _calls()
               if f not in never and (module, f) not in inline]
    assert not waiting, (
        "these C calls can wait on something outside the program; make each "
        "through a `Turkey.Workers.blocking*` entry, or list it, with its "
        "reason, in the contract in Turkey.Workers:\n" + "\n".join(waiting))


def test_no_excuse_outlives_its_call():
    made = {(module, f) for module, f, _ in _calls()}
    stale = sorted(f"{m}.{f}" for m, f in _inline() - made)
    assert not stale, ("listed as made inline, but no longer called there: "
                       + ", ".join(stale))
