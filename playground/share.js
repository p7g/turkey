// Share links. The source is compressed with deflate-raw, written in
// base64url, and put in the URL's fragment as `#code=...`. Browsers never
// send the fragment to a server, so a link stores nothing anywhere and the
// source never leaves the machine until someone pastes the link.
//
// No DOM, so node loads it.

/** The most bytes of UTF-8 a link holds, either way. */
export const MAX_SOURCE_BYTES = 1 << 20;

/**
 * Returns the fragment, starting with `#`, that holds `text`. Throws if `text`
 * is longer than `MAX_SOURCE_BYTES` in UTF-8.
 */
export async function fragmentFor(text) {
  const bytes = new TextEncoder().encode(text);
  if (bytes.length > MAX_SOURCE_BYTES) {
    throw new Error(`the source is over ${MAX_SOURCE_BYTES >> 10} KiB, too long for a link`);
  }
  return "#code=" + toBase64url(await pipe(bytes, new CompressionStream("deflate-raw")));
}

/**
 * Returns the source a fragment holds, or null for a fragment that holds
 * none, such as "" or "#". Throws if the fragment's `code` is not base64url,
 * does not decompress, decompresses to more than `MAX_SOURCE_BYTES`, or is not
 * UTF-8.
 */
export async function sourceFromFragment(fragment) {
  const encoded = new URLSearchParams(fragment.replace(/^#/, "")).get("code");
  if (encoded === null) {
    return null;
  }
  const bytes = await pipe(fromBase64url(encoded), new DecompressionStream("deflate-raw"), MAX_SOURCE_BYTES);
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new Error("the link's source is not UTF-8");
  }
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
