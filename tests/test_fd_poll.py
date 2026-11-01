"""`Turkey.FD` and `Turkey.Stdout` parking on readiness instead of spinning.

A throwaway library module drives `FD` over non-blocking pipes it makes with
`pipe` declared through `TURKEY_TEST_FOREIGN`.
"""

import hashlib
import resource
import select
import subprocess
import time

import pytest

from tests import bootc, toolchain
from tests.lang import binary, run

LIB = bootc.REPO_ROOT / "lib"

PROBE = """module PROBE (
    pingPong, bulk, closeParked, heartbeat, gcParked, readDeadline,
)

import System.Sync as Sync
import System.Task as Task
import Turkey.FD as FD
import Turkey.IO as IO
import Turkey.Libc as Libc
import Turkey.Sys as Sys
import Turkey.Wait as Wait

foreign "pipe" fun pipe(fds : Prim.Ptr) -> Int

let millisecond = 1000000

fun word(p : Prim.Ptr, at : Int) -> Int = Prim.charToInt(Prim.loadI32(p, at))

fun nonBlocking(fd : Int) -> Unit {
    let flags = IO.fcntl(fd, Sys.getStatusFlags)
    let _ = IO.fcntlWith(fd, Sys.setStatusFlags, flags + Sys.nonBlocking())
}

-- A pipe with both ends non-blocking: (read end, write end).
fun makePipe() -> (FD.FD, FD.FD) {
    let fds = Libc.malloc(8)
    let _ = pipe(fds)
    nonBlocking(word(fds, 0))
    nonBlocking(word(fds, 4))
    (FD.fd(word(fds, 0)), FD.fd(word(fds, 4)))
}

fun one(f : FD.FD) -> Int {
    let b = Array.filled(1, Byte.truncate(0))
    let n = FD.read(f, b, 0, 1)
    if n == 1 { Prim.byteToInt(b[0]) } else { n }
}

fun send(f : FD.FD, v : Int) -> Int {
    let b = Array.filled(1, Byte.truncate(v))
    FD.write(f, b, 0, 1)
}

-- Two tasks over two pipes, one sleeping 20 ms in each round.
fun pingPong(rounds : Int) -> Unit {
    let (abRead, abWrite) = makePipe()
    let (baRead, baWrite) = makePipe()
    let _ = Task.runScope(do {
        let a = Task.spawn(fun() {
            var sum = 0
            for var i = 0; i < rounds; i = i + 1 {
                let _ = send(abWrite, i % 200)
                sum = sum + one(baRead)
            }
            sum
        })?
        let b = Task.spawn(fun() {
            for var i = 0; i < rounds; i = i + 1 {
                let v = one(abRead)
                Wait.sleepUntil(Wait.now() + 20 * millisecond)
                let _ = send(baWrite, v)
            }
            0
        })?
        print(Task.join(a))
        let _ = Task.join(b)
        pure(0)
    })
}

fun fill(size : Int, salt : Int) -> Array Byte {
    let data = Array.filled(size, Byte.truncate(0))
    for var i = 0; i < size; i = i + 1 { data[i] = Byte.truncate((i + salt) % 251) }
    data
}

-- Through each of two pipes, `size` bytes, one task writing and one reading,
-- while another task computes.
fun bulk(size : Int) -> Unit {
    let (r1, w1) = makePipe()
    let (r2, w2) = makePipe()
    let _ = Task.runScope(do {
        let reader = fun(r : FD.FD) = fun() {
            let into = Array.filled(4096, Byte.truncate(0))
            var total = 0
            var sum = 0
            while total < size {
                let n = FD.read(r, into, 0, 4096)
                if n <= 0 { return (-1, -1) }
                for var i = 0; i < n; i = i + 1 { sum = sum + Prim.byteToInt(into[i]) }
                total = total + n
            }
            (total, sum)
        }
        let writer = fun(w : FD.FD, salt : Int) = fun() {
            FD.write(w, fill(size, salt), 0, size)
        }
        let a = Task.spawn(reader(r1))?
        let b = Task.spawn(reader(r2))?
        let c = Task.spawn(writer(w1, 0))?
        let d = Task.spawn(writer(w2, 7))?
        let compute = Task.spawn(fun() {
            var x = 0
            for var i = 0; i < 300000; i = i + 1 {
                x = x + i
                if i % 1000 == 0 { Task.yield() }
            }
            x
        })?
        print(Task.join(a))
        print(Task.join(b))
        print(Task.join(c))
        print(Task.join(d))
        print(Task.join(compute))
        pure(0)
    })
}

-- A reader parks on an empty pipe and another task closes its FD; then the
-- number is reused by a new pipe that a new reader reads from.
fun closeParked() -> Unit {
    let (r, w) = makePipe()
    let first = FD.number(r)
    let _ = Task.runScope(do {
        let reader = Task.spawn(fun() { one(r) == FD.closed })?
        let closer = Task.spawn(fun() {
            Wait.sleepUntil(Wait.now() + 100 * millisecond)
            FD.close(r)
        })?
        print(Task.join(reader))
        print(Task.join(closer))
        pure(0)
    })
    let _ = FD.close(w)
    let (r2, w2) = makePipe()
    print(FD.number(r2) == first || FD.number(w2) == first)
    let _ = Task.runScope(do {
        let reader = Task.spawn(fun() { one(r2) })?
        let writer = Task.spawn(fun() {
            Wait.sleepUntil(Wait.now() + 50 * millisecond)
            send(w2, 77)
        })?
        print(Task.join(reader))
        let _ = Task.join(writer)
        pure(0)
    })
}

-- Beats every 50 ms while main reads a line from the inherited stdin.
fun heartbeat() -> Unit {
    let done = Sync.atomicInt(0)
    let _ = Task.runScope(do {
        let beats = Task.spawn(fun() {
            let err = FD.fd(2)
            let mark = String.toBytes("b\\n")
            while Sync.load(done) == 0 {
                print("beat")
                let _ = FD.write(err, mark, 0, 2)
                Wait.sleepUntil(Wait.now() + 50 * millisecond)
            }
        })?
        let into = Array.filled(64, Byte.truncate(0))
        let n = FD.read(FD.fd(0), into, 0, 64)
        Sync.store(done, 1)
        print(n)
        let _ = Task.join(beats)
        pure(0)
    })
}

-- A parked reader's frame holds the only reference to an object.
fun gcParked() -> Unit {
    let (r, w) = makePipe()
    let _ = Task.runScope(do {
        let reader = Task.spawn(fun() {
            let held = [10, 20, 30, 40]
            let v = one(r)
            print(held)
            print(v)
        })?
        let writer = Task.spawn(fun() {
            Wait.sleepUntil(Wait.now() + 100 * millisecond)
            let _ = send(w, 99)
        })?
        let _ = Task.join(reader)
        let _ = Task.join(writer)
        pure(0)
    })
}

-- A read on a pipe nobody writes, with a deadline 50 ms out, while another
-- task computes: it answers `timedOut` at the deadline, and the computation
-- is not held up. Then the pipe is written and a read with a deadline that
-- has passed still finds the byte, as one with room does.
fun readDeadline() -> Unit {
    let (r, w) = makePipe()
    print(FD.timedOut == Wait.timedOut && FD.timedOut != FD.closed && FD.timedOut < -4096)
    let _ = Task.runScope(do {
        let reader = Task.spawn(fun() {
            let into = Array.filled(1, Byte.truncate(0))
            let began = Wait.now()
            let n = FD.readBy(r, into, 0, 1, began + 50 * millisecond)
            (n == FD.timedOut, (Wait.now() - began) / 100000)
        })?
        let compute = Task.spawn(fun() {
            var x = 0
            for var i = 0; i < 300000; i = i + 1 {
                x = x + i
                if i % 1000 == 0 { Task.yield() }
            }
            x
        })?
        print(Task.join(compute))
        print(Task.join(reader))
        pure(0)
    })
    let into = Array.filled(1, Byte.truncate(0))
    print(FD.readBy(r, into, 0, 1, Wait.now() - millisecond) == FD.timedOut)
    let _ = send(w, 42)
    print(FD.readBy(r, into, 0, 1, Wait.now() - millisecond))
    print(Prim.byteToInt(into[0]))
}
"""


