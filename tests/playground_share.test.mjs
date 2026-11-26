// Checks `playground/share.js` and `changedRange` in `playground/editor.js`,
// run by `tests/test_playground_host.py`:
//
//     node tests/playground_share.test.mjs

import assert from "node:assert/strict";
import {
  MAX_SOURCE_BYTES,
  fragmentFor,
  fromBase64url,
  sourceFromFragment,
  toBase64url,
} from "../playground/share.js";
import { changedRange } from "../playground/editor.js";

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
