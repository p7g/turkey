"""TLS over loopback: a client and a server of `Net.TLS` in one program.

Every certificate is made when the session starts, by the `openssl` program of
the OpenSSL the tests link against: a test CA and leaves it signs, one signed
by a CA no client trusts, and one that expired in 2020. Made rather than
committed, so that none can expire in the repository and none is mistaken for
something to trust. The client trusts the test CA by naming it as its roots,
never by turning verification off, which `Net.TLS` cannot do.

Each program is an executable `boot` writes, which loads OpenSSL's `libssl`
and `libcrypto` by the names its declarations give them; a machine without
OpenSSL 3 skips these, unless `$TURKEY_REQUIRE_OPENSSL` says it must.
"""

import os
import secrets
import subprocess

import pytest

from tests import bootc, toolchain


@pytest.fixture(autouse=True)
def openssl(monkeypatch):
    """OpenSSL, and an internal import: the programs make a `Destination`
    for a name of their own, which no resolver knows, on loopback."""
    toolchain.needs_openssl()
    monkeypatch.setenv(bootc.INTERNAL_IMPORTS,
                       os.environ[bootc.INTERNAL_IMPORTS]
                       + ",Turkey.ConnectPolicy,Turkey.OpenSSL")


def _openssl(*args, cwd):
    result = subprocess.run([toolchain.openssl_command(), *args], cwd=cwd,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


CA_CONFIG = """
[ca]
default_ca = test
[test]
database = index.txt
new_certs_dir = .
serial = serial
default_md = sha256
policy = anything
unique_subject = no
[anything]
commonName = supplied
"""


@pytest.fixture(scope="session")
def certs(tmp_path_factory):
    """The certificates' directory: `NAME.pem` and `NAME.key` for each."""
    if not toolchain.has_openssl():
        pytest.skip("OpenSSL 3 is not installed")
    d = tmp_path_factory.mktemp("certs")

    def key(name):
        _openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256",
                 "-out", f"{name}.key", cwd=d)

    def authority(name, subject):
        key(name)
        _openssl("req", "-x509", "-new", "-key", f"{name}.key", "-out", f"{name}.pem",
                 "-days", "2", "-subj", f"/CN={subject}",
                 "-addext", "basicConstraints=critical,CA:TRUE",
                 "-addext", "keyUsage=critical,keyCertSign,cRLSign", cwd=d)

    def leaf(name, names, signer="ca"):
        key(name)
        _openssl("req", "-new", "-key", f"{name}.key", "-out", f"{name}.csr",
                 "-subj", f"/CN={name}", cwd=d)
        (d / f"{name}.ext").write_text(
            f"subjectAltName={names}\nbasicConstraints=CA:FALSE\n"
            "extendedKeyUsage=serverAuth\n")
        _openssl("x509", "-req", "-in", f"{name}.csr", "-CA", f"{signer}.pem",
                 "-CAkey", f"{signer}.key", "-set_serial", "0x" + secrets.token_hex(8),
                 "-days", "2", "-extfile", f"{name}.ext", "-out", f"{name}.pem", cwd=d)

    authority("ca", "Turkey Test CA")
    authority("other", "Untrusted Test CA")
    leaf("localhost", "DNS:localhost,IP:127.0.0.1")
    leaf("alpha", "DNS:alpha.test")
    leaf("beta", "DNS:beta.test,DNS:*.beta.test")
    leaf("stranger", "DNS:localhost", signer="other")
    # `x509` cannot backdate a certificate before OpenSSL 3.4; `ca` can.
    (d / "ca.cnf").write_text(CA_CONFIG)
    (d / "index.txt").write_text("")
    (d / "serial").write_text("01\n")
    key("expired")
    _openssl("req", "-new", "-key", "expired.key", "-out", "expired.csr",
             "-subj", "/CN=localhost", cwd=d)
    (d / "expired.ext").write_text("subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\n")
    _openssl("ca", "-batch", "-config", "ca.cnf", "-cert", "ca.pem", "-keyfile", "ca.key",
             "-in", "expired.csr", "-out", "expired.pem", "-notext",
             "-startdate", "20200101000000Z", "-enddate", "20200102000000Z",
             "-extfile", "expired.ext", cwd=d)
    return d


