"""`Target.os` and `Target.arch`, and the `--target` option that sets them.

What a program can observe is in `docs/ref/modules.md`, whose examples
`test_reference` runs. Here: the command line, and the claim the reference
makes about compiling -- that only the chosen target's arm of a
`match Target.os` survives to be compiled.

The test that matters most cannot be written yet: a program whose other arm
calls a `foreign` symbol that exists only on the other target, checked absent
from the output. With one target there is no other arm. It lands with the
second target.
"""

from __future__ import annotations

import subprocess

from tests import bootc, lang

MATCHES = """\
import Target (OS(..), Arch(..))
import Target as Target

fun signalName(n : Int) -> String = match Target.os {
    Darwin -> if n == 10 { "SIGBUS" } else { "other" }
}

fun wordBytes() -> Int = match Target.arch {
    Arm64 -> 8
}

fun main() {
    print(signalName(10))
    print(wordBytes())
}
"""

SOURCE = bootc.REPO_ROOT / "tests" / "programs" / "constant_globals.gob"


def _boot(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([str(bootc.binary()), *args], cwd=bootc.REPO_ROOT,
                          capture_output=True)


def test_the_default_is_the_only_target() -> None:
    chosen = _boot("native", "--target", "arm64-darwin", str(SOURCE))
    default = _boot("native", str(SOURCE))
    assert chosen.returncode == 0, chosen.stderr
    assert chosen.stdout == default.stdout


def test_an_unknown_target_is_refused() -> None:
    result = _boot("check", "--target", "x86_64-linux", str(SOURCE))
    assert result.returncode == 2
    assert result.stderr.decode() == (
        "boot: unknown target 'x86_64-linux'; supported: arm64-darwin\n")


def test_target_without_a_value_is_a_usage_error() -> None:
    result = _boot("check", str(SOURCE), "--target")
    assert result.returncode == 2
    assert result.stderr.decode().startswith("boot: usage: ")


def test_a_target_match_runs_the_chosen_arm() -> None:
    assert lang.output(MATCHES) == "SIGBUS\n8\n"


def test_a_target_match_is_decided_before_lowering() -> None:
    """Nothing reads `Target.os` or `Target.arch` once the optimizer has run,
    and no `match` on either is left to compile."""
    result = lang.dump("opt", MATCHES)
    assert result.code == 0, result.stderr
    assert "Target#os" not in result.stdout
    assert "Target#arch" not in result.stdout
    assert "match" not in result.stdout


def test_every_arm_of_a_target_match_is_checked() -> None:
    """Exhaustiveness sees `OS` as an ordinary type.

    With one target a `match Target.os` cannot miss one, so this is the same
    check through a pair. The direct case -- a match that names `Darwin` and
    not a second target -- lands with the second target.
    """
    message = lang.fails("""\
import Target (OS(..))
import Target as Target

fun sigbus(fallback : Bool) -> Int = match (Target.os, fallback) {
    (Darwin, False) -> 10
}

fun main() {
    print(sigbus(False))
}
""")
    assert message == (
        "this match is not exhaustive; '(Darwin, True)' is not handled")
