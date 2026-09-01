"""Non-CI comparison of the evaluator and generated Python backend."""

from __future__ import annotations

import argparse
import io
import statistics
import time
from contextlib import redirect_stdout
from pathlib import Path

from turkey.builtins import initial_values
from turkey.driver import check
from turkey.eval import Evaluator
from turkey.pygen import _runtime_namespace, generate


LOOP = """
fun count(n : Int) -> Int {
    var i = 0
    while i < n { i = i + 1 }
    i
}
fun main() { let answer = count(200000) }
"""


def timed(fn, rounds: int) -> float:
    samples = []
    sink = io.StringIO()
    for _ in range(rounds):
        start = time.perf_counter()
        with redirect_stdout(sink):
            fn()
        samples.append(time.perf_counter() - start)
    return statistics.median(samples)


def benchmark(name: str, source: str, filename: str, search: list[Path] | None,
              rounds: int) -> float:
    checked = check(source, filename, search)
    start = time.perf_counter()
    generated = generate(checked.opt, checked.decls, checked.main)
    generate_time = time.perf_counter() - start
    start = time.perf_counter()
    code = compile(generated, filename, "exec")
    compile_time = time.perf_counter() - start
    namespace = _runtime_namespace()
    exec(code, namespace)
    runner = namespace["__turkey_run"]

    interpreted = timed(
        lambda: Evaluator(checked.decls, initial_values()).run(
            checked.opt, checked.main), rounds)
    compiled = timed(runner, rounds)
    ratio = interpreted / compiled
    print(
        f"{name}: evaluator {interpreted:.6f}s, Python {compiled:.6f}s, "
        f"{ratio:.2f}x; generate {generate_time:.6f}s, "
        f"compile {compile_time:.6f}s")
    return ratio


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bf = root / "tests/programs/bf.tl"
    ratios = [
        benchmark("join-loop", LOOP, "<join-loop>", None, args.rounds),
        benchmark("bf", bf.read_text(encoding="utf-8"), str(bf),
                  [bf.parent.resolve()], args.rounds),
    ]
    return 0 if min(ratios) >= 3.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
