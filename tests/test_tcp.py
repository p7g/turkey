"""TCP sockets that park tasks, and the policy that decides where they may go.

Every connection here is to a listener in the same program, on loopback, so
nothing reaches the network. A name that must resolve to something chosen is
given to `Turkey.ConnectPolicy.resolve` with a resolver of the test's own, as
`tests/test_net.py` does for lookups.
"""

import os
import re
import subprocess

import pytest

from tests import bootc, toolchain


@pytest.fixture(autouse=True)
def internal(monkeypatch):
    """Some programs reach under `Net`: a listener with a backlog of their
    own, and a resolver that answers what they say."""
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       os.environ[bootc.INTERNAL_IMPORTS]
                       + ",Turkey.ConnectPolicy,Turkey.Resolve,Turkey.Socket,Turkey.Sys,"
                       + "Turkey.Workers,Turkey.IO,Turkey.Scheduler")


def _build(tmp_path, source, *flags):
    if toolchain.missing():
        pytest.skip("C compiler unavailable")
    entry = tmp_path / "main.gob"
    entry.write_text(source)
    binary = tmp_path / "tcp"
    bootc.link(entry, binary, *flags)
    return binary


def _run(binary, workers="1"):
    process = subprocess.Popen(toolchain.command(binary), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               env=dict(os.environ, TURKEY_WORKERS=workers))
    try:
        out, err = process.communicate(timeout=60)
    finally:
        process.kill()
    assert process.returncode == 0, err
    return out


# -- what the system's headers say ------------------------------------------------

CONSTANTS = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdio.h>
#include <sys/socket.h>

int main(void) {
    printf("%d %d %d %d %d\n", EINPROGRESS, ECONNREFUSED, ECONNRESET, ECONNABORTED, ENETUNREACH);
    printf("%d %d %d %d %d\n", EHOSTUNREACH, EADDRINUSE, EADDRNOTAVAIL, EAFNOSUPPORT, ENOTCONN);
    printf("%d %d %d\n", SOL_SOCKET, SO_REUSEADDR, SO_ERROR);
#ifdef __APPLE__
    printf("%d 0\n", SO_NOSIGPIPE);
#else
    printf("%d %d\n", SOCK_NONBLOCK, SOCK_CLOEXEC);
#endif
#ifdef __APPLE__
    printf("0\n");
#else
    printf("%d\n", MSG_NOSIGNAL);
#endif
    printf("%d %d %d\n", IPPROTO_TCP, TCP_NODELAY, SHUT_WR);
    printf("%zu %zu\n", sizeof(struct sockaddr_in), sizeof(struct sockaddr_in6));
    return 0;
}
"""

CONSTANTS_PROGRAM = """
import Target (OS(..))
import Target as Target
import Turkey.Sys as Sys

fun line(xs : Array Int) -> Unit = print(String.join(Array.map(xs, show), " "))

fun main() {
    line([Sys.einprogress(), Sys.econnrefused(), Sys.econnreset(), Sys.econnaborted(),
          Sys.enetunreach()])
    line([Sys.ehostunreach(), Sys.eaddrinuse(), Sys.eaddrnotavail(), Sys.eafnosupport(),
          Sys.enotconn()])
    line([Sys.solSocket(), Sys.soReuseAddress(), Sys.soError()])
    match Target.os {
        Darwin -> line([Sys.soNoSigPipe, 0])
        Linux -> line([Sys.sockNonBlocking, Sys.sockCloseOnExec])
    }
    line([Sys.msgNoSignal()])
    line([Sys.ipprotoTCP, Sys.tcpNoDelay, Sys.shutWrite])
    line([Sys.sockaddrInBytes, Sys.sockaddrIn6Bytes])
}
"""


def test_the_socket_constants_match_the_headers(tmp_path):
    binary = _build(tmp_path, CONSTANTS_PROGRAM)
    source = tmp_path / "constants.c"
    source.write_text(CONSTANTS)
    exe = tmp_path / "constants"
    built = subprocess.run([*toolchain.cc(), "-o", str(exe), str(source)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    expected = subprocess.run(toolchain.command(exe), capture_output=True, text=True,
                              check=True).stdout
    assert _run(binary) == expected


# -- connections on loopback ------------------------------------------------------

COMMON = """
import Net
import Net.TCP as TCP
import System.IO as IO
import System.Task as Task
import Time.Duration as Duration
import Time.Sleep as Sleep
import Time.Stopwatch as Stopwatch

let loopback : Net.IP = Net.V4(0x7f000001)

fun text(b : IO.Buffer) -> String = String.fromBytesLossy(IO.bytes(b))

fun listening(address : Net.IP) -> (TCP.Listener, Int) = match TCP.listen(address, 0) {
    Right(l) -> (l, TCP.listenerAddress(l).1)
    Left(e) -> error(show(e))
}

