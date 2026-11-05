"""The ASGI benchmark server: a bare ASGI application, no framework, served
by uvicorn with uvloop and httptools, one process per core. Serves the routes
bench/http/turkey/Server.gob lists.

    uvicorn --app-dir bench/http/python asgi:app --workers N ...
"""

HELLO = b"Hello, world!\n"
CHUNK = b"x" * 16384
MEBIBYTE = b"x" * (16384 * 64)


def head(status, kind, length=None):
    headers = [(b"content-type", kind)]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return {"type": "http.response.start", "status": status, "headers": headers}


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    method, path = scope["method"], scope["path"]
    if method == "GET" and path == "/hello":
        await send(head(200, b"text/plain", len(HELLO)))
        await send({"type": "http.response.body", "body": HELLO})
    elif method == "GET" and path == "/1m":
        await send(head(200, b"application/octet-stream", len(MEBIBYTE)))
        await send({"type": "http.response.body", "body": MEBIBYTE})
    elif method == "POST" and path == "/upload":
        read = 0
        more = True
        while more:
            message = await receive()
            read += len(message.get("body", b""))
            more = message.get("more_body", False)
        if read > 2 << 20:
            await send(head(400, b"text/plain", 0))
            await send({"type": "http.response.body", "body": b""})
        else:
            await send(head(200, b"text/plain", 3))
            await send({"type": "http.response.body", "body": b"ok\n"})
    elif method == "GET" and path == "/stream":
        await send(head(200, b"application/octet-stream"))
        for _ in range(64):
            await send({"type": "http.response.body", "body": CHUNK, "more_body": True})
        await send({"type": "http.response.body", "body": b""})
    else:
        await send(head(404, b"text/plain", 0))
        await send({"type": "http.response.body", "body": b""})
