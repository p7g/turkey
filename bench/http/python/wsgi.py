"""The WSGI benchmark server: a bare WSGI application served by gunicorn's
threaded workers (`gthread`, which keep connections alive), one process per
core. Serves the routes bench/http/turkey/Server.gob lists.

    gunicorn --chdir bench/http/python wsgi:app --workers N --worker-class gthread ...
"""

HELLO = b"Hello, world!\n"
CHUNK = b"x" * 16384
MEBIBYTE = b"x" * (16384 * 64)


def pieces():
    for _ in range(64):
        yield CHUNK


def app(environ, start_response):
    method, path = environ["REQUEST_METHOD"], environ["PATH_INFO"]
    if method == "GET" and path == "/hello":
        start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(HELLO)))])
        return [HELLO]
    if method == "GET" and path == "/1m":
        start_response("200 OK", [("Content-Type", "application/octet-stream"), ("Content-Length", str(len(MEBIBYTE)))])
        return [MEBIBYTE]
    if method == "POST" and path == "/upload":
        body = environ["wsgi.input"]
        read = 0
        while True:
            got = body.read(65536)
            if not got:
                break
            read += len(got)
        if read > 2 << 20:
            start_response("400 Bad Request", [("Content-Length", "0")])
            return [b""]
        start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", "3")])
        return [b"ok\n"]
    if method == "GET" and path == "/stream":
        start_response("200 OK", [("Content-Type", "application/octet-stream")])
        return pieces()
    start_response("404 Not Found", [("Content-Length", "0")])
    return [b""]