fun dial(address : Net.IP, port : Int) -> Either IO.IOError TCP.Socket {
    let d = Net.resolve(show(address), port, Net.anywhere, Duration.seconds(10))?
    TCP.connect(d, Duration.seconds(10))
}

-- What is left of an error to compare: its operation and kind.
fun brief(r : Either IO.IOError a) -> String = match r {
    Right(_) -> "Right"
    Left(e) -> e.op + " " + show(e.kind)
}
"""

EXCHANGE = COMMON + """
-- Each side writes, sends the end of input, and reads to the end of the
-- other's.
fun talk(s : TCP.Socket, says : String) -> Either IO.IOError String {
    IO.writeText(s, says)?
    TCP.shutdownWrite(s)?
    let heard = IO.readAll(s)?
    TCP.close(s)?
    Right(text(heard))
}

fun exchange(address : Net.IP) -> String {
    let l = match TCP.listen(address, 0) {
        Right(l) -> l
        -- A machine without IPv6 on loopback.
        Left(e) -> return "no " + show(address)
    }
    let port = TCP.listenerAddress(l).1
    let (server, client) = Task.runScope(do {
        let server = Task.spawn(fun() = match TCP.accept(l) {
            Right(s) -> {
                let peer = TCP.peerAddress(s)
                talk(s, "from the server, " + show(Either.map(peer, fun(p) = p.0)))
            }
            Left(e) -> Left(e)
        })?
        let client = Task.spawn(fun() {
            let s = dial(address, port)?
            let local = TCP.localAddress(s)?
            -- Large enough to need several writes and reads.
            talk(s, "from the client " + String.repeat("x", 300000) + show(local.0))
        })?
        pure((server, client))
    })
    let _ = TCP.closeListener(l)
    let heard = match Task.join(client) {
        Right(t) -> t
        Left(e) -> show(e)
    }
    let said = match Task.join(server) {
        Right(t) -> String.replace(t, String.repeat("x", 300000), "...")
        Left(e) -> show(e)
    }
    said + " / " + heard
}

fun main() {
    print(exchange(loopback))
    let six = exchange(Net.V6(0, 1, 0))
    print(six == "no ::1" || six == "from the client ...::1 / from the server, Right(::1)")
}
"""


@pytest.mark.parametrize("workers", ["1", "4"])
def test_a_loopback_exchange_both_ways_ends_at_the_half_close(tmp_path, workers):
    binary = _build(tmp_path, EXCHANGE)
    assert _run(binary, workers).splitlines() == [
        "from the client ...127.0.0.1 / from the server, Right(127.0.0.1)",
        "True",
    ]


REFUSED = COMMON + """
fun main() {
    -- A port nothing listens on: one a listener had, closed.
    let (l, port) = listening(loopback)
    let _ = TCP.closeListener(l)
    let r = dial(loopback, port)
    print(brief(r))
    if let Left(e) = r { print(e) }
    -- A write to a peer that has gone fails, and does not end the program
    -- with SIGPIPE.
    let (l2, port2) = listening(loopback)
    let s = match dial(loopback, port2) { Right(s) -> s; Left(e) -> error(show(e)) }
    let peer = match TCP.accept(l2) { Right(p) -> p; Left(e) -> error(show(e)) }
    let _ = TCP.close(peer)
    var written : Either IO.IOError Unit = Right(())
    for var i = 0; i < 100; i = i + 1 {
        written = IO.writeText(s, String.repeat("y", 65536))
        if !Either.isRight(written) { break }
        Sleep.sleep(Duration.millis(1))
    }
    print(brief(written) == "write BrokenPipe" || brief(written) == "write ConnectionReset")
    print(brief(TCP.close(s)))
    print(brief(TCP.close(s)))
    print(brief(IO.writeText(s, "z")))
}
"""


def test_a_refused_connection_and_a_write_to_a_closed_peer(tmp_path):
    binary = _build(tmp_path, REFUSED)
    out = _run(binary).splitlines()
    assert out[0] == "connect ConnectionRefused"
    assert re.fullmatch(r"connect 127\.0\.0\.1:\d+: connection refused", out[1])
    assert out[2:] == ["True", "Right", "Right", "write Closed"]


# A listener that never accepts, with the smallest backlog: once the kernel's
# queue for it is full it drops the next connection's SYN, and the connection
# waits as one to an address that never answers would. This needs no
# blackhole address, which a CI network may route or reject.
DEADLINE = COMMON + """
import Turkey.Socket as Socket

