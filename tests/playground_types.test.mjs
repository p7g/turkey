// Checks `playground/types.js`, the Types view's logic, and
// `playground/shortcuts.js`, the keys of Run and Format, run by
// `tests/test_playground_host.py`:
//
//     node tests/playground_types.test.mjs

import assert from "node:assert/strict";
import { offsetsIn } from "../playground/diagnostics.js";
import { shortcutFor, shortcutText } from "../playground/shortcuts.js";
import { nameRange, typeMarksFor, typesFor } from "../playground/types.js";

// Bindings as `turkey check --types=json` writes them.
const at = (file, line, column, name, type) => ({ file, line, column, name, type });
const error = { severity: "error" };
const warning = { severity: "warning" };

// The files come in the order the page lists them, whatever the compiler's,
// and a file's bindings in the order of their positions.
{
  const types = [
    at("Shapes.gob", 3, 1, "area", "fun(Int) -> Int"),
    at("main.gob", 3, 9, "b", "Int"),
    at("main.gob", 3, 6, "a", "String"),
    at("main.gob", 1, 1, "main", "fun() -> Unit"),
  ];
  const view = typesFor([warning], types, ["main.gob", "Shapes.gob", "Empty.gob"]);
  assert.equal(view.current, true);
  assert.deepEqual(
    view.files.map(({ file, bindings }) => [file, bindings.map((b) => b.name)]),
    [
      ["main.gob", ["main", "a", "b"]],
      ["Shapes.gob", ["area"]],
    ],
  );
  assert.deepEqual(view.files[1].bindings[0], { name: "area", type: "fun(Int) -> Int", line: 3, column: 1 });
}

// A file the page no longer has is left out.
assert.deepEqual(typesFor([], [at("Gone.gob", 1, 1, "x", "Int")], ["main.gob"]).files, []);

// A check that found an error and wrote no types says nothing about the
// program; one that found an error after the types were written, or none,
// is current.
assert.equal(typesFor([error], [], ["main.gob"]).current, false);
assert.equal(typesFor([error], [at("main.gob", 1, 1, "main", "fun() -> Unit")], ["main.gob"]).current, true);
assert.equal(typesFor([], [], ["main.gob"]).current, true);

// A `fun` is placed at its keyword, and its name is found after it; a
// variable is placed at its name.
{
  const text = "fun main() {\n}\n\nlet (b, a) = (1, 2)\n";
  const offset = offsetsIn(text);
  assert.deepEqual(nameRange(text, offset(1, 1), "main"), [4, 8]);
  assert.deepEqual(nameRange(text, offset(4, 9), "a"), [24, 25]);
  assert.deepEqual(nameRange(text, offset(4, 6), "b"), [21, 22]);
}

// A name is a whole word: `max` is not found inside `maximum`.
assert.deepEqual(nameRange("fun maximum(max) = max\n", 0, "max"), [12, 15]);
// A name not on the line is no range, and the text after the line is not
// looked at.
assert.deepEqual(nameRange("fun f() = 1\nlet g = 2\n", 0, "g"), [0, 0]);
// Columns count code points, offsets UTF-16 units.
{
  const text = 'let s = "\u{1F600}"; let t = 1\n';
  assert.deepEqual(nameRange(text, offsetsIn(text)(1, 18), "t"), [18, 19]);
}

// The marks label each found name with its type, and skip the ones not
// found.
assert.deepEqual(
  typeMarksFor("fun twice(f, x) = f(f(x))\n", [
    { name: "twice", type: "fun(fun(a) -> a, a) -> a", line: 1, column: 1 },
    { name: "missing", type: "Int", line: 1, column: 1 },
  ]),
  [{ start: 4, end: 9, severity: "type", label: "twice : fun(fun(a) -> a, a) -> a", notes: [] }],
);

// -- Shortcuts --------------------------------------------------------------------

const key = (key, code, mods = {}) => ({
  key,
  code,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  altKey: false,
  repeat: false,
  isComposing: false,
  ...mods,
});

assert.equal(shortcutFor(key("Enter", "Enter", { ctrlKey: true })), "run");
assert.equal(shortcutFor(key("Enter", "NumpadEnter", { metaKey: true })), "run");
assert.equal(shortcutFor(key("Enter", "Enter")), null);
assert.equal(shortcutFor(key("Enter", "Enter", { ctrlKey: true, shiftKey: true })), null);
assert.equal(shortcutFor(key("Enter", "Enter", { ctrlKey: true, repeat: true })), null);
assert.equal(shortcutFor(key("Enter", "Enter", { ctrlKey: true, isComposing: true })), null);
// Option+Shift+F types "Ï" on a Mac, and the key's place is what counts.
assert.equal(shortcutFor(key("Ï", "KeyF", { shiftKey: true, altKey: true })), "format");
assert.equal(shortcutFor(key("F", "KeyF", { shiftKey: true, altKey: true })), "format");
assert.equal(shortcutFor(key("F", "KeyF", { shiftKey: true })), null);
assert.equal(shortcutFor(key("F", "KeyF", { shiftKey: true, altKey: true, ctrlKey: true })), null);
assert.equal(shortcutFor(key("f", "KeyF", { ctrlKey: true })), null);

assert.equal(shortcutText("run", true).text, "Cmd+Enter");
assert.equal(shortcutText("run", false).text, "Ctrl+Enter");
assert.equal(shortcutText("format", true).text, "Shift+Option+F");
assert.equal(shortcutText("format", false).aria, "Shift+Alt+F");

console.log("ok");
