// The page: a source box, Run and Stop, and an output pane. Compiling happens
// in one Worker and running in another, so the page itself never waits.

const $ = (id) => document.getElementById(id);
const source = $("source");
const runButton = $("run");
const stopButton = $("stop");
const output = $("output");
const state = $("state");
const timeoutInput = $("timeout");

// The compiler warns about two of its own functions on every compile. The
// warning says nothing about the user's program, so we drop its line.
const NOISE = "functions use operations the wasm32 backend does not compile yet";

function withoutNoise(diagnostics) {
  return diagnostics
    .split("\n")
    .filter((line) => !line.includes(NOISE))
    .join("\n")
    .trim();
}

const HELLO = `fun main() {
  print("Hello, Turkey!")
}
`;

try {
  source.value = localStorage.getItem("turkey-playground-source") ?? HELLO;
} catch {
  source.value = HELLO;
}
source.addEventListener("input", () => {
  try {
    localStorage.setItem("turkey-playground-source", source.value);
  } catch {}
});

let compiler = null; // the compile Worker
let runner = null; // the run Worker, idle or running
let phase = "loading"; // loading | idle | compiling | running
let nextId = 0;
let currentId = 0;
let timer = null;

function setPhase(next, text) {
  phase = next;
  state.textContent = text;
  runButton.disabled = phase !== "idle";
  stopButton.disabled = phase !== "compiling" && phase !== "running";
}

// Each part of the output is its own element, so stdout, stderr and the
// status are told apart by style and not only by their text.
function show(parts) {
  output.replaceChildren(
    ...parts
      .filter(([, text]) => text !== "")
      .map(([kind, text]) => {
        const element = document.createElement("pre");
        element.className = kind;
        element.textContent = text;
        return element;
      }),
  );
}

function loadFailed(text) {
  setPhase("loading", "The compiler failed to load.");
  show([["error", text]]);
}

function startCompiler() {
  compiler = new Worker("compile-worker.js", { type: "module" });
  compiler.onmessage = ({ data }) => {
    if (data.type === "status") {
      state.textContent = data.text;
    } else if (data.type === "ready") {
      if (phase === "loading") {
        setPhase("idle", "Ready.");
      }
    } else if (data.type === "failed") {
      loadFailed(data.text);
    } else if (data.type === "compiled" && data.id === currentId) {
      compiled(data);
    }
  };
  compiler.onerror = (event) => loadFailed(event.message || "the compile Worker failed");
}

function startRunner() {
  runner = new Worker("run-worker.js", { type: "module" });
}

function compiled(data) {
  const diagnostics = withoutNoise(data.diagnostics);
  if (data.bytes === null) {
    const status = data.status === null ? "the compiler trapped" : `compiler exit status ${data.status}`;
    show([
      ["error", diagnostics],
      ["status", status],
    ]);
    setPhase("idle", "Compile failed.");
    return;
  }
  const seconds = Math.max(1, Number(timeoutInput.value) || 10);
  setPhase("running", "Running...");
  const worker = runner;
  const finish = (parts, text) => {
    clearTimeout(timer);
    // A finished Worker is dropped for a fresh one, so no run sees another's
    // leftovers.
    worker.terminate();
    startRunner();
    show(parts);
    setPhase("idle", text);
  };
  worker.onmessage = ({ data: result }) => {
    const trapped = result.trap !== null;
    finish(
      [
        // Warnings about the user's own program, if the compiler gave any.
        ["warning", diagnostics],
        ["stdout", result.stdout],
        ["stderr", result.stderr],
        ["error", trapped ? `trap: ${result.trap}` : ""],
        ["status", trapped ? "no exit status (trapped)" : `exit status ${result.status}`],
      ],
      "Done.",
    );
  };
  worker.onerror = (event) => finish([["error", event.message || "the run Worker failed"]], "Run failed.");
  timer = setTimeout(
    () => finish([["error", `Timed out after ${seconds} s; the program was stopped.`]], "Timed out."),
    seconds * 1000,
  );
  worker.postMessage({ type: "run", bytes: data.bytes });
}

// Stops what is in flight. A running program is stopped by terminating its
// Worker, which needs no cooperation from the program. A compile in flight is
// stopped the same way, which also drops the loaded compiler; the next
// compile reloads it from the HTTP cache.
function stop() {
  clearTimeout(timer);
  if (phase === "running") {
    runner.terminate();
    startRunner();
    setPhase("idle", "Stopped.");
  } else if (phase === "compiling") {
    compiler.terminate();
    startCompiler();
    setPhase("loading", "Stopped. Reloading the compiler...");
  } else {
    return;
  }
  show([["status", "Stopped."]]);
}

runButton.addEventListener("click", () => {
  if (phase !== "idle") {
    return;
  }
  currentId = ++nextId;
  show([]);
  setPhase("compiling", "Compiling...");
  compiler.postMessage({ type: "compile", id: currentId, source: source.value });
});
stopButton.addEventListener("click", stop);

setPhase("loading", "Loading the compiler...");
startCompiler();
startRunner();