fun main() {
    let number = Socket.listen(loopback, 0, 0)
    let port = match Socket.localAddress(number) { Right(a) -> a.1; Left(n) -> error(show(n)) }
    let d = match Net.resolve("127.0.0.1", port, Net.anywhere, Duration.seconds(10)) {
        Right(d) -> d
        Left(e) -> error(show(e))
    }
    let held : Vec TCP.Socket = []
    var outcome = "never timed out"
    for var i = 0; i < 200; i = i + 1 {
        let watch = Stopwatch.start()
        match TCP.connect(d, Duration.millis(300)) {
            Right(s) -> Vec.push(held, s)
            Left(e) -> {
                let took = Duration.toNanos(Stopwatch.elapsed(watch)) / 1000000
                outcome = e.op + " " + show(e.kind) + " " + show(took >= 300 && took < 5000)
                break
            }
        }
    }
    print(outcome)
    print(len(held) > 0)
}
"""


def test_a_deadline_ends_a_connection_that_is_never_answered(tmp_path):
    binary = _build(tmp_path, DEADLINE)
    assert _run(binary).splitlines() == ["connect TimedOut True", "True"]


# `close` from another task ends a parked `read` and a parked `accept` with
# `Closed`. New connections made at once afterwards are likely to be given the
# numbers just closed, and the bytes written into them must not reach the
# parked read.
RACE = COMMON + """
fun main() {
    let (quiet, _) = listening(loopback)
    let (l, port) = listening(loopback)
    let c = match dial(loopback, port) { Right(s) -> s; Left(e) -> error(show(e)) }
    let s = match TCP.accept(l) { Right(s) -> s; Left(e) -> error(show(e)) }
    let (accepted, read) = Task.runScope(do {
        let accepted = Task.spawn(fun() = brief(TCP.accept(quiet)))?
        let read = Task.spawn(fun() = brief(IO.readAll(c)))?
        -- Both park; then both are closed under them.
        Sleep.sleep(Duration.millis(100))
        let _ = TCP.closeListener(quiet)
        let _ = TCP.close(c)
        for var i = 0; i < 4; i = i + 1 {
            let n = match dial(loopback, port) { Right(n) -> n; Left(e) -> error(show(e)) }
            let m = match TCP.accept(l) { Right(m) -> m; Left(e) -> error(show(e)) }
            let _ = IO.writeText(n, "not yours")
            let _ = IO.writeText(m, "not yours")
        }
        pure((accepted, read))
    })
    print(Task.join(accepted))
    print(Task.join(read))
    print(brief(TCP.accept(quiet)))
    print(brief(IO.readAll(c)))
    let _ = TCP.close(s)
}
"""


@pytest.mark.parametrize("workers", ["1", "4"])
def test_close_ends_a_parked_read_and_a_parked_accept(tmp_path, workers):
    binary = _build(tmp_path, RACE)
    assert _run(binary, workers).splitlines() == [
        "accept Closed", "read Closed", "accept Closed", "read Closed",
    ]


# With one worker, a task parked in `accept` holds no worker, so another
# task's connection and exchange go on, as does a read with a deadline.
PROGRESS = COMMON + """
fun main() {
    let (waiting, _) = listening(loopback)
    let (l, port) = listening(loopback)
    let (parked, talked) = Task.runScope(do {
        let parked = Task.spawn(fun() = brief(TCP.accept(waiting)))?
        let talked = Task.spawn(fun() {
            let c = match dial(loopback, port) { Right(c) -> c; Left(e) -> return show(e) }
            let s = match TCP.accept(l) { Right(s) -> s; Left(e) -> return show(e) }
            let _ = IO.writeText(c, "ping")
            let into = IO.buffer(16)
            let _ = IO.readInto(s, into)
            -- Nothing more is coming: a deadline ends the next read.
            TCP.setDeadline(s, Duration.millis(50))
            let late = brief(IO.readInto(s, IO.buffer(16)))
            let _ = TCP.close(c)
            let _ = TCP.close(s)
            let _ = TCP.closeListener(waiting)
            text(into) + " " + late
        })?
        pure((parked, talked))
    })
    print(Task.join(talked))
    print(Task.join(parked))
}
"""


def test_a_parked_accept_holds_no_worker(tmp_path):
    binary = _build(tmp_path, PROGRESS)
    assert _run(binary, "1").splitlines() == ["ping read TimedOut", "accept Closed"]


# -- policies -----------------------------------------------------------------------

LITERALS = """
import Net
import System.IO as IO
import Time.Duration as Duration

fun check(policy : Net.ConnectPolicy, host : String) -> String =
    match Net.resolve(host, 443, policy, Duration.seconds(5)) {
        Right(d) -> "allowed " + show(Net.addresses(d))
        Left(e) -> show(e.kind)
    }

