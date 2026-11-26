// Runs the wasm32-wasi compiler, `turkey.wasm`, and the programs it writes,
// over browser_wasi_shim's in-memory file system.
//
// A plain ES module with no imports but the vendored shim, so a page's Worker
// and node load the same file. It reads nothing from the network or the disk:
// the caller fetches `turkey.wasm` and `lib.tar.gz` (`scripts/lib-tar.sh`) and
// hands over their bytes.
//
//     const compiler = await loadCompiler(wasmBytes, libTarGz);
//     const built = await compile(compiler, [["main.gob", source], ["Geometry.gob", geometry]]);
//     if (built.bytes) { const ran = await run(built.bytes); }
//     const { html } = await highlight(compiler, source);
//     const { text } = await format(compiler, source);

import {
  WASI,
  File,
  Directory,
  OpenFile,
  PreopenDirectory,
  ConsoleStdout,
} from "./vendor/browser_wasi_shim/index.js";

/**
 * Returns a compiler for `compile`, `highlight` and `format`: `wasm` compiled,
 * and `libArchive` unpacked. `wasm` is `turkey.wasm` as bytes or an already
 * compiled `WebAssembly.Module`. `libArchive` is `lib.tar.gz`'s bytes, or null
 * for a compiler that only highlights and formats, since neither reads the
 * library. Neither argument is read again, so one compiler serves any number
 * of calls. Throws if the archive is not a well-formed ustar archive holding
 * files under `lib/`.
 */
export async function loadCompiler(wasm, libArchive = null) {
  const module = wasm instanceof WebAssembly.Module ? wasm : await WebAssembly.compile(wasm);
  if (libArchive === null) {
    return { module, lib: null };
  }
  const lib = untar(await gunzip(libArchive))
    .filter(([path]) => path.startsWith("lib/"))
    .map(([path, bytes]) => [path.slice("lib/".length), bytes]);
  if (lib.length === 0) {
    throw new Error("lib.tar.gz holds no files under lib/");
  }
  return { module, lib };
}

/**
 * Compiles a program for wasm32-wasi. `files` is its source files as
 * `[name, source]` pairs, the entry first, each name a path relative to the
 * entry's directory, such as `Geometry.gob` or `Shapes/Circle.gob`; a string
 * is one file, `main.gob`. Returns `{ bytes, status, diagnostics, memory }`:
 * `bytes` is the program as a `Uint8Array`, or null when the compiler wrote
 * none; `status` is the compiler's exit status, or null if it trapped;
 * `diagnostics` is everything it wrote to stdout and stderr, as text, with a
 * trap's message last; `memory` is its linear memory's size in bytes at exit,
 * which is its peak, since wasm memory never shrinks. Throws for a compiler
 * loaded without the library, for no files, and for a name that is not a
 * relative path, has a `.` or `..` part, is under `lib/` or is `out.wasm`,
 * or names a file twice or a file and a directory.
 */
