"""The HTTP client against scripted servers on loopback.

`tests/http_client/Peer.gob` is a server that reads each request with the
HTTP/1 codec and replies with whatever bytes a scenario scripts, then keeps
the connection, closes it, stalls or drips, so that every rule of the
client's can be driven from the other end: reuse only after an exact end,
idle connections closed wherever they are in the pool,
the one retry on a pooled connection the server closed and the rule that
allows it, each redirect rule, the policy, the deadlines, an early 413,
trailers both ways, `Expect` relayed for a body that waits for a 100,
upgrades, and TLS with a test CA.

`tests/http_client/Main.gob` runs one scenario per process, named by its
first argument, with the certificates of `tests/test_tls.py`'s `certs`
fixture. The client links OpenSSL, so these skip where it does not link,
as the TLS tests do.
"""

import os
import subprocess

import pytest

from tests import bootc, toolchain
from tests.test_tls import certs  # noqa: F401 -- a fixture

PROGRAM = bootc.REPO_ROOT / "tests" / "http_client" / "Main.gob"


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    toolchain.needs_openssl()
    out = tmp_path_factory.mktemp("http_client") / "client"
    bootc.link(PROGRAM, out, libraries=tuple(toolchain.openssl_libraries()))
    return out


def _run(binary, scenario, certs_dir, workers):
    process = subprocess.Popen(toolchain.command(binary, scenario, str(certs_dir)),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               env=dict(os.environ, TURKEY_WORKERS=workers))
    try:
        out, err = process.communicate(timeout=120)
    finally:
        process.kill()
    assert process.returncode == 0, err
    return out.splitlines()