fun main() {
    for host in ["127.0.0.1", "::1", "::ffff:127.0.0.1", "10.0.0.1", "169.254.169.254",
                 "[::1]", "0.0.0.0", "100.64.0.1", "64:ff9b::a9fe:a9fe", "fe80::1%1",
                 "192.0.2.1", "2001:db8::1", "224.0.0.1", "fd00::1", "255.255.255.255"] {
        print(host + " " + check(Net.publicInternet, host))
    }
    for host in ["93.184.215.14", "2606:4700::1111", "[2606:4700::1111]", "::ffff:8.8.8.8"] {
        print(host + " " + check(Net.publicInternet, host))
    }
    print(check(Net.anywhere, "127.0.0.1"))
    let internal = Net.allowlist([Net.AddressRange(Net.V4(0x0a00_0000), 8)])
    print(check(internal, "10.1.2.3"))
    print(check(internal, "::ffff:10.1.2.3"))
    print(check(internal, "11.0.0.1"))
    print(check(Net.allowlist([Net.HostName("[::1]")]), "::1"))
    print(check(Net.allowlist([]), "8.8.8.8"))
    match Net.resolve("127.0.0.1", 0, Net.anywhere, Duration.seconds(5)) {
        Right(_) -> print("port 0 allowed")
        Left(e) -> print(e)
    }
}
"""


def test_public_internet_refuses_what_is_not_on_it(tmp_path):
    binary = _build(tmp_path, LITERALS)
    assert _run(binary).splitlines() == [
        "127.0.0.1 Forbidden",
        "::1 Forbidden",
        "::ffff:127.0.0.1 Forbidden",
        "10.0.0.1 Forbidden",
        "169.254.169.254 Forbidden",
        "[::1] Forbidden",
        "0.0.0.0 Forbidden",
        "100.64.0.1 Forbidden",
        "64:ff9b::a9fe:a9fe Forbidden",
        "fe80::1%1 Forbidden",
        "192.0.2.1 Forbidden",
        "2001:db8::1 Forbidden",
        "224.0.0.1 Forbidden",
        "fd00::1 Forbidden",
        "255.255.255.255 Forbidden",
        "93.184.215.14 allowed [93.184.215.14]",
        "2606:4700::1111 allowed [2606:4700::1111]",
        "[2606:4700::1111] allowed [2606:4700::1111]",
        "::ffff:8.8.8.8 allowed [::ffff:8.8.8.8]",
        "allowed [127.0.0.1]",
        "allowed [10.1.2.3]",
        "allowed [::ffff:10.1.2.3]",
        "Forbidden",
        "allowed [::1]",
        "Forbidden",
        "resolve 127.0.0.1: invalid argument",
    ]


# A resolver that answers every name with 127.0.0.1 and remembers the name it
# was asked for, so that a rule's comparison can be tested on names that no
# real resolver would answer offline.
NAMES = """
import Net
import System.IO as IO
import Turkey.ConnectPolicy as ConnectPolicy
import Turkey.IO as Calls
import Turkey.Libc as Libc
import Turkey.Ptr as Ptr
import Turkey.Resolve as Resolve
import Turkey.Scheduler as Scheduler
import Turkey.Workers as Workers

let loopbackName : Array Byte = Calls.cPath("127.0.0.1")

let asked : Vec String = []

fun cString(p : Ptr.Ptr) -> String = Option.unwrapOr(Ptr.fromCString(p), "?")

let fixed : Resolve.Resolver = Resolve.Resolver {
    getaddrinfo = fun(node, hints, out) {
        Vec.push(asked, cString(node))
        let r = Workers.blockingGetaddrinfo(Calls.addressOf(loopbackName, 0), hints, out)
        Scheduler.afterCall()
        r
    },
    freeaddrinfo = fun(list) = Libc.freeaddrinfo(list),
}

fun check(policy : Net.ConnectPolicy, host : String) -> String =
    match ConnectPolicy.resolve(fixed, host, 80, policy, -1) {
        Right(d) -> "allowed " + d.host + " " + show(d.addresses)
        Left(e) -> show(e)
    }

fun main() {
    let rule = Net.allowlist([Net.HostName("example.com")])
    print(check(rule, "Example.COM."))
    print(check(rule, "EXAMPLE.com"))
    print(check(rule, "example.com.."))
    print(check(rule, "example.org"))
    print(check(Net.allowlist([Net.HostName("API.Example.com.")]), "api.example.com"))
    print(check(Net.publicInternet, "Example.COM."))
    print(String.join(Vec.toArray(asked), ","))
}
"""


def test_a_name_rule_compares_names_normalised(tmp_path):
    binary = _build(tmp_path, NAMES)
    assert _run(binary).splitlines() == [
        "allowed Example.COM. [127.0.0.1]",
        "allowed EXAMPLE.com [127.0.0.1]",
        "resolve example.com..: every address refused by the connect policy",
        "resolve example.org: every address refused by the connect policy",
        "allowed api.example.com [127.0.0.1]",
        "resolve Example.COM.: every address refused by the connect policy",
        # Each lookup asks for the name as written.
        "Example.COM.,EXAMPLE.com,example.com..,example.org,api.example.com,Example.COM.",
    ]
