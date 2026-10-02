"""`System.Parallel`: parallel maps, ordered consumption and dependency
graphs, whose results are the same at any number of workers.

Each program runs at one worker, where the functions are their sequential
loops, and at several, where they run on tasks in parallel; the output must
not differ. The graph programs also check that a step only ever sees its
dependencies finished.
"""

import pytest

from tests import lang

COUNTS = ["1", "2", "8"]

MAPS = """
import System.Parallel as Parallel

fun slow(n : Int) -> Int {
    var acc = 0
    for var i = 0; i < 2000 + n % 7 * 500; i = i + 1 { acc = (acc + i * n) % 1000003 }
    acc
}

fun main() {
    let xs = []
    for var i = 0; i < 1000; i = i + 1 { Array.push(xs, i) }
    let ys = Parallel.map(xs, slow)
    var sum = 0
    for y in ys { sum = (sum + y) % 1000003 }
    print(len(ys))
    print(sum)
    print(ys[0] + ys[999])
    -- Strings, so that every result is a heap object made on some worker.
    let names = Parallel.map(xs, fun(x) = "n" + Int.toString(x))
    print(names[0] + names[500] + names[999])
    -- In order, and every one.
    var expect = 0
    var inOrder = True
    Parallel.mapInOrder(xs, fun(x) = x * 2, fun(i, y) {
        if i != expect || y != i * 2 { inOrder = False }
        expect = expect + 1
    })
    print(inOrder && expect == 1000)
    print(Parallel.map([], slow))
    Parallel.each(xs, fun(x) { let _ = slow(x) })
    print(Parallel.workers() >= 1)
}
"""


@pytest.mark.parametrize("workers", COUNTS)
def test_maps_give_the_same_results_at_any_number_of_workers(workers):
    result = lang.run(MAPS, env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    assert result.stdout == lang.output(MAPS)


# A diamond on a long chain: step i depends on i - 1 (the chain) and, for
# each step that is a multiple of 10, on the two steps before it too (the
# diamonds). Each step's result is a string of its dependencies' results, so
# a step that read a dependency before it finished would panic in `finished`,
# and a step run twice or out of order would change the hash.
GRAPH = """
import System.Parallel as Parallel

fun dependsOn(i : Int) -> Array Int {
    if i == 0 { return [] }
    if i % 10 == 0 && i >= 2 { return [i - 1, i - 2] }
    [i - 1]
}

fun main() {
    let results = Parallel.graph(500, dependsOn, fun(i, finished) {
        var h = i
        for d in dependsOn(i) { h = (h * 31 + finished(d)) % 1000003 }
        h
    })
    print(len(results))
    print(results[499])
    -- Wide: 400 independent steps feeding one.
    let wide = Parallel.graph(401, fun(i) = if i == 400 {
        let all = []
        for var k = 0; k < 400; k = k + 1 { Array.push(all, k) }
        all
    } else { [] }, fun(i, finished) {
        if i < 400 { return i * i }
        var s = 0
        for var k = 0; k < 400; k = k + 1 { s = s + finished(k) }
        s
    })
    print(wide[400])
}
"""


@pytest.mark.parametrize("workers", COUNTS)
def test_a_graph_runs_each_step_after_its_dependencies(workers):
    result = lang.run(GRAPH, env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    assert result.stdout == lang.output(GRAPH)


def test_a_cycle_in_a_graph_is_an_error():
    result = lang.run("""
import System.Parallel as Parallel

fun main() {
    let _ = Parallel.graph(3, fun(i) = [(i + 1) % 3], fun(i, _) = i)
    print("unreachable")
}
""")
    assert result.code != 0
    assert "the steps' dependencies have a cycle" in result.stderr
    assert "unreachable" not in result.stdout


@pytest.mark.parametrize("workers", ["1", "4"])
def test_collecting_while_parallel_maps_and_graphs_run(workers):
    env = {"TURKEY_WORKERS": workers, "TURKEY_GC_STRESS": "97"}
    for program in (MAPS, GRAPH):
        result = lang.run(program, env=env, flags=("--gc-verify",))
        assert result.code == 0, result.stderr
        assert result.stdout == lang.output(program)