@pytest.fixture
def probe(request, monkeypatch):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    digest = hashlib.sha1(request.node.name.encode()).hexdigest()[:10]
    module = f"Probe_fdpoll_{digest}"
    path = LIB / f"{module}.gob"
    monkeypatch.setenv("TURKEY_TEST_FOREIGN", module)
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       monkeypatch_env(bootc.INTERNAL_IMPORTS) + ",Turkey.FD,Turkey.IO,Turkey.Sys")
    path.write_text(PROBE.replace("PROBE", module), encoding="utf-8")

    def source(body: str) -> str:
        return f"import {module} as P\nfun main() {{ {body} }}\n"

    source.module = module
    try:
        yield source
    finally:
        path.unlink(missing_ok=True)


def monkeypatch_env(name: str) -> str:
    import os
    return os.environ.get(name, "")


def lines(source, body, workers="1", **kw):
    result = run(source(body), env={"TURKEY_WORKERS": workers}, **kw)
    assert result.code == 0, result.stderr
    return result.stdout.split("\n")[:-1]


def test_a_ping_pong_over_pipes_waits_without_spinning(probe):
    rounds = 200
    exe = binary(probe(f"P.pingPong({rounds})"))
    import os
    env = dict(os.environ, TURKEY_WORKERS="1")
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    began = time.monotonic()
    done = subprocess.run(toolchain.command(exe), env=env, capture_output=True, timeout=120)
    wall = time.monotonic() - began
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    assert done.returncode == 0, done.stderr
    assert done.stdout.decode().split() == [str(sum(i % 200 for i in range(rounds)))]
    print(f"cpu {cpu:.3f}s wall {wall:.3f}s")
    assert wall > 0.02 * rounds * 0.9
    assert cpu < 0.5 * wall


