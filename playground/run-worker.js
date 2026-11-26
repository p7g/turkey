// The run Worker: runs one program and is then discarded. The page makes a
// fresh one after every run, so `Worker.terminate()` is all Stop needs, and a
// program that loops or leaks takes nothing else down.
//
// Message in:  { type: "run", bytes }
// Message out: { type: "ran", status, stdout, stderr, trap }

import { run } from "./host.js";

onmessage = async ({ data }) => {
  if (data.type !== "run") {
    return;
  }
  try {
    const { status, stdout, stderr, trap } = await run(data.bytes);
    postMessage({ type: "ran", status, stdout, stderr, trap });
  } catch (error) {
    postMessage({ type: "ran", status: null, stdout: "", stderr: "", trap: String(error) });
  }
};
