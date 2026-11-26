// Drives `playground/host.js` under node for `tests/test_playground_host.py`:
//
//     node tests/playground_host.mjs TURKEY.WASM LIB.TAR.GZ SOURCE [STDIN]
//
// compiles SOURCE with the wasm compiler and, if it wrote a program, runs that
// with STDIN's contents on its standard input. Prints one JSON object:
// `compiled`, what `compile` returned less its bytes, and `ran`, what `run`
// returned, or null.
//
//     node tests/playground_host.mjs --tools TURKEY.WASM SOURCE
//
// loads the compiler without the library, as the page's tools Worker does,
// and prints `{ highlighted, formatted }`, what `highlight` and `format`
// return for SOURCE.
//
//     node tests/playground_host.mjs --check TURKEY.WASM LIB.TAR.GZ SOURCE
//
// prints `{ checked }`, what `check` returns for SOURCE.
//
//     node tests/playground_host.mjs --worker TURKEY.WASM LIB.TAR.GZ SOURCE
//
// runs `playground/compile-worker.js` in this thread, as the page's tools
// Worker: it is loaded as the page loads it, its fetches answered from the
// two files, and sent one `check` message for SOURCE. Prints the `checked`
// message it answers with, less `ms`.

import { readFile } from "node:fs/promises";
import { loadCompiler, compile, run, check, highlight, format } from "../playground/host.js";

if (process.argv[2] === "--worker") {
  const [wasmPath, libPath, sourcePath] = process.argv.slice(3);
  const files = { "turkey.wasm": wasmPath, "lib.tar.gz": libPath };
  globalThis.fetch = async (url) => {
    const bytes = await readFile(files[url]);
    return new Response(bytes, { headers: { "content-type": url.endsWith(".wasm") ? "application/wasm" : "" } });
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
  deliver({ type: "check", id: 7, source: await readFile(sourcePath, "utf8") });
  const { ms, ...answer } = await checked;
  process.stdout.write(JSON.stringify({ replies, checked: answer }) + "\n");
} else if (process.argv[2] === "--check") {
  const [wasmPath, libPath, sourcePath] = process.argv.slice(3);
  const compiler = await loadCompiler(await readFile(wasmPath), await readFile(libPath));
  const checked = await check(compiler, await readFile(sourcePath, "utf8"));
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
  const { bytes, ...compiled } = await compile(compiler, await readFile(sourcePath, "utf8"));
  const stdin = stdinPath === undefined ? "" : await readFile(stdinPath);
  const ran = bytes === null ? null : await run(bytes, { stdin });
  process.stdout.write(JSON.stringify({ compiled, ran }) + "\n");
}
