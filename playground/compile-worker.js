// The compile Worker: loads `turkey.wasm` and `lib.tar.gz` once, then answers
// each `compile` message with the program's bytes or the compiler's
// diagnostics, and each `highlight` and `format` message with what those
// commands write. A module Worker, so it imports `host.js` as the page does.
//
// The page starts it twice: once to compile, and once, named "tools", to
// highlight and format, so that neither waits behind a compile that takes
// seconds. The tools Worker never fetches `lib.tar.gz`, since neither command
// reads the library.
//
// Messages in:  { type: "compile", id, source }
//               { type: "highlight", id, source }
//               { type: "format", id, source }
// Messages out: { type: "status", text }            progress while loading
//               { type: "ready" }                   the compiler is loaded
//               { type: "failed", text }            loading failed for good
//               { type: "compiled", id, bytes, status, diagnostics, ms }
//               { type: "highlighted", id, source, html }
//               { type: "formatted", id, source, text, status, diagnostics }
// where `html` and `text` are null if the command failed.

import { loadCompiler, compile, highlight, format } from "./host.js";

const tools = self.name === "tools";

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
    tools ? null : fetchOk("lib.tar.gz").then((response) => response.arrayBuffer()),
  ]);
  const compiler = await loadCompiler(module, lib === null ? null : new Uint8Array(lib));
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

// Each reply carries the source it was made from, so the page can tell a
// reply for text it has since edited.
const handlers = {
  async compile(compiler, { id, source }) {
    const started = performance.now();
    try {
      const built = await compile(compiler, source);
      postMessage({
        type: "compiled",
        id,
        bytes: built.bytes,
        status: built.status,
        diagnostics: built.diagnostics,
        ms: performance.now() - started,
      });
    } catch (error) {
      postMessage({
        type: "compiled",
        id,
        bytes: null,
        status: null,
        diagnostics: `the host failed: ${error}\n`,
        ms: performance.now() - started,
      });
    }
  },
  async highlight(compiler, { id, source }) {
    let html = null;
    try {
      html = (await highlight(compiler, source)).html;
    } catch {}
    postMessage({ type: "highlighted", id, source, html });
  },
  async format(compiler, { id, source }) {
    try {
      const { text, status, diagnostics } = await format(compiler, source);
      postMessage({ type: "formatted", id, source, text, status, diagnostics });
    } catch (error) {
      postMessage({
        type: "formatted",
        id,
        source,
        text: null,
        status: null,
        diagnostics: `the host failed: ${error}\n`,
      });
    }
  },
};

onmessage = async ({ data }) => {
  const handler = Object.hasOwn(handlers, data.type) ? handlers[data.type] : null;
  if (handler === null) {
    return;
  }
  const compiler = await loading;
  if (compiler !== null) {
    await handler(compiler, data);
  }
};
