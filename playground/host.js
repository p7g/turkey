// Runs the wasm32-wasi compiler, `turkey.wasm`, and the programs it writes,
// over browser_wasi_shim's in-memory file system.
//
// A plain ES module with no imports but the vendored shim, so a page's Worker
// and node load the same file. It reads nothing from the network or the disk:
// the caller fetches `turkey.wasm` and `lib.tar.gz` (`scripts/lib-tar.sh`) and
// hands over their bytes.
//
//     const compiler = await loadCompiler(wasmBytes, libTarGz);
//     const built = await compile(compiler, source);
//     if (built.bytes) { const ran = await run(built.bytes); }

import {
  WASI,
  File,
  Directory,
  OpenFile,
  PreopenDirectory,
  ConsoleStdout,
} from "./vendor/browser_wasi_shim/index.js";

/**
 * Returns a compiler for `compile`: `wasm` compiled, and `libArchive`
 * unpacked. `wasm` is `turkey.wasm` as bytes or an already compiled
 * `WebAssembly.Module`; `libArchive` is `lib.tar.gz`'s bytes. Neither is read
 * again, so one compiler serves any number of compiles.
 */
export async function loadCompiler(wasm, libArchive) {
  const module = wasm instanceof WebAssembly.Module ? wasm : await WebAssembly.compile(wasm);
  const files = untar(await gunzip(libArchive));
  const root = directoryOf(files, { readonly: true });
  const lib = root.contents.get("lib");
  if (!(lib instanceof Directory)) {
    throw new Error("lib.tar.gz holds no lib/ directory");
  }
  return { module, lib };
}

/**
 * Compiles `source` as the module `main.gob` for wasm32-wasi. Returns
 * `{ bytes, status, diagnostics, memory }`: `bytes` is the program as a
 * `Uint8Array`, or null when the compiler wrote none; `status` is the
 * compiler's exit status, or null if it trapped; `diagnostics` is everything
 * it wrote to stdout and stderr, as text, with a trap's message last; `memory`
 * is its linear memory's size in bytes at exit, which is its peak, since wasm
 * memory never shrinks.
 */
export async function compile(compiler, source) {
  const work = new PreopenDirectory(".", [["main.gob", new File(new TextEncoder().encode(source))]]);
  // `lib` is its own preopen, and read-only, so that no compile can change
  // what the next one reads: the compiler resolves `lib/...` against the
  // preopen with the longest matching name, and everything else against `.`.
  const fds = [
    new OpenFile(new File([])),
    null,
    null,
    new PreopenDirectory("lib", compiler.lib.contents),
    work,
  ];
  const argv = ["turkey", "build", "-o", "out.wasm", "--target", "wasm32-wasi", "main.gob"];
  const result = await start(compiler.module, argv, ["TURKEY_LIB=lib"], fds, { merged: true });
  const out = work.dir.contents.get("out.wasm");
  return {
    bytes: result.status === 0 && out instanceof File ? out.data : null,
    status: result.status,
    diagnostics: result.stdout + (result.trap === null ? "" : result.trap + "\n"),
    memory: result.memory,
  };
}

/**
 * Runs `program`, a wasm32-wasi module as bytes or a `WebAssembly.Module`,
 * with `stdin` (a string or bytes, empty by default) on its standard input and
 * an empty directory preopened as `.`. Returns `{ status, stdout, stderr, trap,
 * memory }`: `status` is its exit status, or null if it trapped, and `trap` is
 * the trap's message, or null. A panic is not a trap: it writes its report to
 * stderr and exits with a non-zero status.
 */
export async function run(program, { stdin = "", args = [] } = {}) {
  const module = program instanceof WebAssembly.Module ? program : await WebAssembly.compile(program);
  const input = typeof stdin === "string" ? new TextEncoder().encode(stdin) : stdin;
  const fds = [new OpenFile(new File(input)), null, null, new PreopenDirectory(".", [])];
  return await start(module, ["main", ...args], [], fds, { merged: false });
}

