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

import { readFile } from "node:fs/promises";
import { loadCompiler, compile, run, highlight, format } from "../playground/host.js";

if (process.argv[2] === "--tools") {
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
