// Drives `playground/host.js` under node for `tests/test_playground_host.py`:
//
//     node tests/playground_host.mjs TURKEY.WASM LIB.TAR.GZ SOURCE [STDIN]
//
// compiles SOURCE with the wasm compiler and, if it wrote a program, runs that
// with STDIN's contents on its standard input. SOURCE is a file, compiled as
// `main.gob`, or a directory, whose `main.gob` is the entry and whose other
// `.gob` files, at any depth, are its modules. Prints one JSON object:
// `compiled`, what `compile` returned less its bytes, `ran`, what `run`
// returned, or null, and `streamed`, what `run` passed to `onWrite`: the
// bytes of each stream, concatenated and decoded, and the number of calls.
//
//     node tests/playground_host.mjs --tools TURKEY.WASM SOURCE
//
// loads the compiler without the library, as the page's tools Worker does,
// and prints `{ highlighted, formatted }`, what `highlight` and `format`
// return for SOURCE.
//
//     node tests/playground_host.mjs --check TURKEY.WASM LIB.TAR.GZ SOURCE
//
// prints `{ checked }`, what `check` returns for SOURCE, a file or a
// directory as above.
//
//     node tests/playground_host.mjs --worker TURKEY.WASM LIB.TAR.GZ SOURCE
//
// runs `playground/compile-worker.js` in this thread, as the page's tools
// Worker: it is loaded as the page loads it, its fetches answered from the
// two files, and sent one `check` message for SOURCE's files. Prints the
// types of the messages it sent, as `replies`, and the `checked` message it
// answered with, less `ms`.

import { readdir, readFile, stat } from "node:fs/promises";
import { join, sep } from "node:path";
import { loadCompiler, compile, check, run, highlight, format } from "../playground/host.js";

// Returns the `[name, source]` pairs `compile` takes for SOURCE.
async function filesAt(path) {
  if (!(await stat(path)).isDirectory()) {
    return [["main.gob", await readFile(path, "utf8")]];
  }
  const names = (await readdir(path, { recursive: true }))
    .map((name) => name.split(sep).join("/"))
    .filter((name) => name.endsWith(".gob") && name !== "main.gob")
    .sort();
  return Promise.all(
    ["main.gob", ...names].map(async (name) => [name, await readFile(join(path, name), "utf8")]),
  );
}

if (process.argv[2] === "--worker") {
  const [wasmPath, libPath, sourcePath] = process.argv.slice(3);
  const assets = { "turkey.wasm": wasmPath, "lib.tar.gz": libPath };
  globalThis.fetch = async (url) => {
    const type = url.endsWith(".wasm") ? "application/wasm" : "application/gzip";
    return new Response(await readFile(assets[url]), { headers: { "content-type": type } });
  };
  const replies = [];
  let replied;
  const checked = new Promise((resolve) => {
    replied = resolve;
  });
  globalThis.postMessage = (data) => {
    replies.push(data.type);
    if (data.type === "checked" || data.type === "failed") {
      replied(data);
    }
  };
  // A Worker's global scope has `onmessage`, which the module assigns.
  globalThis.onmessage = null;
  await import("../playground/compile-worker.js");
  const deliver = (data) => globalThis.onmessage({ data });
  deliver({ type: "load", module: null, lib: true });
  deliver({ type: "check", id: 7, files: await filesAt(sourcePath) });
  const { ms, ...answer } = await checked;
  process.stdout.write(JSON.stringify({ replies, checked: answer }) + "\n");
} else if (process.argv[2] === "--check") {
  const [wasmPath, libPath, sourcePath] = process.argv.slice(3);
  const compiler = await loadCompiler(await readFile(wasmPath), await readFile(libPath));
  const checked = await check(compiler, await filesAt(sourcePath));
  process.stdout.write(JSON.stringify({ checked }) + "\n");
} else if (process.argv[2] === "--tools") {
  const [wasmPath, sourcePath] = process.argv.slice(3);
  const compiler = await loadCompiler(await readFile(wasmPath));
  const source = await readFile(sourcePath, "utf8");
  const highlighted = await highlight(compiler, source);
  const formatted = await format(compiler, source);
  process.stdout.write(JSON.stringify({ highlighted, formatted }) + "\n");
} else {
  const [wasmPath, libPath, sourcePath, stdinPath] = process.argv.slice(2);
  const compiler = await loadCompiler(await readFile(wasmPath), await readFile(libPath));
  const { bytes, ...compiled } = await compile(compiler, await filesAt(sourcePath));
  const stdin = stdinPath === undefined ? "" : await readFile(stdinPath);
  const parts = { stdout: [], stderr: [] };
  let calls = 0;
  const onWrite = (stream, chunk) => {
    calls++;
    parts[stream].push(chunk);
  };
  const ran = bytes === null ? null : await run(bytes, { stdin, onWrite });
  const joined = (chunks) => new TextDecoder().decode(Buffer.concat(chunks));
  const streamed = { stdout: joined(parts.stdout), stderr: joined(parts.stderr), calls };
  process.stdout.write(JSON.stringify({ compiled, ran, streamed }) + "\n");
}