@pytest.mark.parametrize("workers", ["1", "4"])
def test_more_than_a_pipe_buffer_goes_through_two_pipes_at_once(probe, workers):
    size = 1 << 20
    sums = [sum((i + salt) % 251 for i in range(size)) for salt in (0, 7)]
    out = lines(probe, f"P.bulk({size})", workers)
    assert out == [f"({size}, {sums[0]})", f"({size}, {sums[1]})",
                   str(size), str(size), str(sum(range(300000)))]


@pytest.mark.parametrize("workers", ["1", "4"])
def test_closing_wakes_a_parked_reader_and_a_reused_number_is_clean(probe, workers):
    assert lines(probe, "P.closeParked()", workers) == ["True", "0", "True", "77"]


def test_a_read_of_an_inherited_pipe_does_not_hold_the_only_worker(probe):
    exe = binary(probe("P.heartbeat()"))
    import os
    proc = subprocess.Popen(toolchain.command(exe), bufsize=0, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=dict(os.environ, TURKEY_WORKERS="1"))
    # The program marks each beat on stderr; send the line after three.
    deadline = time.monotonic() + 60
    for _ in range(3):
        assert select.select([proc.stderr], [], [], max(0, deadline - time.monotonic()))[0]
        proc.stderr.readline()
    out, err = proc.communicate(b"hello\n", timeout=60)
    assert proc.returncode == 0, err
    got = out.decode().split("\n")[:-1]
    assert got[-1] == "6"
    assert got.count("beat") >= 3 and set(got[:-1]) == {"beat"}


def test_a_parked_reader_keeps_what_it_holds_across_collections(probe):
    result = run(probe("P.gcParked()"),
                 env={"TURKEY_WORKERS": "1", "TURKEY_GC_STRESS": "1"},
                 flags=("--gc-verify",))
    assert result.code == 0, result.stderr
    assert result.stdout.split("\n")[:-1] == ["[10, 20, 30, 40]", "99"]


@pytest.mark.parametrize("workers", ["1", "4"])
def test_a_read_with_a_deadline_gives_up_at_it_without_holding_the_worker(probe, workers):
    out = lines(probe, "P.readDeadline()", workers)
    assert out[:2] == ["True", str(sum(range(300000)))]
    timed_out, tenths_of_ms = out[2].strip("()").split(", ")
    assert timed_out == "True"
    # 50 ms, and within a few ms of it.
    assert 500 <= int(tenths_of_ms) < 650
    assert out[3:] == ["True", "1", "42"]