def _build(tmp_path, source):
    entry = tmp_path / "main.gob"
    entry.write_text(source)
    binary = tmp_path / "tls"
    bootc.executable(entry, binary)
    return binary


def _run(binary, *args, workers="1"):
    process = subprocess.Popen(toolchain.command(binary, *map(str, args)),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               env=dict(os.environ, TURKEY_WORKERS=workers))
    try:
        out, err = process.communicate(timeout=120)
    finally:
        process.kill()
    assert process.returncode == 0, err
    return out.splitlines()


# -- the constants, against OpenSSL's headers ------------------------------------

CONSTANTS = r"""
#include <openssl/err.h>
#include <openssl/ssl.h>
#include <openssl/tls1.h>
#include <openssl/x509_vfy.h>
#include <stdio.h>

int main(void) {
    printf("%d %d %d %d %d %d\n", SSL_ERROR_SSL, SSL_ERROR_WANT_READ, SSL_ERROR_WANT_WRITE,
           SSL_ERROR_SYSCALL, SSL_ERROR_ZERO_RETURN, SSL_ERROR_WANT_CLIENT_HELLO_CB);
    printf("%d %d %llu %d %d\n", SSL_CTRL_SET_MIN_PROTO_VERSION, TLS1_2_VERSION,
           (unsigned long long)SSL_OP_NO_RENEGOTIATION, SSL_VERIFY_PEER, SSL_FILETYPE_PEM);
    printf("%d %d %d\n", SSL_CTRL_SET_TLSEXT_HOSTNAME, TLSEXT_NAMETYPE_host_name,
           TLSEXT_TYPE_server_name);
    printf("%d %d %d %d %d\n", SSL_CLIENT_HELLO_RETRY, SSL_CLIENT_HELLO_SUCCESS,
           SSL_TLSEXT_ERR_OK, SSL_TLSEXT_ERR_ALERT_FATAL, OPENSSL_NPN_NEGOTIATED);
    printf("%d %d %d %d %d\n", X509_V_OK, X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT,
           X509_V_ERR_CERT_NOT_YET_VALID, X509_V_ERR_CERT_HAS_EXPIRED,
           X509_V_ERR_DEPTH_ZERO_SELF_SIGNED_CERT);
    printf("%d %d %d %d %d\n", X509_V_ERR_SELF_SIGNED_CERT_IN_CHAIN,
           X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY,
           X509_V_ERR_UNABLE_TO_VERIFY_LEAF_SIGNATURE, X509_V_ERR_HOSTNAME_MISMATCH,
           X509_V_ERR_IP_ADDRESS_MISMATCH);
    /* The no-ack answer and the block's layout, which the giblet writes out. */
    printf("%d\n", SSL_TLSEXT_ERR_NOACK);
    printf("%lu\n", (unsigned long)ERR_SYSTEM_FLAG);
    return 0;
}
"""

CONSTANTS_PROGRAM = """
import Turkey.OpenSSL as S

fun line(xs : Array Int) -> Unit = print(String.join(Array.map(xs, show), " "))

fun main() {
    line([S.errorSSL, S.errorWantRead, S.errorWantWrite, S.errorSyscall, S.errorZeroReturn,
          S.errorWantClientHelloCb])
    line([S.ctrlSetMinProtoVersion, S.tls12Version, S.opNoRenegotiation, S.verifyPeer,
          S.filetypePEM])
    line([S.ctrlSetTLSExtHostName, S.nameTypeHostName, S.extServerName])
    line([S.clientHelloRetry, S.clientHelloSuccess, S.tlsextErrOK, S.tlsextErrAlertFatal,
          S.npnNegotiated])
    line([S.verifyOK, S.verifyUnableToGetIssuerCert, S.verifyCertNotYetValid,
          S.verifyCertHasExpired, S.verifyDepthZeroSelfSigned])
    line([S.verifySelfSignedInChain, S.verifyUnableToGetIssuerCertLocally,
          S.verifyUnableToVerifyLeafSignature, S.verifyHostnameMismatch,
          S.verifyIPAddressMismatch])
    -- SSL_TLSEXT_ERR_NOACK, which `Turkey.TLSCallbacks` writes out as a literal.
    line([3])
    line([S.errSystemFlag])
}
"""


