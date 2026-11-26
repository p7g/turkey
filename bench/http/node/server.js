// The Node http benchmark server: `node server.js PORT [CERT KEY]`. Node runs
// JavaScript on one thread, so it uses the cores it is given the way a Node
// deployment does: `cluster`, one process per core, `BENCH_WORKERS` of them,
// all accepting on one port. Serves the routes
// bench/http/turkey/Server.gob lists.
"use strict";
const cluster = require("node:cluster");
const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");

const workers = Number(process.env.BENCH_WORKERS || 1);
const [port, cert, key] = process.argv.slice(2);

if (cluster.isPrimary && workers > 1) {
  for (let i = 0; i < workers; i++) cluster.fork();
} else {
  const hello = Buffer.from("Hello, world!\n");
  const chunk = Buffer.alloc(16384, "x");
  const mebibyte = Buffer.alloc(16384 * 64, "x");
  const handle = (req, res) => {
    if (req.method === "GET" && req.url === "/hello") {
      res.writeHead(200, { "content-type": "text/plain", "content-length": hello.length });
      res.end(hello);
    } else if (req.method === "GET" && req.url === "/1m") {
      res.writeHead(200, { "content-type": "application/octet-stream", "content-length": mebibyte.length });
      res.end(mebibyte);
    } else if (req.method === "POST" && req.url === "/upload") {
      let read = 0;
      req.on("data", (d) => {
        read += d.length;
      });
      req.on("end", () => {
        if (read > 2 << 20) {
          res.writeHead(400);
          res.end();
        } else {
          res.writeHead(200, { "content-type": "text/plain", "content-length": 3 });
          res.end("ok\n");
        }
      });
    } else if (req.method === "GET" && req.url === "/stream") {
      res.writeHead(200, { "content-type": "application/octet-stream" });
      // Write the next piece once the last has drained, as a stream from
      // a slower source would.
      let left = 64;
      const more = () => {
        while (left > 0) {
          left--;
          if (!res.write(chunk)) return res.once("drain", more);
        }
        res.end();
      };
      more();
    } else {
      res.writeHead(404);
      res.end();
    }
  };
  const server = cert
    ? https.createServer({ cert: fs.readFileSync(cert), key: fs.readFileSync(key), ALPNProtocols: ["http/1.1"] }, handle)
    : http.createServer(handle);
  server.keepAliveTimeout = 60000;
  server.listen(Number(port), "127.0.0.1");
}
