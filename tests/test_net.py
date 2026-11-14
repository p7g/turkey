"""Lookups through the system's resolver, and calls a caller can stop waiting for.

`getaddrinfo` cannot be timed or stopped, so a lookup with a deadline runs on a
helper task (`Turkey.Detach`) and the caller leaves at the deadline while the
call goes on. These programs give `Turkey.Resolve.lookup` a resolver of their
own, whose `getaddrinfo` blocks for as long as the test says, since no real
resolver can be made slow on demand, and whose `freeaddrinfo` counts what it
frees.
"""

import os
import re
import subprocess

import pytest

from tests import bootc, lang, toolchain


@pytest.fixture(autouse=True)
def internal(monkeypatch):
    """The test queries are built from `Turkey.*` modules, which a test
    program may import only by name."""
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       os.environ[bootc.INTERNAL_IMPORTS]
                       + ",Turkey.Resolve,Turkey.Scheduler,Turkey.Wait,Turkey.Workers,Turkey.Sys")


def _build(tmp_path, source, *flags):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    entry = tmp_path / "main.gob"
    entry.write_text(source)
    binary = tmp_path / "net"
    bootc.executable(entry, binary, *flags)
    return binary


def _start(binary, workers="1", **env):
    return subprocess.Popen(toolchain.command(binary), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=dict(os.environ, TURKEY_WORKERS=workers, **env))


def _run(binary, workers="1", stdin="", **env):
    process = _start(binary, workers, **env)
    try:
        out, err = process.communicate(stdin, timeout=60)
    finally:
        process.kill()
    assert process.returncode == 0, err
    return out, err


# -- what the system's headers say ------------------------------------------------
#
# `struct addrinfo`'s layout, the address families and `getaddrinfo`'s codes,
# read back from a C program built against this machine's own headers.

CONSTANTS = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/socket.h>

int main(void) {
    printf("%zu %zu %zu %zu %zu\n", sizeof(struct addrinfo),
           offsetof(struct addrinfo, ai_family), offsetof(struct addrinfo, ai_socktype),
           offsetof(struct addrinfo, ai_addr), offsetof(struct addrinfo, ai_next));
    printf("%zu %zu %zu\n", offsetof(struct sockaddr_in, sin_addr),
           offsetof(struct sockaddr_in6, sin6_addr), offsetof(struct sockaddr_in6, sin6_scope_id));
    printf("%d %d %d %d\n", AF_INET, AF_INET6, SOCK_STREAM, ETIMEDOUT);
    printf("%d %d %d %d %d %d\n", EAI_AGAIN, EAI_FAIL, EAI_MEMORY, EAI_NODATA, EAI_NONAME,
           EAI_SYSTEM);
    return 0;
}
"""

CONSTANTS_PROGRAM = """
import Turkey.Sys as Sys

fun main() {
    print(String.join([show(Sys.addrinfoBytes), show(Sys.addrinfoFamilyAt),
                       show(Sys.addrinfoSocktypeAt), show(Sys.addrinfoAddressAt()),
                       show(Sys.addrinfoNextAt)], " "))
    print("4 8 24")
    print(String.join([show(Sys.afInet), show(Sys.afInet6()), show(Sys.sockStream),
                       show(Sys.etimedout())], " "))
    print(String.join([show(Sys.eaiAgain()), show(Sys.eaiFail()), show(Sys.eaiMemory()),
                       show(Sys.eaiNoData()), show(Sys.eaiNoName()), show(Sys.eaiSystem())], " "))
}
"""


def test_the_resolver_constants_match_the_headers(tmp_path):
    binary = _build(tmp_path, CONSTANTS_PROGRAM)
    source = tmp_path / "constants.c"
    source.write_text(CONSTANTS)
    exe = tmp_path / "constants"
    built = subprocess.run([*toolchain.cc(), "-o", str(exe), str(source)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    expected = subprocess.run(toolchain.command(exe), capture_output=True, text=True,
                              check=True).stdout
    out, _ = _run(binary)
    assert out == expected


# -- offline answers --------------------------------------------------------------

OFFLINE = """
import Net
import Time.Duration as Duration
import Turkey.Resolve as Resolve

-- A resolver whose `getaddrinfo` must not be called.
let never : Resolve.Resolver = Resolve.Resolver {
    getaddrinfo = fun(_, _, _) = error("a literal reached getaddrinfo"),
    freeaddrinfo = fun(_) = {},
}

-- Each address found, as its text.
fun shown(found) = Either.map(found, fun(ips) = Array.map(ips, show))

