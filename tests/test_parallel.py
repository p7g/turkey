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
    for var i = 0; i < 1000; i = i + 1 { Vec.push(xs, i) }
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
        for var k = 0; k < 400; k = k + 1 { Vec.push(all, k) }
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


# The functions read their input through the classes, not as an `Array`: a
# range that is never materialized works the same, as the input of the maps
# and as what `dependsOn` answers.
RANGE = """
import System.Parallel as Parallel

type Upto = Upto(Int)
type UptoCursor = UptoCursor { var at : Int }

instance Length Upto { fun len(Upto(n)) = n }

instance Index Upto {
    type Key = Int
    type Value = Int
    fun get(Upto(_), i) = i
}

instance Iterator Upto {
    type Item = Int
    type Cursor = UptoCursor
    fun iter(_) = UptoCursor { at = 0 }
    fun next(Upto(n), cursor) {
        if cursor.at >= n { return None }
        let i = cursor.at
        cursor.at = i + 1
        Some(i)
    }
}

fun main() {
    let squares = Parallel.map(Upto(1000), fun(x) = x * x)
    print(len(squares))
    print(squares[999])
    print(Parallel.mapWith(Upto(4), fun() = 10, fun(base, x) = base + x))
    var total = 0
    Parallel.mapInOrder(Upto(100), fun(x) = x, fun(_, x) { total = total + x })
    print(total)
    Parallel.each(Upto(3), fun(x) { let _ = x })
    -- Each step depends on every step before it.
    print(Parallel.graph(6, Upto, fun(i, finished) {
        var sum = 1
        for d in Upto(i) { sum = sum + finished(d) }
        sum
    }))
}
"""


@pytest.mark.parametrize("workers", ["1", "4"])
def test_the_maps_and_graphs_read_any_indexable_container(workers):
    result = lang.run(RANGE, env={"TURKEY_WORKERS": workers})
    assert result.code == 0, result.stderr
    assert result.stdout == "1000\n998001\n[10, 11, 12, 13]\n4950\n[1, 2, 4, 8, 16, 32]\n"


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
