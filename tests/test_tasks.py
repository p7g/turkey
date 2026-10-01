"""Tasks (`System.Task`): scopes, `spawn`, `join` and `yield` on one worker.

Every program that should succeed is run twice, plainly and with a
collection at every allocation and the heap verified around each, and the two
must print the same: a task's values live in frames of a parked stack, which
the collector must find.
"""

from tests import lang

STRESS = {"TURKEY_GC_STRESS": "1"}


def agrees(src: str) -> str:
    plain = lang.run(src)
    assert plain.code == 0, plain.stderr
    stressed = lang.run(src, env=STRESS, flags=("--gc-verify",))
    assert stressed.code == 0, stressed.stderr
    assert stressed.stdout == plain.stdout
    return plain.stdout


def test_tasks_take_turns_at_each_yield():
    out = agrees("""
import System.Task as Task

fun worker(name : String, steps : Int) -> Int {
    for var i = 0; i < steps; i = i + 1 {
        print(name + " " + Int.toString(i))
        Task.yield()
    }
    steps * 10
}

fun main() {
    let (a, b) = Task.runScope(do {
        let a = Task.spawn(fun() = worker("a", 3))?
        let b = Task.spawn(fun() = worker("b", 2))?
        pure((a, b))
    })
    print(Task.join(a) + Task.join(b))
}
""")
    assert out == "a 0\nb 0\na 1\nb 1\na 2\n50\n"


def test_a_scope_ends_only_when_its_tasks_have():
    """Tasks run when the code that started them waits: here, at the end of
    the scope, which then waits for every one of them."""
    out = agrees("""
import System.Task as Task

fun main() {
    let log : Array String = []
    Task.runScope(do {
        for name in ["one", "two", "three"] {
            let _ = Task.spawn(fun() { Array.push(log, name) })?
        }
        Array.push(log, "body done")
        pure(())
    })
    Array.push(log, "scope done")
    print(String.join(log, ", "))
}
""")
    assert out == "body done, one, two, three, scope done\n"


def test_a_task_joins_another_and_waits_for_it():
    out = agrees("""
import System.Task as Task

fun slow(steps : Int) -> String {
    for var i = 0; i < steps; i = i + 1 { Task.yield() }
    "slow after " + Int.toString(steps)
}

fun main() {
    let reported = Task.runScope(do {
        let first = Task.spawn(fun() = slow(5))?
        let second = Task.spawn(fun() = "second saw: " + Task.join(first))?
        pure(second)
    })
    print(Task.join(reported))
}
""")
    assert out == "second saw: slow after 5\n"


def test_a_task_can_open_a_scope_of_its_own():
    """The inner scope's end makes the outer task wait, and the worker runs
    the inner tasks meanwhile."""
    out = agrees("""
import System.Task as Task

fun sumOf(xs : Array Int) -> Int {
    let tasks = Task.runScope(do {
        let made : Array (Task.Task Int) = []
        for x in xs { Array.push(made, Task.spawn(fun() = x * x)?) }
        pure(made)
    })
    var total = 0
    for t in tasks { total = total + Task.join(t) }
    total
}

fun main() {
    let outer = Task.runScope(do {
        let a = Task.spawn(fun() = sumOf([1, 2, 3]))?
        let b = Task.spawn(fun() = sumOf([4, 5]))?
        pure((a, b))
    })
    let (a, b) = outer
    print(Task.join(a) + Task.join(b))
}
""")
    assert out == f"{1 + 4 + 9 + 16 + 25}\n"


def test_what_only_parked_tasks_hold_survives_collections():
    """Each task holds a string built before it yields and reads after; the
    others allocate in between."""
    out = agrees("""
import System.Task as Task

fun hold(n : Int) -> String {
    let mine = "task " + Int.toString(n) + " held this"
    for var i = 0; i < 3; i = i + 1 {
        let _ = Array.map([1, 2, 3], fun(x) = Int.toString(x + n))
        Task.yield()
    }
    mine
}

fun main() {
    let tasks = Task.runScope(do {
        let made : Array (Task.Task String) = []
        for var n = 0; n < 20; n = n + 1 {
            -- A `for var` is one variable for the whole loop, which a closure
            -- shares; the tasks run after the loop, so each needs its own.
            let k = n
            Array.push(made, Task.spawn(fun() = hold(k))?)
        }
        pure(made)
    })
    print(Task.join(tasks[0]))
    print(Task.join(tasks[19]))
}
""")
    assert out == "task 0 held this\ntask 19 held this\n"


def test_waiting_on_a_task_that_waits_on_you_is_a_deadlock():
    result = lang.run("""
import System.Task as Task

var other : Option (Task.Task Int) = None

fun main() {
    let _ = Task.runScope(do {
        let a = Task.spawn(fun() = match other {
            Some(b) -> Task.join(b)
            None -> 0
        })?
        let b = Task.spawn(fun() = Task.join(a))?
        other = Some(b)
        pure(())
    })
}
""")
    assert result.code == 1
    assert result.stderr == "panic: deadlock: every task is waiting\n"


def test_a_panic_in_a_task_ends_the_program():
    result = lang.run("""
import System.Task as Task

fun main() {
    let _ = Task.runScope(do {
        let _ = Task.spawn(fun() {
            print("before")
            let xs = [1]
            print(xs[3])
        })?
        pure(())
    })
    print("not reached")
}
""")
    assert result.code == 1
    assert result.stdout == "before\n"
    assert result.stderr == \
        "panic: array index out of bounds: read at index 3, length 1\n"