fun main() {
    print(shown(Resolve.lookup(never, "192.0.2.1", -1)))
    print(shown(Resolve.lookup(never, "2001:db8::1", -1)))
    print(shown(Resolve.lookup(never, "fe80::1%3", -1)))
    -- `localhost` comes from the hosts file, which has the IPv4 loopback
    -- address on every system the suite runs on; whether it lists `::1` too
    -- differs.
    for found in [Net.lookupUnbounded("localhost"), Net.lookup("localhost", Duration.seconds(30))] {
        match found {
            Right(found) -> print(Array.contains(found, Resolve.V4(0x7f000001)))
            Left(e) -> print(e)
        }
    }
    -- The message, which names the failure, rather than the error's fields.
    match Net.lookup("bad\\u{0}name", Duration.seconds(30)) {
        Left(e) -> print("Left(" + message(e) + ")")
        other -> print(shown(other))
    }
}
"""


@pytest.mark.parametrize("workers", ["1", "4"])
def test_literals_and_localhost_resolve_offline(tmp_path, workers):
    binary = _build(tmp_path, OFFLINE)
    out, _ = _run(binary, workers)
    assert out.split("\n")[:-1] == [
        'Right(["192.0.2.1"])',
        'Right(["2001:db8::1"])',
        'Right(["fe80::1%3"])',
        "True",
        "True",
        "Left(lookup bad\0name: invalid argument)",
    ]


# -- a deadline while the call goes on --------------------------------------------

QUERIES = """
import System.Env as Env
import System.IO as IO
import System.Sync as Sync
import System.Task as Task
import Turkey.Libc as Libc
import Turkey.Resolve as Resolve
import Turkey.Scheduler as Scheduler
import Turkey.Wait as Wait
import Turkey.Workers as Workers

let freed = Sync.atomicInt(0)

fun say(text : String) -> Unit {
    let _ = IO.writeText(IO.stderr(), text + "\\n")
}

-- Waits for a byte on the standard input, then calls `getaddrinfo`, and
-- counts each list it frees.
let slow : Resolve.Resolver = Resolve.Resolver {
    getaddrinfo = fun(node, hints, out) {
        let buffer = Libc.malloc(8)
        let _ = Workers.blockingRead(0, buffer, 1)
        Scheduler.afterCall()
        Libc.free(buffer)
        Workers.blockingGetaddrinfo(node, hints, out)
    },
    freeaddrinfo = fun(list) {
        Libc.freeaddrinfo(list)
        -- Said before it is counted: `main` may end as soon as it is.
        say("freed")
        let _ = Sync.addAndGet(freed, 1)
    },
}

-- From a task, or from the program's own stack, which cannot run the helper
-- itself while it waits.
fun from(body : fun() -> Unit) -> Unit = match Env.get("FROM") {
    Some("task") -> Task.runScope(do {
        let _ = Task.spawn(body)?
        pure({})
    })
    _ -> body()
}
"""

LATE = QUERIES + """
fun main() {
    from(fun() {
        let start = Wait.now()
        let found = Resolve.lookup(slow, "localhost", start + 200000000)
        let shown = match found {
            Left(e) -> "Left(" + message(e) + ")"
            Right(_) -> "Right"
        }
        say(shown + " after " + show((Wait.now() - start) / 1000000) + " ms")
    })
    -- Until the helper has freed the list, which it does once the test has
    -- let its call return.
    var tries = 0
    while Sync.load(freed) == 0 && tries < 30000 {
        Wait.sleepUntil(Wait.now() + 1000000)
        tries = tries + 1
    }
    print(Sync.load(freed))
}
"""


@pytest.mark.parametrize("workers", ["1", "4"])
@pytest.mark.parametrize("origin", ["task", "program"])
def test_a_deadline_returns_while_the_call_goes_on(tmp_path, workers, origin):
    binary = _build(tmp_path, LATE, "--gc-verify")
    process = _start(binary, workers, FROM=origin, TURKEY_GC_STRESS="1")
    try:
        line = process.stderr.readline()
        timed = re.fullmatch(r"Left\(lookup localhost: timed out\) after (\d+) ms\n", line)
        assert timed, line
        assert 200 <= int(timed.group(1)) < 2000
        out, err = process.communicate("x", timeout=60)
    finally:
        process.kill()
    assert process.returncode == 0, err
    # The late list is freed once, by the helper.
    assert out == "1\n"
    assert err == "freed\n"


TAKEN = QUERIES + """
fun main() {
    from(fun() {
        match Resolve.lookup(slow, "localhost", Wait.now() + 60000000000) {
            Right(found) -> say("found " + show(Array.contains(found, Resolve.V4(0x7f000001))))
            Left(e) -> say(show(e))
        }
    })
    print(Sync.load(freed))
}
"""


@pytest.mark.parametrize("origin", ["task", "program"])
def test_a_result_before_the_deadline_is_the_callers(tmp_path, origin):
    binary = _build(tmp_path, TAKEN)
    out, err = _run(binary, "1", stdin="x", FROM=origin)
    # Freed once, by the caller, after reading it.
    assert err == "freed\nfound True\n"
    assert out == "1\n"


# -- many at once -----------------------------------------------------------------

BURST = """
import System.Sync as Sync
import System.Task as Task
import Turkey.Libc as Libc
import Turkey.Memory as Memory
import Turkey.Resolve as Resolve
import Turkey.Wait as Wait
import Turkey.Workers as Workers

