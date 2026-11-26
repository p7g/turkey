// The run Worker: runs one program and is then discarded. The page makes a
// fresh one after every run, so `Worker.terminate()` is all Stop needs, and a
// program that loops or leaks takes nothing else down.
//
// Message in:  { type: "run", bytes }
// Messages out: { type: "write", chunks: [[stream, text], ...] } while the
//   program runs, then { type: "ran", status, stdout, stderr, trap }. The
//   chunks of a run, in order, are its stdout and stderr interleaved as they
//   were written; `ran` repeats each stream whole.

import { run } from "./host.js";

// A batch is posted once it holds this many characters, or once this many
// milliseconds have passed since the last post, checked at each write.
const BATCH_CHARS = 4096;
const BATCH_MS = 16;

onmessage = async ({ data }) => {
  if (data.type !== "run") {
    return;
  }
  const decoders = { stdout: new TextDecoder(), stderr: new TextDecoder() };
  let chunks = []; // [stream, text] pairs not yet posted
  let size = 0;
  let last = performance.now();
  let tail = null; // posts what a program that writes and then waits leaves behind
  function flush() {
    clearTimeout(tail);
    tail = null;
    last = performance.now();
    if (chunks.length > 0) {
      postMessage({ type: "write", chunks });
      chunks = [];
      size = 0;
    }
  }
  // A program that computes without waiting never returns to the event loop,
  // so no timer fires while it runs: the clock check at each write is what
  // posts its output. The timer covers the program that waits.
  function onWrite(stream, bytes) {
    const text = decoders[stream].decode(bytes, { stream: true });
    if (text === "") {
      return;
    }
    const end = chunks[chunks.length - 1];
    if (end !== undefined && end[0] === stream) {
      end[1] += text;
    } else {
      chunks.push([stream, text]);
    }
    size += text.length;
    if (size >= BATCH_CHARS || performance.now() - last >= BATCH_MS) {
      flush();
    } else if (tail === null) {
      tail = setTimeout(flush, BATCH_MS);
    }
  }
  try {
    const { status, stdout, stderr, trap } = await run(data.bytes, { onWrite });
    for (const stream of ["stdout", "stderr"]) {
      const text = decoders[stream].decode();
      if (text !== "") {
        chunks.push([stream, text]);
      }
    }
    flush();
    postMessage({ type: "ran", status, stdout, stderr, trap });
  } catch (error) {
    flush();
    postMessage({ type: "ran", status: null, stdout: "", stderr: "", trap: String(error) });
  }
};