def test_the_constants_match_the_headers(tmp_path):
    binary = _build(tmp_path, CONSTANTS_PROGRAM)
    source = tmp_path / "constants.c"
    source.write_text(CONSTANTS)
    exe = tmp_path / "constants"
    built = subprocess.run([*toolchain.cc(), *toolchain.openssl_includes(), "-o", str(exe),
                            str(source)], capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    expected = subprocess.run(toolchain.command(exe), capture_output=True, text=True,
                              check=True).stdout
    assert "\n".join(_run(binary)) + "\n" == expected


# -- a client and a server ----------------------------------------------------------

COMMON = """
import Net
import Net.TCP as TCP
import Net.TLS as TLS
import Net.TLS (TLSError(..))
import System.Env as Env
import System.IO as IO
import System.Task as Task
import Time.Duration as Duration
import Turkey.ConnectPolicy (Destination(..))

let loopback : Net.IP = Net.V4(0x7f000001)

-- The certificates' directory, the program's first argument.
fun dir() -> String = Env.args()[0]

fun cert(name : String) -> TLS.Certificate =
    TLS.Certificate { chain = dir() + "/" + name + ".pem", key = dir() + "/" + name + ".key" }

fun text(b : IO.Buffer) -> String = String.fromBytesLossy(IO.bytes(b))

fun listening() -> (TCP.Listener, Int) = match TCP.listen(loopback, 0) {
    Right(l) -> (l, TCP.listenerAddress(l).1)
    Left(e) -> error(show(e))
}

fun serverOf(certificates : Array TLS.Certificate, protocols : Array String) -> TLS.ServerConfig =
    match TLS.serverConfig(certificates, protocols) {
        Right(c) -> c
        Left(e) -> error(show(e))
    }

fun clientOf(roots : String, protocols : Array String) -> TLS.ClientConfig =
    match TLS.clientConfig(TLS.RootsFile(dir() + "/" + roots + ".pem"), protocols) {
        Right(c) -> c
        Left(e) -> error(show(e))
    }

-- A TLS client of `host` on loopback, its TCP connection closed if the
-- handshake fails.
fun dial(host : String, port : Int, config : TLS.ClientConfig) -> Either TLSError TLS.TLS {
    let d = Destination { host, port, addresses = [loopback] }
    let s = Either.mapLeft(TCP.connect(d, Duration.seconds(10)), Stream)?
    match TLS.client(s, d, config, Duration.seconds(10)) {
        Right(t) -> Right(t)
        Left(e) -> {
            let _ = TCP.close(s)
            Left(e)
        }
    }
}

-- One request and answer: the client says hello and sends its close_notify,
-- and the server answers with what the handshake chose and sends its own.
fun serveOne(a : TLS.Acceptor) -> Either TLSError String {
    let t = Either.mapLeft(TLS.accept(a), Stream)?
    TLS.setDeadline(t, Duration.seconds(10))
    let heard = IO.readAll(t)
    let answer = match heard {
        Right(b) -> "hello " + text(b) + ", sni " + show(TLS.serverName(t))
                    + ", alpn " + show(TLS.protocol(t))
        Left(e) -> {
            let _ = TLS.close(t)
            return Left(e)
        }
    }
    IO.writeText(t, answer)?
    TLS.shutdownWrite(t)?
    TLS.close(t)?
    Right("served")
}

fun ask(host : String, port : Int, config : TLS.ClientConfig) -> Either TLSError String {
    let t = dial(host, port, config)?
    IO.writeText(t, "from " + host)?
    TLS.shutdownWrite(t)?
    let heard = IO.readAll(t)?
    let chosen = TLS.protocol(t)
    TLS.close(t)?
    Right(text(heard) + " / " + show(chosen))
}

-- A side's outcome, with the text it ended with unquoted.
fun outcome(r : Either TLSError String) -> String = match r {
    Right(t) -> "Right(" + t + ")"
    Left(_) -> show(r)
}

-- The client's answer, and the server's.
fun exchange(server : TLS.ServerConfig, client : TLS.ClientConfig, host : String) -> String {
    let (l, port) = listening()
    let a = TLS.acceptor(l, server)
    let (s, c) = Task.runScope(do {
        let s = Task.spawn(fun() = serveOne(a))?
        let c = Task.spawn(fun() = ask(host, port, client))?
        pure((s, c))
    })
    let _ = TCP.closeListener(l)
    outcome(Task.join(c)) + " | " + outcome(Task.join(s))
}
"""

LOOPBACK = COMMON + """
fun main() {
    let server = serverOf([cert("localhost")], ["h2", "http/1.1"])
    let client = clientOf("ca", ["http/1.1"])
    print(exchange(server, client, "localhost"))
    print(exchange(server, client, "localhost."))
    print(exchange(server, client, "127.0.0.1"))
    -- Without ALPN on the client, none is chosen.
    print(exchange(server, clientOf("ca", []), "localhost"))
    TLS.releaseServer(server)
    TLS.releaseClient(client)
}
"""


LOOPBACK_OUT = [
    'Right(hello from localhost, sni Some("localhost"), alpn Some("http/1.1")'
    ' / Some("http/1.1")) | Right(served)',
    # SNI and the check go without the trailing dot.
    'Right(hello from localhost., sni Some("localhost"), alpn Some("http/1.1")'
    ' / Some("http/1.1")) | Right(served)',
    # An address is checked against the certificate's addresses, and is
    # not sent as SNI.
    'Right(hello from 127.0.0.1, sni None, alpn Some("http/1.1")'
    ' / Some("http/1.1")) | Right(served)',
    'Right(hello from localhost, sni Some("localhost"), alpn None / None) | Right(served)',
]


@pytest.mark.parametrize("workers", ["1", "4"])
def test_a_client_and_a_server_over_loopback(tmp_path, certs, workers):
    assert _run(_build(tmp_path, LOOPBACK), certs, workers=workers) == LOOPBACK_OUT


SNI = COMMON + """
fun main() {
    let server = serverOf([cert("localhost"), cert("alpha"), cert("beta")], ["http/1.1"])
    let client = clientOf("ca", ["http/1.1"])
    for host in ["alpha.test", "beta.test", "www.beta.test", "localhost", "elsewhere.test"] {
        print(host + ": " + exchange(server, client, host))
    }
}
"""


def test_sni_picks_the_certificate_that_names_the_host(tmp_path, certs):
    out = _run(_build(tmp_path, SNI), certs)
    for host, line in zip(["alpha.test", "beta.test", "www.beta.test", "localhost"], out):
        assert line == (f'{host}: Right(hello from {host}, sni Some("{host}"), alpn '
                        'Some("http/1.1") / Some("http/1.1")) | Right(served)')
    # A name no certificate has gets the first, which does not name it.
    assert out[4].startswith("elsewhere.test: Left(WrongHost) | Left(Refused(")


REFUSED = COMMON + """
fun main() {
    let client = clientOf("ca", ["http/1.1"])
    let cases = [("wrong host", "localhost", "elsewhere.test"),
                 ("expired", "expired", "localhost"),
                 ("unknown CA", "stranger", "localhost")]
    for (what, certificate, host) in cases {
        let server = serverOf([cert(certificate)], ["http/1.1"])
        print(what + ": " + exchange(server, client, host))
        TLS.releaseServer(server)
    }
    -- A protocol in common or none: RFC 7301's alert, on both sides.
    let server = serverOf([cert("localhost")], ["http/1.1"])
    print("no protocol: " + exchange(server, clientOf("ca", ["h2"]), "localhost"))
}
"""


def test_a_certificate_the_client_cannot_trust_is_refused(tmp_path, certs):
    out = _run(_build(tmp_path, REFUSED), certs)
    assert out[0].startswith("wrong host: Left(WrongHost) | Left(Refused(")
    assert out[1].startswith("expired: Left(Expired) | Left(Refused(")
    assert out[2].startswith("unknown CA: Left(Untrusted) | Left(Refused(")
    assert out[3].startswith("no protocol: Left(Refused(")
    assert "no application protocol" in out[3]


CLOSED = COMMON + """
import Time.Sleep as Sleep

fun brief(r : Either TLSError a) -> String = match r {
    Right(_) -> "Right"
    Left(Stream(e)) -> "Stream " + e.op + " " + debug(e.kind)
    Left(e) -> show(e)
}

-- A client of a server that accepts and then says nothing, so the handshake
-- waits for its answer.
fun silent(within : Duration.Duration, closeAfter : Option Duration.Duration) -> String {
    let (l, port) = listening()
    let d = Destination { host = "localhost", port, addresses = [loopback] }
    let client = clientOf("ca", ["http/1.1"])
    let s = match TCP.connect(d, Duration.seconds(10)) { Right(s) -> s; Left(e) -> error(show(e)) }
    let peer = match TCP.accept(l) { Right(p) -> p; Left(e) -> error(show(e)) }
    let r = Task.runScope(do {
        let shaking = Task.spawn(fun() = TLS.client(s, d, client, within))?
        if let Some(after) = closeAfter {
            let _ = Task.spawn(fun() {
                Sleep.sleep(after)
                TCP.close(s)
            })?
        }
        pure(shaking)
    })
    let _ = TCP.close(s)
    let _ = TCP.close(peer)
    let _ = TCP.closeListener(l)
    brief(Task.join(r))
}

-- A server whose client goes away mid-handshake: its read of the handshake
-- ends early.
fun abandoned() -> String {
    let (l, port) = listening()
    let a = TLS.acceptor(l, serverOf([cert("localhost")], []))
    let d = Destination { host = "localhost", port, addresses = [loopback] }
    let s = match TCP.connect(d, Duration.seconds(10)) { Right(s) -> s; Left(e) -> error(show(e)) }
    let t = match TLS.accept(a) { Right(t) -> t; Left(e) -> error(show(e)) }
    let _ = IO.writeText(s, "not a hello")
    let _ = TCP.close(s)
    let r = TLS.handshake(t, Duration.seconds(10))
    let _ = TLS.close(t)
    let _ = TCP.closeListener(l)
    brief(r)
}

fun main() {
    -- Closed from another task while the handshake waits.
    print(silent(Duration.seconds(30), Some(Duration.millis(100))))
    -- Out of time.
    print(silent(Duration.millis(100), None))
    print(abandoned())
    -- Once closed, every operation answers `Closed`.
    let (l, port) = listening()
    let a = TLS.acceptor(l, serverOf([cert("localhost")], []))
    let d = Destination { host = "localhost", port, addresses = [loopback] }
    let s = match TCP.connect(d, Duration.seconds(10)) { Right(s) -> s; Left(e) -> error(show(e)) }
    let t = match TLS.accept(a) { Right(t) -> t; Left(e) -> error(show(e)) }
    print(brief(TLS.close(t)))
    print(brief(TLS.close(t)))
    print(brief(IO.writeText(t, "late")))
    print(brief(IO.readAll(t)))
    print(TLS.protocol(t))
    let _ = TCP.close(s)
    let _ = TCP.closeListener(l)
}
"""


@pytest.mark.parametrize("workers", ["1", "4"])
def test_close_and_time_running_out_mid_handshake(tmp_path, certs, workers):
    out = _run(_build(tmp_path, CLOSED), certs, workers=workers)
    assert out[0] == "Stream read Closed"
    assert out[1] == "Stream read TimedOut"
    assert out[2].startswith("Refused(")
    assert out[3:] == ["Right", "Right", "Stream write Closed", "Stream read Closed", "None"]


# Both directions at once on one connection: the client writes from one task
# while another reads, and the server writes back what it reads as it reads
# it, so neither side's writes can finish unless the reads run alongside.
DUPLEX = COMMON + """
let total : Int = 1048576

-- A byte at `i` that is not periodic in any buffer size.
fun byteAt(i : Int) -> Byte = Byte.truncate((i * 7 + i / 251) % 256)

fun echo(a : TLS.Acceptor) -> Either TLSError Int {
    let t = Either.mapLeft(TLS.accept(a), Stream)?
    TLS.setDeadline(t, Duration.seconds(60))
    let b = IO.buffer(5000)
    var moved = 0
    loop {
        IO.clear(b)
        let n = IO.readInto(t, b)?
        if n == 0 { break }
        IO.writeFrom(t, b)?
        moved = moved + n
    }
    TLS.shutdownWrite(t)?
    TLS.close(t)?
    Right(moved)
}

fun send(t : TLS.TLS) -> Either TLSError Unit {
    let b = IO.buffer(65536)
    var at = 0
    while at < total {
        IO.clear(b)
        while IO.room(b) > 0 && at < total {
            IO.push(b, byteAt(at))
            at = at + 1
        }
        IO.writeFrom(t, b)?
    }
    TLS.shutdownWrite(t)
}

fun receive(t : TLS.TLS) -> Either TLSError String {
    let b = IO.buffer(3000)
    var at = 0
    loop {
        IO.clear(b)
        let n = IO.readInto(t, b)?
        if n == 0 { break }
        for k in IO.bytes(b) {
            if k != byteAt(at) { return Right("wrong byte at " + show(at)) }
            at = at + 1
        }
    }
    Right("received " + show(at))
}

fun main() {
    let (l, port) = listening()
    let a = TLS.acceptor(l, serverOf([cert("localhost")], ["http/1.1"]))
    let client = clientOf("ca", ["http/1.1"])
    let (server, sent, heard) = Task.runScope(do {
        let server = Task.spawn(fun() = echo(a))?
        let t = match dial("localhost", port, client) { Right(t) -> t; Left(e) -> error(show(e)) }
        TLS.setDeadline(t, Duration.seconds(60))
        let sent = Task.spawn(fun() = send(t))?
        let heard = Task.spawn(fun() = receive(t))?
        pure((server, sent, heard))
    })
    print(Task.join(heard))
    print(Either.isRight(Task.join(sent)))
    print(Task.join(server))
    let _ = TCP.closeListener(l)
}
"""


@pytest.mark.parametrize("workers", ["1", "4"])
def test_a_mebibyte_each_way_at_once(tmp_path, certs, workers):
    out = _run(_build(tmp_path, DUPLEX), certs, workers=workers)
    assert out == ['Right("received 1048576")', "True", "Right(1048576)"]


CONFIGURATION = COMMON + """
fun said(r : Either TLSError a) -> String = match r {
    Right(_) -> "Right"
    Left(e) -> "Left(" + show(e) + ")"
}

fun main() {
    print(said(TLS.clientConfig(TLS.RootsFile(dir() + "/missing.pem"), [])))
    print(said(TLS.serverConfig([TLS.Certificate { chain = dir() + "/alpha.pem",
                                                   key = dir() + "/beta.key" }], [])))
    print(said(TLS.serverConfig([], [])))
    print(said(TLS.clientConfig(TLS.SystemRoots, [""])))
    match TLS.clientConfig(TLS.SystemRoots, ["http/1.1"]) {
        Right(c) -> {
            print("system roots")
            TLS.releaseClient(c)
            TLS.releaseClient(c)
            let (l, port) = listening()
            print(said(dial("localhost", port, c)))
            let _ = TCP.closeListener(l)
        }
        Left(e) -> print(e)
    }
}
"""


def test_a_configuration_that_cannot_be_made_says_why(tmp_path, certs):
    out = _run(_build(tmp_path, CONFIGURATION), certs)
    assert out[0] == f"Left(Configuration({certs}/missing.pem: no such file or directory))"
    # OpenSSL checks the key against the certificate as it loads it.
    assert out[1] == f"Left(Configuration({certs}/beta.key: key values mismatch))"
    assert out[2] == "Left(Configuration(a server needs at least one certificate))"
    assert out[3] == ("Left(Configuration(the ALPN protocol '' is 0 bytes long, "
                      "and must be 1 to 255))")
    assert out[4:] == [
        "system roots",
        "Left(Configuration(cannot make a connection from a released configuration))",
    ]