SCENARIOS = {
    "basics": [
        "200 GET /hello [] on 1",
        "200 POST /form [a=1&b=2] on 1",
        "200 PUT /buffer [from a buffer] on 1",
        "200 PUT /file [from a file] on 1",
        "200 POST /empty [] on 1",
        "200 PATCH /patch [patch] on 1",
        "200 QUERY /query [q=1] on 1",
        # The relayed request goes on a second connection while the first
        # is still reading the response it relays.
        "200 POST /relayed [GET /source [] on 1] on 2",
        "200 head, 0 body bytes",
        "error unexpected status 200 OK",
        "connections 2",
    ],
    "pool": [
        "200 on 1",
        "200 on 1",
        # An unread body short enough to drain keeps the connection.
        "unread",
        "200 on 1",
        "200 abc",
        "200 on 1",
        # Bytes after the body's end: the connection is closed.
        "200 five",
        "200 on 2",
        # `Connection: close`.
        "200 closing",
        "200 on 3",
        # HTTP/1.0 without keep-alive.
        "200 ok",
        "200 on 4",
        # An unread body over the drain cap: closed, not drained.
        "unread big",
        "200 on 5",
        "connections 5",
    ],
    "expiry": [
        "200 a on 2",
        # The inner request's connection expired below the outer one's.
        "200 a on 1",
        "a closed 1",
        # A request to `b` closes `a`'s expired connection.
        "200 b on 1",
        "a closed 2",
        # With one idle connection in all, `b`'s evicts `a`'s.
        "200 a on 3",
        "200 b on 2",
        "a closed 3",
        "200 b on 2",
    ],
    "retry": [
        "200 GET on 1",
        # Idempotent, or with a key, and a replayable body: sent again on a
        # new connection.
        "GET: 200 GET on 2",
        "200 GET on 3",
        "PUT: 200 PUT on 4",
        "200 GET on 5",
        "POST with a key: 200 POST on 6",
        "200 GET on 7",
        # A plain POST is not: the server may have acted on it.
        "POST: error MaybeSent(Truncated)",
        "connections 7",
    ],
    # Columns: plain; with an `Idempotency-Key`; with a body that cannot be
    # sent again, which a GET has no room for, so its third is plain again.
    "rule": [
        "NotSent GET: retry retry retry",
        "NotSent PUT: retry retry no",
        "NotSent POST: retry retry no",
        "MaybeSent GET: retry retry retry",
        "MaybeSent PUT: retry retry no",
        "MaybeSent POST: no retry no",
        "BadResponse GET: no no no",
        "BadResponse PUT: no no no",
        "BadResponse POST: no no no",
    ],
    "redirects": [
        # Off by default: the caller gets the redirect.
        "off: 302 ",
        "overridden: 302 ",
        # Three hops, the limit.
        "200 GET /end []",
        "error NotSent(BadRedirect(TooManyRedirects))",
        # 302 after a POST becomes a GET without the body; 307 and 308 keep
        # the method and the body.
        "200 GET /end []",
        "200 POST /end [form]",
        "200 PUT /end [put]",
        # A HEAD stays a HEAD.
        "200 ",
        # Another port of the same host: credentials dropped. The same
        # origin: kept.
        "200 other got GET /landed, authorization False, cookie False",
        "200 landed, authorization True",
        "error NotSent(BadRedirect(BadTarget(unsupported URL scheme 'file')))",
        # No Location: not followed.
        "302 ",
        # Each hop goes through the policy again.
        "200 landed, authorization False",
        "error NotSent(Transport(Forbidden))",
    ],
    "policy": [
        "error NotSent(Transport(Forbidden))",
        "error NotSent(Transport(Forbidden))",
        "error NotSent(BadURL(unsupported URL scheme 'ftp'))",
        "error NotSent(BadURL(URL has user information, which is not allowed here))",
        "connections 0",
    ],
    "deadlines": [
        "send within 200 ms: error MaybeSent(Transport(TimedOut)), in time True",
        # Inside the callback, the body's own error.
        "drip within 300 ms: error Transport(TimedOut), in time True",
        "unbounded drip: 0123456789",
        "unbounded stall: error Transport(TimedOut), in time True",
        "quiet caller: 0123456789",
    ],
    "early": ["413 too large", "413 too large", "before the body ended True"],
    "trailers": [
        "trailers before the end False; hello; then x-checksum: abc",
        "200 POST [one,two] chunked True, trailers [x-checksum: abc]",
        "200 PUT [abc] chunked False, trailers []",
        "error MaybeSent(trailers were given for a body that is not chunked)",
        "error MaybeSent(Truncated)",
        "error MaybeSent(more body was written than the length declared)",
        "error MaybeSent(Transport(PermissionDenied))",
        "200 POST [hello] chunked True, trailers [x-checksum: abc]",
    ],
    "upgrades": [
        "101 hello world",
        "200 offered [echo, chat]; duplex Transport(Closed)",
        "error BadResponse(BadMessage(Malformed))",
        "error Transport(TimedOut)",
        "after the stall limit: late",
        "200 offered []",
        # The 200 to an offer kept its connection; no switched one was pooled.
        "connections 5",
    ],
    "relayed": [
        "200 PUT expected True [small]; downstream read True",
        "200 PUT expected True [small]; downstream read True",
        "200 PUT expected False [small]; downstream read True",
        "413 too large; downstream read False",
        "discarded 0",
    ],
    "expect": [
        "200 PUT expected False, 5 bytes",
        "200 PUT expected True, 2097152 bytes",
        "final 200",
        "413 too large",
        "discarded 0",
        "200 PUT expected True, 2097152 bytes, waited True",
    ],
    "heads": [
        "103 (1 field), 103 (1 field), 200 after hints; then Transport(Closed)",
        "200 after hints",
        "fresh: dns True, connect True, tls False",
        "pooled: dns False, connect False, tls False",
        "302 from /a",
        "307 from /b",
        "200 from /end",
        "stop: NotRedirect",
        "body GET /end []",
        "hops 2",
        "200 from /end",
    ],
    "escape": ["escaped: error Transport(Closed)"],
    "tls": [
        "200 GET /one [] on 1",
        "200 POST /two [over tls] on 1",
        "error NotSent(BadRedirect(Downgrade))",
        "connections 1",
        "error NotSent(Transport(Untrusted))",
    ],
}


@pytest.mark.parametrize("workers", ["1", "4"])
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_client_scenario(binary, certs, scenario, workers):  # noqa: F811
    assert _run(binary, scenario, certs, workers) == SCENARIOS[scenario]


def test_the_example_builds():
    bootc.boot("check", "examples/http_client/main.gob")