// Instantiates `module`, fills fds 1 and 2 with streams that collect what is
// written, and runs `_start`. With `merged`, both streams collect into one
// text, in the order the writes were made, as a terminal shows them.
async function start(module, argv, env, fds, { merged }) {
  const decoder = { stdout: new TextDecoder(), stderr: new TextDecoder() };
  const text = { stdout: "", stderr: "" };
  // A `ConsoleStdout` is a character device that cannot seek, which the
  // runtime takes for a terminal and flushes a line at a time. That keeps
  // stdout and stderr interleaved as they were written.
  const stream = (name) =>
    new ConsoleStdout((bytes) => {
      const into = merged ? "stdout" : name;
      text[into] += decoder[into].decode(bytes, { stream: true });
    });
  fds[1] = stream("stdout");
  fds[2] = stream("stderr");
  // The shim logs every call unless `debug` is false: left out, it is on.
  const wasi = new WASI(argv, env, fds, { debug: false });
  const instance = await WebAssembly.instantiate(module, {
    wasi_snapshot_preview1: wasi.wasiImport,
  });
  let status = null;
  let trap = null;
  try {
    status = wasi.start(instance);
  } catch (error) {
    // A trap is a `WebAssembly.RuntimeError`, a stack overflow V8's
    // `RangeError`, and an import the shim leaves unimplemented throws a
    // string. All three end the program with no status.
    trap = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  }
  for (const name of ["stdout", "stderr"]) {
    text[name] += decoder[name].decode();
  }
  return {
    status,
    stdout: text.stdout,
    stderr: text.stderr,
    trap,
    memory: instance.exports.memory.buffer.byteLength,
  };
}

// Returns `bytes`, a gzip stream, decompressed.
async function gunzip(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

// Returns the regular files in `tar`, a POSIX ustar archive, as [path, bytes]
// pairs in archive order. It reads what `scripts/lib-tar.sh` writes and
// refuses anything else: a member that is not a regular file or a directory,
// or a header that is not ustar's.
function untar(tar) {
  const files = [];
  const latin1 = new TextDecoder("latin1");
  const field = (header, at, length) => {
    const bytes = header.subarray(at, at + length);
    const end = bytes.indexOf(0);
    return latin1.decode(end < 0 ? bytes : bytes.subarray(0, end));
  };
  let at = 0;
  // The archive ends with two zero blocks; one is enough to stop at.
  while (at + 512 <= tar.length && tar[at] !== 0) {
    const header = tar.subarray(at, at + 512);
    if (field(header, 257, 5) !== "ustar") {
      throw new Error(`lib.tar.gz: the header at byte ${at} is not ustar`);
    }
    const prefix = field(header, 345, 155);
    const name = (prefix === "" ? "" : prefix + "/") + field(header, 0, 100);
    const size = parseInt(field(header, 124, 12).trim(), 8);
    const type = String.fromCharCode(header[156]);
    const body = at + 512;
    if (type === "0" || type === "\0") {
      if (body + size > tar.length) {
        throw new Error(`lib.tar.gz: '${name}' runs past the end of the archive`);
      }
      files.push([name, tar.slice(body, body + size)]);
    } else if (type !== "5") {
      throw new Error(`lib.tar.gz: '${name}' has type '${type}', which is not a file`);
    }
    at = body + Math.ceil(size / 512) * 512;
  }
  return files;
}

// Returns a `Directory` holding `files`, [path, bytes] pairs, making each
// path's parent directories. `options` is given to each `File`.
function directoryOf(files, options) {
  // Build plain maps first: a shim `Directory` records each child directory's
  // parent when it is made, so the tree is made from the leaves up, once.
  const tree = new Map();
  for (const [path, bytes] of files) {
    const parts = path.split("/").filter((part) => part !== "" && part !== ".");
    let here = tree;
    for (const part of parts.slice(0, -1)) {
      if (!here.has(part)) {
        here.set(part, new Map());
      }
      here = here.get(part);
    }
    here.set(parts[parts.length - 1], new File(bytes, options));
  }
  const build = (map) =>
    new Directory([...map].map(([name, entry]) => [name, entry instanceof Map ? build(entry) : entry]));
  return build(tree);
}
