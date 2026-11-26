// Checks `playground/diagnostics.js`, run by `tests/test_playground_host.py`:
//
//     node tests/playground_diagnostics.test.mjs

import assert from "node:assert/strict";
import { diagnosticsFor } from "../playground/diagnostics.js";

const line = (n) =>
  `main.gob: warning: ${n} functions use operations the wasm32 backend does not compile yet, and trap if they are called; '--dump select' lists them\n`;

// Without a trap, the warning is dropped whatever its count.
assert.equal(diagnosticsFor(line(2), false), "");
assert.equal(diagnosticsFor(line(3), false), "");
// With a trap, it stays.
assert.equal(diagnosticsFor(line(2), true).includes("2 functions"), true);
// Other lines are kept, trimmed.
assert.equal(diagnosticsFor("main.gob:2:3: type error: y\n" + line(3), false), "main.gob:2:3: type error: y");
console.log("ok");
