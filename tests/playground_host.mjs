// Drives `playground/host.js` under node for `tests/test_playground_host.py`:
//
//     node tests/playground_host.mjs TURKEY.WASM LIB.TAR.GZ SOURCE [STDIN]
//
// compiles SOURCE with the wasm compiler and, if it wrote a program, runs that
// with STDIN's contents on its standard input. Prints one JSON object:
// `compiled`, what `compile` returned less its bytes, and `ran`, what `run`
// returned, or null.

import { readFile } from "node:fs/promises";
import { loadCompiler, compile, run } from "../playground/host.js";

const [wasmPath, libPath, sourcePath, stdinPath] = process.argv.slice(2);
const compiler = await loadCompiler(await readFile(wasmPath), await readFile(libPath));
const { bytes, ...compiled } = await compile(compiler, await readFile(sourcePath, "utf8"));
const stdin = stdinPath === undefined ? "" : await readFile(stdinPath);
const ran = bytes === null ? null : await run(bytes, { stdin });
process.stdout.write(JSON.stringify({ compiled, ran }) + "\n");
