"""Differential test: Algorithm J against a naive reference checker.

`tests/reference.py` computes types the obvious way -- an explicit substitution
and generalization by scanning the environment. The real inferencer uses Remy
levels instead, which is an optimization of exactly that scan. If the two ever
disagree the level bookkeeping is wrong, which is the failure mode that would
otherwise show up as a program wrongly accepted rather than as a crash.

Programs are generated from a seeded RNG rather than a property-testing
library, so the suite keeps its zero-dependency stance and every failure names
a seed that reproduces it exactly.
"""

from __future__ import annotations

import random

import pytest

from tests import reference
from turkey.driver import check as real_check
from turkey.errors import ParseError, TypeError_, Unsupported
from turkey.types import show_scheme

LITERALS = ["1", "2", "-3", '"s"', "true", "'c'"]


def gen_expr(rng: random.Random, scope: list[str], depth: int) -> str:
    kinds = ["lit", "lit"]
    if scope:
        kinds += ["var", "var"]
    if depth > 0:
        kinds += ["lambda", "call", "tuple", "apply_var"]

    kind = rng.choice(kinds)

    if kind == "lit":
        return rng.choice(LITERALS)

    if kind == "var":
        return rng.choice(scope)

    if kind == "lambda":
        params = [f"x{depth}{i}" for i in range(rng.randint(1, 2))]
        body = gen_expr(rng, scope + params, depth - 1)
        return f"fun({', '.join(params)}) = {body}"

    if kind == "call":
        # An inline lambda applied to matching arity: always well-formed, so
        # the generator produces programs that actually typecheck often enough
        # for the comparison to mean something.
        n = rng.randint(1, 2)
        params = [f"y{depth}{i}" for i in range(n)]
        body = gen_expr(rng, scope + params, depth - 1)
        args = ", ".join(gen_expr(rng, scope, depth - 1) for _ in range(n))
        return f"(fun({', '.join(params)}) = {body})({args})"

    if kind == "apply_var":
        # Applying a name of unknown type is where the interesting failures and
        # the interesting instantiations both live.
        if not scope:
            return rng.choice(LITERALS)
        n = rng.randint(1, 2)
        args = ", ".join(gen_expr(rng, scope, depth - 1) for _ in range(n))
        return f"{rng.choice(scope)}({args})"

    elems = ", ".join(gen_expr(rng, scope, depth - 1) for _ in range(rng.randint(2, 3)))
    return f"({elems})"


def gen_program(rng: random.Random, items: int) -> str:
    lines: list[str] = []
    scope: list[str] = []
    for i in range(items):
        name = f"v{i}"
        if rng.random() < 0.5:
            lines.append(f"let {name} = {gen_expr(rng, scope, 2)}")
        else:
            params = [f"p{i}{j}" for j in range(rng.randint(1, 2))]
            body = gen_expr(rng, scope + params, 2)
            lines.append(f"fun {name}({', '.join(params)}) = {body}")
        scope.append(name)
    return "\n".join(lines) + "\n"


def signatures(pairs) -> list[tuple[str, str]]:
    return [(name, show_scheme(scheme)) for name, scheme in pairs]


def _reference(src):
    try:
        return signatures(reference.check(src))
    except reference.RefError:
        return None


def _real(src):
    try:
        return signatures(real_check(src).signatures)
    except TypeError_:
        return None


@pytest.mark.parametrize("seed", range(400))
def test_levels_agree_with_scanning_the_environment(seed: int) -> None:
    rng = random.Random(seed)
    src = gen_program(rng, rng.randint(1, 4))

    try:
        expected = _reference(src)
    except (reference.Unsupported, ParseError, Unsupported):
        pytest.skip("outside the fragment the reference covers")

    actual = _real(src)

    if (expected is None) != (actual is None):
        rejected, accepted = ("reference", "J") if expected is None else ("J", "reference")
        pytest.fail(
            f"the two checkers disagree on whether this is well-typed:\n\n{src}\n"
            f"{rejected} rejected it, {accepted} accepted it "
            f"as {accepted is not None and (actual or expected)}"
        )
    if expected is not None:
        assert actual == expected, f"seed {seed}:\n{src}"


def test_the_generator_produces_programs_that_typecheck() -> None:
    """Guards the test above from passing vacuously.

    Every generated program being rejected, or skipped, would make the
    comparison above assert nothing at all.
    """
    accepted = 0
    considered = 0
    for seed in range(400):
        rng = random.Random(seed)
        src = gen_program(rng, rng.randint(1, 4))
        try:
            if _reference(src) is None:
                considered += 1
                continue
        except (reference.Unsupported, ParseError, Unsupported):
            continue
        considered += 1
        accepted += 1

    assert considered >= 200, f"only {considered} programs reached the comparison"
    assert accepted >= 50, f"only {accepted} of {considered} programs typechecked"
