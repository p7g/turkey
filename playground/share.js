// Share links. A link holds the program in the URL's fragment, compressed
// with deflate-raw and written in base64url:
//
//   #code=...   one file, `main.gob`: its text, as UTF-8
//   #files=...  any number: a JSON array of [name, text] pairs, the entry
//               first, as UTF-8
//
// A program that is only `main.gob` gets a `#code=` link, the one it would
// get from a page that knows only `#code=`, so either page opens it.
// Browsers never send the fragment to a server, so a link stores nothing
// anywhere and the source never leaves the machine until someone pastes the
// link.
//
// No DOM, so node loads it.

import { filesProblem } from "./files.js";

/** The most bytes of UTF-8 a link holds, either way. */
export const MAX_SOURCE_BYTES = 1 << 20;

/**
 * Returns the fragment, starting with `#`, that holds `files`, `[name,
 * text]` pairs with the entry first, or a string, which is one file,
 * `main.gob`. Throws if what the link holds is longer than
 * `MAX_SOURCE_BYTES` in UTF-8.
 */
export async function fragmentFor(files) {
  const pairs = typeof files === "string" ? [["main.gob", files]] : files;
  const single = pairs.length === 1 && pairs[0][0] === "main.gob";
  const bytes = new TextEncoder().encode(single ? pairs[0][1] : JSON.stringify(pairs));
  if (bytes.length > MAX_SOURCE_BYTES) {
    throw new Error(`the source is over ${MAX_SOURCE_BYTES >> 10} KiB, too long for a link`);
  }
  const encoded = toBase64url(await pipe(bytes, new CompressionStream("deflate-raw")));
  return (single ? "#code=" : "#files=") + encoded;
}

/**
 * Returns the files a fragment holds, as `[name, text]` pairs with the entry
 * first, or null for a fragment that holds none, such as "" or "#". A
 * fragment with both keys is read by its `files`. Throws if the value is not
 * base64url, does not decompress, decompresses to more than
 * `MAX_SOURCE_BYTES`, or is not UTF-8, and if a `files` value is not an array
 * of files `filesProblem` accepts.
 */
export async function filesFromFragment(fragment) {
  const params = new URLSearchParams(fragment.replace(/^#/, ""));
  const key = params.has("files") ? "files" : params.has("code") ? "code" : null;
  if (key === null) {
    return null;
  }
  const bytes = await pipe(fromBase64url(params.get(key)), new DecompressionStream("deflate-raw"), MAX_SOURCE_BYTES);
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new Error("the link's source is not UTF-8");
  }
  if (key === "code") {
    return [["main.gob", text]];
  }
  let pairs;
  try {
    pairs = JSON.parse(text);
  } catch {
    throw new Error("the link's files are damaged: they are not JSON");
  }
  const problem = filesProblem(pairs);
  if (problem !== null) {
    throw new Error(`the link's files are damaged: ${problem}`);
  }
  return pairs;
}

// Returns `bytes` passed through `transform`. Throws once the output passes
// `limit` bytes, before reading the rest, so a small link cannot make the
// page decompress gigabytes.
async function pipe(bytes, transform, limit = Infinity) {
  const reader = new Blob([bytes]).stream().pipeThrough(transform).getReader();
  const chunks = [];
  let length = 0;
  for (;;) {
    let step;
    try {
      step = await reader.read();
    } catch {
      throw new Error("the link's source is damaged: it does not decompress");
    }
    if (step.done) {
      break;
    }
    length += step.value.length;
    if (length > limit) {
      await reader.cancel();
      throw new Error(`the link's source is over ${limit >> 10} KiB`);
    }
    chunks.push(step.value);
  }
  const out = new Uint8Array(length);
  let at = 0;
  for (const chunk of chunks) {
    out.set(chunk, at);
    at += chunk.length;
  }
  return out;
}

/** Returns `bytes` in base64url (RFC 4648, section 5), without padding. */
export function toBase64url(bytes) {
  // Build the binary string in slices, since spreading a large array into
  // `String.fromCharCode` overflows the call stack.
  let binary = "";
  for (let at = 0; at < bytes.length; at += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * Returns the bytes `text` holds in base64url, with or without padding.
 * Throws for any other text.
 */
export function fromBase64url(text) {
  const bare = text.replace(/=+$/, "");
  if (!/^[A-Za-z0-9_-]*$/.test(bare) || bare.length % 4 === 1) {
    throw new Error("the link's source is damaged: it is not base64url");
  }
  const binary = atob(bare.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}
