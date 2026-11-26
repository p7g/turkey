// Checks `playground/share.js`, the file names `playground/files.js`
// accepts, and `changedRange` in `playground/editor.js`, run by
// `tests/test_playground_host.py`:
//
//     node tests/playground_share.test.mjs

import assert from "node:assert/strict";
import {
  MAX_SOURCE_BYTES,
  fragmentFor,
  fromBase64url,
  filesFromFragment,
  toBase64url,
} from "../playground/share.js";
import { changedRange } from "../playground/editor.js";
import { nameProblem } from "../playground/files.js";

// Returns the text of the one file, `main.gob`, that `fragment` holds, or
// null if it holds none.
async function sourceFromFragment(fragment) {
  const files = await filesFromFragment(fragment);
  if (files === null) {
    return null;
  }
  assert.equal(files.length, 1);
  assert.equal(files[0][0], "main.gob");
  return files[0][1];
}

// A link made before links held several files opens as it did: the page
// that made this one knew only `#code=`.
assert.deepEqual(await filesFromFragment("#code=SyvNU8hNzMzT0FSo5uIsKMrMK9FQ8kjNycnXUQgpLcpOrVRU0uSq5QIA"), [
  ["main.gob", 'fun main() {\n\tprint("Hello, Turkey!")\n}\n'],
]);
// A program that is only `main.gob` still gets that link, given as a string
// or as files.
assert.equal(
  await fragmentFor([["main.gob", 'fun main() {\n\tprint("Hello, Turkey!")\n}\n']]),
  "#code=SyvNU8hNzMzT0FSo5uIsKMrMK9FQ8kjNycnXUQgpLcpOrVRU0uSq5QIA",
);