let inside = Sync.atomicInt(0)
let most = Sync.atomicInt(0)
let word = zeroed()

fun zeroed() {
    let p = Libc.malloc(8)
    Memory.fill(p, 8, 0)
    p
}

-- Stays in a blocking call for 20 ms, as a slow name server would keep
-- `getaddrinfo`, counting the lookups inside at once.
let slow : Resolve.Resolver = Resolve.Resolver {
    getaddrinfo = fun(node, hints, out) {
        let n = Sync.addAndGet(inside, 1)
        var seen = Sync.load(most)
        while n > seen && !Sync.compareAndSet(most, seen, n) { seen = Sync.load(most) }
        let until = Wait.now() + 20000000
        while Wait.now() < until { Workers.blockingSleepOn(word, 0, until - Wait.now()) }
        let _ = Sync.addAndGet(inside, -1)
        Workers.blockingGetaddrinfo(node, hints, out)
    },
    freeaddrinfo = fun(list) = Libc.freeaddrinfo(list),
}

fun main() {
    let n = 2 * Resolve.lookupsAtOnce
    let deadline = Wait.now() + 60000000000
    let tasks = Task.runScope(do {
        let started : Vec (Task.Task Bool) = []
        for var i = 0; i < n; i = i + 1 {
            Vec.push(started, Task.spawn(fun() = match Resolve.lookup(slow, "localhost", deadline) {
                Right(_) -> True
                Left(_) -> False
            })?)
        }
        pure(started)
    })
    var answered = 0
    for t in tasks { if Task.join(t) { answered = answered + 1 } }
    print(answered == n)
    print(Sync.load(most) <= Resolve.lookupsAtOnce)
    print(Workers.count() <= Workers.threadLimit())
}
"""


@pytest.mark.parametrize("workers", ["1", "4"])
def test_a_burst_past_the_bound_queues_and_completes(tmp_path, workers):
    binary = _build(tmp_path, BURST)
    out, _ = _run(binary, workers)
    assert out == "True\nTrue\nTrue\n"


# -- the rest of the program goes on ------------------------------------------------

PROGRESS = "import Process\n" + QUERIES + """
fun found(deadline : Int) -> String = match Resolve.lookup(slow, "localhost", deadline) {
    Right(_) -> "found"
    Left(e) -> show(e)
}

fun main() {
    let (inline, detached, ticks) = Task.runScope(do {
        -- Without a deadline the task makes the call itself; with one, a
        -- helper does.
        let inline = Task.spawn(fun() = found(-1))?
        let detached = Task.spawn(fun() = found(Wait.now() + 60000000000))?
        -- Timers, then a child's pipes and exit, all through the poller,
        -- while both lookups wait in their calls.
        let ticks = Task.spawn(fun() {
            for var i = 0; i < 10; i = i + 1 { Wait.sleepUntil(Wait.now() + 10000000) }
            let echoed = match Process.run(Process.command(["echo", "hi"]), None) {
                Right(out) -> String.fromBytesLossy(out.stdout)
                Left(e) -> show(e)
            }
            say("progress " + String.trim(echoed))
            10
        })?
        pure((inline, detached, ticks))
    })
    print(Task.join(inline))
    print(Task.join(detached))
    print(Task.join(ticks))
}
"""


@pytest.mark.parametrize("workers", ["1", "4"])
def test_timers_and_pipes_go_on_while_lookups_wait(tmp_path, workers):
    binary = _build(tmp_path, PROGRESS)
    process = _start(binary, workers)
    try:
        # Neither lookup has its byte yet.
        assert process.stderr.readline() == "progress hi\n"
        out, err = process.communicate("xy", timeout=60)
    finally:
        process.kill()
    assert process.returncode == 0, err
    assert out == "found\nfound\n10\n"


# -- no limit is said, not defaulted ------------------------------------------------

def test_an_option_is_not_a_timeout():
    """A `None` from configuration must not make a lookup unbounded without
    the code saying so: `lookupUnbounded` is how a program says it."""
    with pytest.raises(lang.CompileError) as error:
        lang.check("""
import Net
import Time.Duration as Duration

fun main() {
    let configured : Option Duration.Duration = None
    print(Net.lookup("localhost", configured))
}
""")
    assert error.value.message == "no instance for 'Timeout (Option Duration)'"
