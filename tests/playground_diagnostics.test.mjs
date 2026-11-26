// Checks `playground/diagnostics.js`, run by `tests/test_playground_host.py`:
//
//     node tests/playground_diagnostics.test.mjs

import assert from "node:assert/strict";
import { refusedCount, withoutBaseline } from "../playground/diagnostics.js";

const line = (n) =>
  `main.gob: warning: ${n} functions use operations the wasm32 backend does not compile yet, and trap if they are called; '--dump select' lists them\n`;

assert.equal(refusedCount(line(3)), 3);
assert.equal(refusedCount("main.gob:1:1: type error: x\n"), 0);

// The baseline's own warning is dropped.
assert.equal(withoutBaseline(line(3), 3), "");
// A larger count is the user's program's, and stays.
assert.equal(withoutBaseline(line(4), 3).includes("4 functions"), true);
// Other lines are kept, trimmed.
assert.equal(withoutBaseline("main.gob:2:3: type error: y\n" + line(3), 3), "main.gob:2:3: type error: y");
// With no baseline known (0), no warning is dropped.
assert.equal(withoutBaseline(line(3), 0).includes("3 functions"), true);
console.log("ok");
