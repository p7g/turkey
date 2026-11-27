// Checks `playground/diagnostics.js`, run by `tests/test_playground_host.py`:
// the filter for a compile's text, and the marks made from `check`'s JSON.
//
//     node tests/playground_diagnostics.test.mjs

import assert from "node:assert/strict";
import { diagnosticsFor, marksFor, shiftMarks } from "../playground/diagnostics.js";

const line = (n) =>
  `main.gob: warning: ${n} functions use operations the wasm32 backend does not compile yet, and trap if they are called; '--dump select' lists them\n`;

// Without a trap, the warning is dropped whatever its count.
assert.equal(diagnosticsFor(line(2), false), "");
assert.equal(diagnosticsFor(line(3), false), "");
// With a trap, it stays.
assert.equal(diagnosticsFor(line(2), true).includes("2 functions"), true);
// Other lines are kept, trimmed.
assert.equal(diagnosticsFor("main.gob:2:3: type error: y\n" + line(3), false), "main.gob:2:3: type error: y");

// A diagnostic as `turkey check --diagnostics=json` writes one.
const at = (line, column, endLine, endColumn, more = {}) => ({
  file: "main.gob",
  line,
  column,
  endLine,
  endColumn,
  severity: "error",
  stage: "type error",
  code: null,
  message: "m",
  notes: [],
  ...more,
});
const spans = (text, ds) => marksFor(text, ds).map((m) => text.slice(m.start, m.end));

// Columns count code points, so a character outside the BMP is one column
// and two UTF-16 units; a tab is one column.
const emoji = 'fun main() {\n\tprint("\u{1F600}" + 1)\n}\n';
assert.deepEqual(spans(emoji, [at(2, 14, 2, 15)]), ["1"]);
assert.deepEqual(spans(emoji, [at(2, 9, 2, 10)]), ["\u{1F600}"]);
assert.deepEqual(spans(emoji, [at(2, 1, 2, 2)]), ["\t"]);
// A span over several lines, and one that ends at the end of the text.
const block = "fun main() {\n\tmatch 3 {\n\t\tx -> print(x)\n\t}\n}";
assert.deepEqual(spans(block, [at(2, 2, 4, 3)]), ["match 3 {\n\t\tx -> print(x)\n\t}"]);
assert.deepEqual(spans(block, [at(5, 1, 5, 2)]), ["}"]);
// Past the end of a line is the line's end, and past the last line the
// text's end.
assert.deepEqual(spans("ab\ncd", [at(1, 2, 1, 40), at(2, 1, 9, 1)]), ["b", "cd"]);
// An empty span marks the character after it, or the one before at the end
// of a line, and stays empty on an empty line.
assert.deepEqual(spans("ab\n\ncd", [at(1, 1, 1, 1), at(1, 3, 1, 3), at(2, 1, 2, 1)]), ["a", "b", ""]);
assert.deepEqual(spans("a\u{1F600}", [at(1, 3, 1, 3)]), ["\u{1F600}"]);
// Another file's diagnostics, and ones without a position, mark nothing.
assert.deepEqual(
  marksFor("x", [at(1, 1, 1, 2, { file: "lib/Data/Int.gob" }), at(null, null, null, null)]),
  [],
);
// The label is the compiler's text less the place, and each note says its line.
const [warning] = marksFor("let unused = 4", [
  at(1, 5, 1, 11, {
    severity: "warning",
    stage: "warning",
    code: "unused-variable",
    message: "'unused' is never used",
    notes: [{ file: "main.gob", line: 1, column: 1, endLine: 1, endColumn: 4, message: "here" }],
  }),
]);
assert.deepEqual(warning, {
  start: 4,
  end: 10,
  severity: "warning",
  label: "warning: 'unused' is never used [unused-variable]",
  notes: ["line 1: note: here"],
});
// A note in another file names that file, since its line is not this one's.
const [elsewhere] = marksFor("x", [
  at(1, 1, 1, 2, {
    notes: [
      { file: "lib/Process.gob", line: 90, column: 2, endLine: 90, endColumn: 8, message: "here" },
      { file: "lib/Process.gob", line: null, column: null, endLine: null, endColumn: null, message: "x" },
    ],
  }),
]);
assert.deepEqual(elsewhere.notes, ["lib/Process.gob:90: note: here", "lib/Process.gob: note: x"]);

// An edit keeps the marks before it, moves the ones after it, and drops the
// ones it reaches into.
const mark = (start, end) => ({ start, end, severity: "error", label: "", notes: [] });
const marks = [mark(0, 2), mark(4, 6), mark(8, 10)];
// One unit typed inside the middle mark.
assert.deepEqual(shiftMarks(marks, { start: 5, oldEnd: 5, newEnd: 6 }), [mark(0, 2), mark(9, 11)]);
// One typed just after a mark, which is also just before the next.
assert.deepEqual(shiftMarks([mark(0, 2), mark(2, 4)], { start: 2, oldEnd: 2, newEnd: 3 }), [
  mark(0, 2),
  mark(3, 5),
]);
// Two deleted between marks.
assert.deepEqual(shiftMarks(marks, { start: 6, oldEnd: 8, newEnd: 6 }), [mark(0, 2), mark(4, 6), mark(6, 8)]);
console.log("ok");
