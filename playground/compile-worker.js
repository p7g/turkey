// The compile Worker: loads the compiler once, when the page says how, then
// answers each `compile` message with the program's bytes or the compiler's
// diagnostics, and each `check`, `highlight` and `format` message with what
// those commands write. A module Worker, so it imports `host.js` as the page
// does.
//
// The page starts it twice: once to compile, and once, as the tools Worker,
// to check, highlight and format, so that none of them waits behind a compile
// that takes seconds. `turkey.wasm` is compiled once per page: the first
// compile Worker fetches and compiles it and hands the `WebAssembly.Module` to
// the page, which passes it to the tools Worker and to any compile Worker it
// starts later. Both fetch `lib.tar.gz`, which `check` reads as `compile` does;
// the tools Worker is told to load after the compile Worker has, so its fetch
// is answered from the HTTP cache.
//
// Messages in:  { type: "load", module, lib }       first, and once: `module`
//                                                   is a compiled turkey.wasm
//                                                   or null to fetch one, and
//                                                   `lib` says whether to
//                                                   fetch lib.tar.gz
//               { type: "compile", id, files }      `files` as `compile`
//                                                   takes them, the entry
//                                                   first
//               { type: "check", id, files }        `files` as for `compile`
//               { type: "highlight", id, source }
//               { type: "format", id, source, name } `name` is the file's,
//                                                   for the diagnostics
// Messages out: { type: "status", text }            progress while loading
//               { type: "ready", module }           the compiler is loaded
//               { type: "failed", text }            loading failed for good
//               { type: "compiled", id, bytes, status, diagnostics, ms }
//               { type: "checked", id, files, diagnostics, types, status, ms }
//               { type: "highlighted", id, source, html }
//               { type: "formatted", id, source, text, status, diagnostics }
// where `html` and `text` are null if the command failed, and a `checked`
// message's `diagnostics`, `types` and `status` are what `check` returns as
// those, or all null if the host failed.

import { loadCompiler, compile, check, highlight, format } from "./host.js";

// Both files are fetched with the default cache mode, so the browser's HTTP
// cache decides whether the network is touched: the server's validators
// (ETag or Last-Modified) are enough for a revalidation, and a
// `Cache-Control` lifetime avoids even that. `compileStreaming` compiles the
// module while it downloads, and Chrome keeps the compiled code beside the
// cached response, so a repeat visit skips both the transfer and most of the
// compile.
//
// `assetQuery` is empty when the page is served from a checkout. The deploy
// (`scripts/playground-assemble.sh`) sets it to `?v=<commit>`, so that the two
// files a deploy replaces are fetched under URLs no earlier deploy used, and a
// cache holding one deploy's `turkey.wasm` cannot be paired with another's
// `lib.tar.gz`.
const assetQuery = "";

async function fetchOk(url) {
  const response = await fetch(url + assetQuery);
  if (!response.ok) {
    throw new Error(`${url}: ${response.status} ${response.statusText}`);
  }
  return response;
}

async function load({ module, lib }) {
  postMessage({ type: "status", text: "Loading the compiler..." });
  const [compiled, archive] = await Promise.all([
    module ?? WebAssembly.compileStreaming(fetchOk("turkey.wasm")),
    lib ? fetchOk("lib.tar.gz").then((response) => response.arrayBuffer()) : null,
  ]);
  return await loadCompiler(compiled, archive === null ? null : new Uint8Array(archive));
}

// Settles with the compiler, or null if it failed to load, once the page's
// `load` message has arrived.
let loaded;
const loading = new Promise((resolve) => {
  loaded = resolve;
});
let loadAsked = false;

function start(data) {
  loadAsked = true;
  loaded(
    load(data).then(
      (compiler) => {
        postMessage({ type: "ready", module: compiler.module });
        return compiler;
      },
      (error) => {
        postMessage({ type: "failed", text: String(error && error.message ? error.message : error) });
        return null;
      },
    ),
  );
}

// Each reply carries the source it was made from, so the page can tell a
// reply for text it has since edited.
const handlers = {
  async compile(compiler, { id, files }) {
    const started = performance.now();
    try {
      const built = await compile(compiler, files);
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
  async check(compiler, { id, files }) {
    const started = performance.now();
    let diagnostics = null;
    let types = null;
    let status = null;
    try {
      ({ diagnostics, types, status } = await check(compiler, files));
    } catch {}
    postMessage({ type: "checked", id, files, diagnostics, types, status, ms: performance.now() - started });
  },
  async highlight(compiler, { id, source }) {
    let html = null;
    try {
      html = (await highlight(compiler, source)).html;
    } catch {}
    postMessage({ type: "highlighted", id, source, html });
  },
  async format(compiler, { id, source, name }) {
    try {
      const { text, status, diagnostics } = await format(compiler, source, name);
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
  if (data.type === "load") {
    if (!loadAsked) {
      start(data);
    }
    return;
  }
  const handler = Object.hasOwn(handlers, data.type) ? handlers[data.type] : null;
  if (handler === null) {
    return;
  }
  const compiler = await loading;
  if (compiler !== null) {
    await handler(compiler, data);
  }
};