export async function compile(compiler, files) {
  if (compiler.lib === null) {
    throw new Error("this compiler was loaded without lib.tar.gz, so it cannot compile");
  }
  const pairs = typeof files === "string" ? [["main.gob", files]] : files;
  checkNames(pairs.map(([name]) => name));
  const encoder = new TextEncoder();
  const work = new PreopenDirectory(
    ".",
    entriesOf(pairs.map(([name, source]) => [name, encoder.encode(source)])),
  );
  // `lib` is its own preopen: the compiler resolves `lib/...` against the
  // preopen with the longest matching name, and everything else against `.`.
  // Each compile gets its own copy of the tree, files and all, so nothing one
  // compile does to it reaches the next. Sharing one tree would not be safe:
  // the shim's directories hold the maps they are given, and `readonly`
  // guards neither a directory's entries nor `fd_filestat_set_size`. The copy
  // is about 2 MB, which takes a millisecond or two.
  const fds = [
    new OpenFile(new File([])),
    null,
    null,
    new PreopenDirectory("lib", entriesOf(compiler.lib, { readonly: true })),
    work,
  ];
  const argv = ["turkey", "build", "-o", "out.wasm", "--target", "wasm32-wasi", pairs[0][0]];
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
 * Returns `source` as `turkey highlight` writes it: `{ html, status,
 * diagnostics }`. `html` is the HTML fragment, or null if the compiler failed,
 * and `diagnostics` is what it wrote to stderr, with a trap's message last.
 * The fragment escapes every `<`, `>`, `&` and `"` in `source`, so it is safe
 * as an element's content, and its text is exactly `source`.
 */
export async function highlight(compiler, source) {
  const result = await tool(compiler, ["highlight", "-"], source);
  return { html: result.output, status: result.status, diagnostics: result.diagnostics };
}

/**
 * Returns `source`, as the file `name`, laid out as `turkey format` lays it
 * out: `{ text, status, diagnostics }`. `text` is the formatted source, or
 * null if it did not parse or the formatter refused it, and `diagnostics` is
 * what the formatter wrote to stderr, which names the file `name`, with a
 * trap's message last.
 */
export async function format(compiler, source, name = "main.gob") {
  const result = await tool(compiler, ["format", "--stdin-name", name, "-"], source);
  return { text: result.output, status: result.status, diagnostics: result.diagnostics };
}

// Runs the compiler with `args` and `source` on its standard input. Returns
// `{ output, status, diagnostics }`, where `output` is its stdout if it
// exited 0, and otherwise null.
async function tool(compiler, args, source) {
  const stdin = new OpenFile(new File(new TextEncoder().encode(source)));
  const fds = [stdin, null, null, new PreopenDirectory(".", [])];
  const result = await start(compiler.module, ["turkey", ...args], [], fds, { merged: false });
  return {
    output: result.status === 0 ? result.stdout : null,
    status: result.status,
    diagnostics: result.stderr + (result.trap === null ? "" : result.trap + "\n"),
  };
}

/**
 * Runs `program`, a wasm32-wasi module as bytes or a `WebAssembly.Module`,
 * with `args` after its name on its command line, `stdin` (a string or bytes,
 * empty by default) on its standard input, and an empty directory preopened
 * as `.`. Returns `{ status, stdout, stderr, trap,
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
// throws on anything else: a header that is not ustar's or whose checksum is
// wrong, a member that is not a regular file or a directory, a size that is
// not octal, or an archive that ends before its end-of-archive block.
function untar(tar) {
  const files = [];
  const latin1 = new TextDecoder("latin1");
  const field = (header, at, length) => {
    const bytes = header.subarray(at, at + length);
    const end = bytes.indexOf(0);
    return latin1.decode(end < 0 ? bytes : bytes.subarray(0, end));
  };
  const octal = (header, offset, length, what) => {
    const text = field(header, offset, length).trim();
    if (!/^[0-7]+$/.test(text)) {
      throw new Error(`lib.tar.gz: the ${what} in the header at byte ${at} is not octal`);
    }
    return parseInt(text, 8);
  };
  let at = 0;
  for (;;) {
    if (at + 512 > tar.length) {
      throw new Error("lib.tar.gz ends before its end-of-archive block");
    }
    const header = tar.subarray(at, at + 512);
    // The archive ends with two zero blocks; the first is enough to stop at.
    if (header.every((byte) => byte === 0)) {
      return files;
    }
    if (field(header, 257, 5) !== "ustar") {
      throw new Error(`lib.tar.gz: the header at byte ${at} is not ustar`);
    }
    // The checksum is the sum of the header's bytes with its own eight bytes
    // read as spaces.
    let sum = 8 * 0x20;
    for (let i = 0; i < 512; i++) {
      sum += i >= 148 && i < 156 ? 0 : header[i];
    }
    if (octal(header, 148, 8, "checksum") !== sum) {
      throw new Error(`lib.tar.gz: the header at byte ${at} has the wrong checksum`);
    }
    const prefix = field(header, 345, 155);
    const name = (prefix === "" ? "" : prefix + "/") + field(header, 0, 100);
    const size = octal(header, 124, 12, "size");
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
}

// Throws unless `names`, one or more, are paths `compile` can write into its
// directory and find again: relative, with no empty, `.` or `..` part; not
// under `lib/`, which resolves against the library's preopen, nor
// `out.wasm`, which the compile writes; and none naming a file twice or a
// file and a directory.
function checkNames(names) {
  if (names.length === 0) {
    throw new Error("a program needs at least one file, its entry");
  }
  const files = new Set();
  const directories = new Set();
  for (const name of names) {
    const parts = name.split("/");
    if (parts.some((part) => part === "" || part === "." || part === "..")) {
      throw new Error(`'${name}' is not a relative path to a file`);
    }
    if (parts[0] === "lib" || name === "out.wasm") {
      throw new Error(`'${name}' is a name the compile keeps for itself`);
    }
    const parents = parts.slice(1).map((_, i) => parts.slice(0, i + 1).join("/"));
    if (files.has(name) || directories.has(name) || parents.some((parent) => files.has(parent))) {
      throw new Error(`'${name}' names a file twice, or a file and a directory`);
    }
    files.add(name);
    for (const parent of parents) {
      directories.add(parent);
    }
  }
}

// Returns the entries of a directory holding `files`, [path, bytes] pairs, as
// [name, inode] pairs, making each path's parent directories. Each `File`
// copies its bytes, and `options` is given to it.
function entriesOf(files, options) {
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
    [...map].map(([name, entry]) => [name, entry instanceof Map ? new Directory(build(entry)) : entry]);
  return build(tree);
}