// A program of several files gets a `#files=` link, which brings back every
// file, its name, its text and its place, the entry first.
const program = [
  ["main.gob", "import Geometry\nimport Shapes.Circle\n\nfun main() {\n\tprint(Geometry.unit)\n}\n"],
  ["Shapes/Circle.gob", "export (area)\n\nfun area(r) = 3 * r * r\n"],
  ["Geometry.gob", "export (unit)\n\nlet unit = \"\u{1F983} <b>&amp;\"\r\n"],
];
const several = await fragmentFor(program);
assert.match(several, /^#files=[A-Za-z0-9_-]*$/);
assert.deepEqual(await filesFromFragment(several), program);
// `files` wins over `code`, whichever comes first.
const hello = await fragmentFor("fun main() {}\n");
assert.deepEqual(await filesFromFragment(hello + "&" + several.slice(1)), program);
assert.deepEqual(await filesFromFragment(several + "&" + hello.slice(1)), program);

// A `files` link whose files are not a program's is refused with the reason.
const filesLink = async (value) =>
  "#files=" +
  toBase64url(
    new Uint8Array(
      await new Response(
        new Blob([new TextEncoder().encode(value)]).stream().pipeThrough(new CompressionStream("deflate-raw")),
      ).arrayBuffer(),
    ),
  );
for (const [value, why] of [
  ["not json", /not JSON/],
  ["[]", /one or more files/],
  ['{"main.gob": ""}', /one or more files/],
  ['[["main.gob"]]', /a name and a source/],
  ['[["main.gob", 1]]', /a name and a source/],
  ['[["main.gob", ""], ["../Evil.gob", ""]]', /not a module's file name/],
  ['[["main.gob", ""], ["lib/Prelude.gob", ""]]', /not a module's file name/],
  ['[["main.gob", ""], ["Geometry.gob", ""], ["Geometry.gob", ""]]', /already a file named 'Geometry.gob'/],
  ['[["Dir/main.gob", ""]]', /the entry is main.gob, not 'Dir\/main.gob'/],
  ['[["Main.gob", ""]]', /the entry is main.gob/],
  ['[["Geometry.gob", ""], ["main.gob", ""]]', /the entry is main.gob/],
  ['[["main.gob", ""], ["Main.gob", ""], ["main.gob", ""]]', /not a module's file name/],
  [JSON.stringify([["main.gob", ""], ["X".repeat(125) + ".gob", ""]]), /'X{40}\.\.\.' is longer than 128/],
  // A link of many files is refused before an editor is made for any.
  [JSON.stringify([["main.gob", ""], ...Array.from({ length: 64 }, (_, i) => [`M${i}.gob`, ""])]),
   /at most 64 files/],
  [JSON.stringify([["main.gob", ""], ...Array.from({ length: 50_000 }, (_, i) => [`M${i}.gob`, ""])]),
   /at most 64 files/],
]) {
  await assert.rejects(filesFromFragment(await filesLink(value)), why, value);
}

// A module's file is named by its path, each part a module name's.
for (const name of ["Geometry.gob", "Shapes/Circle.gob", "A_1'.gob", "Data/Int.gob"]) {
  assert.equal(nameProblem(name), null, name);
}
for (const name of ["geometry.gob", "Geometry", "Geometry.go", "/Geometry.gob", "Shapes//Circle.gob",
                    "Shapes/circle.gob", "Shapes/../Circle.gob", "Geo metry.gob", ".gob", ""]) {
  assert.match(nameProblem(name), /not a module's file name/, name);
}
assert.match(nameProblem("Geometry.gob", ["main.gob", "Geometry.gob"]), /already a file/);
assert.equal(nameProblem("main.gob", [], { entry: true }), null);
assert.match(nameProblem("main.gob"), /not a module's file name/);
assert.equal(nameProblem("X".repeat(124) + ".gob"), null);
// A link of the most files a program has opens.
const most = [["main.gob", ""], ...Array.from({ length: 63 }, (_, i) => [`M${i}.gob`, ""])];
assert.deepEqual(await filesFromFragment(await fragmentFor(most)), most);

// A link restores the exact text: tabs, CRLF, an empty text, characters
// outside the BMP, and text that is HTML.
const texts = [
  "",
  'fun main() {\n\tprint("Hello, Turkey!")\n}\n',
  "line\r\nline\r\n\r\n",
  "let s = \"<b>&amp;\" -- 'quoted' \u{1F983} café\n",
  "x".repeat(100_000),
];
for (const text of texts) {
  const fragment = await fragmentFor(text);
  assert.match(fragment, /^#code=[A-Za-z0-9_-]*$/);
  assert.equal(await sourceFromFragment(fragment), text);
  // The `#` is optional, and other keys beside `code` are ignored.
  assert.equal(await sourceFromFragment(fragment.slice(1)), text);
  assert.equal(await sourceFromFragment("#example=hello&" + fragment.slice(1)), text);
}
// Compression keeps a repetitive text's link short.
assert.ok((await fragmentFor("x".repeat(100_000))).length < 1000);

// A fragment without `code` holds no source.
for (const fragment of ["", "#", "#example=hello"]) {
  assert.equal(await sourceFromFragment(fragment), null);
}

// A damaged link is refused with a reason, never read as some other text.
await assert.rejects(sourceFromFragment("#code=not+base64"), /not base64url/);
await assert.rejects(sourceFromFragment("#code=A"), /not base64url/);
await assert.rejects(sourceFromFragment("#code=" + toBase64url(new Uint8Array([0xff, 0xff]))), /does not decompress/);
const truncated = (await fragmentFor("fun main() { print(1) }\n".repeat(50))).slice(0, 30);
await assert.rejects(sourceFromFragment(truncated), /does not decompress/);

// Bytes that are not UTF-8 are refused.
const latin1 = new Blob([new Uint8Array([0x63, 0x61, 0x66, 0xe9])]).stream();
const deflated = new Uint8Array(
  await new Response(latin1.pipeThrough(new CompressionStream("deflate-raw"))).arrayBuffer(),
);
await assert.rejects(sourceFromFragment("#code=" + toBase64url(deflated)), /not UTF-8/);

// A link that decompresses past the limit is refused, and so is a source past it.
const zeros = new Blob([new Uint8Array(MAX_SOURCE_BYTES + 1)]).stream();
const bomb = new Uint8Array(
  await new Response(zeros.pipeThrough(new CompressionStream("deflate-raw"))).arrayBuffer(),
);
await assert.rejects(sourceFromFragment("#code=" + toBase64url(bomb)), /over 1024 KiB/);
await assert.rejects(fragmentFor("x".repeat(MAX_SOURCE_BYTES + 1)), /too long for a link/);
assert.ok(await fragmentFor("x".repeat(MAX_SOURCE_BYTES)));

// base64url round-trips every byte, with or without padding.
const every = Uint8Array.from({ length: 256 }, (_, i) => i);
for (let n = 0; n <= 256; n += 37) {
  const bytes = every.subarray(0, n);
  const text = toBase64url(bytes);
  assert.doesNotMatch(text, /[+/=]/);
  assert.deepEqual(fromBase64url(text), bytes);
  assert.deepEqual(fromBase64url(text + "=".repeat((4 - (text.length % 4)) % 4)), bytes);
}

// `changedRange` finds the stretch that changed, and applying it gives the
// new text.
const apply = (old, next) => {
  const { start, oldEnd, newEnd } = changedRange(old, next);
  assert.ok(start <= oldEnd && start <= newEnd);
  return old.slice(0, start) + next.slice(start, newEnd) + old.slice(oldEnd);
};
const cases = [
  ["", ""],
  ["", "abc"],
  ["abc", ""],
  ["abc", "abc"],
  ["fun main", "fun xmain"],
  ["aaaa", "aaa"],
  ["aaa", "aaaa"],
  ["abcabc", "abc"],
  ["x\u{1F600}y", "x\u{1F601}y"],
  ["\u{1F600}", "\u{1F600}\u{1F600}"],
  ["a\u{1F600}", "b\u{1F600}"],
];
for (const [old, next] of cases) {
  assert.equal(apply(old, next), next, JSON.stringify([old, next]));
}
assert.deepEqual(changedRange("fun main", "fun xmain"), { start: 4, oldEnd: 4, newEnd: 5 });
// The stretch never splits a surrogate pair, so neither half is left alone in
// a text node.
const { start, oldEnd, newEnd } = changedRange("x\u{1F600}y", "x\u{1F601}y");
assert.deepEqual({ start, oldEnd, newEnd }, { start: 1, oldEnd: 3, newEnd: 3 });

console.log("ok");
