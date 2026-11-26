// The compile Worker: loads `turkey.wasm` and `lib.tar.gz` once, then answers
// each `compile` message with the program's bytes or the compiler's
// diagnostics. A module Worker, so it imports `host.js` as the page does.
//
// Messages in:  { type: "compile", id, source }
// Messages out: { type: "status", text }            progress while loading
//               { type: "ready" }                   the compiler is loaded
//               { type: "failed", text }            loading failed for good
//               { type: "compiled", id, bytes, status, diagnostics, ms }

import { loadCompiler, compile } from "./host.js";

// Both files are fetched with the default cache mode, so the browser's HTTP
// cache decides whether the network is touched: the server's validators
// (ETag or Last-Modified) are enough for a revalidation, and a
// `Cache-Control` lifetime avoids even that. `compileStreaming` compiles the
// module while it downloads, and Chrome keeps the compiled code beside the
// cached response, so a repeat visit skips both the transfer and most of the
// compile.
async function fetchOk(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url}: ${response.status} ${response.statusText}`);
  }
  return response;
}

async function load() {
  postMessage({ type: "status", text: "Loading the compiler..." });
  const [module, lib] = await Promise.all([
    WebAssembly.compileStreaming(fetchOk("turkey.wasm")),
    fetchOk("lib.tar.gz").then((response) => response.arrayBuffer()),
  ]);
  const compiler = await loadCompiler(module, new Uint8Array(lib));
  return compiler;
}

const loading = load().then(
  (compiler) => {
    postMessage({ type: "ready" });
    return compiler;
  },
  (error) => {
    postMessage({ type: "failed", text: String(error && error.message ? error.message : error) });
    return null;
  },
);

onmessage = async ({ data }) => {
  if (data.type !== "compile") {
    return;
  }
  const compiler = await loading;
  if (compiler === null) {
    return;
  }
  const started = performance.now();
  try {
    const built = await compile(compiler, data.source);
    postMessage({
      type: "compiled",
      id: data.id,
      bytes: built.bytes,
      status: built.status,
      diagnostics: built.diagnostics,
      ms: performance.now() - started,
    });
  } catch (error) {
    postMessage({
      type: "compiled",
      id: data.id,
      bytes: null,
      status: null,
      diagnostics: `the host failed: ${error}\n`,
      ms: performance.now() - started,
    });
  }
};
