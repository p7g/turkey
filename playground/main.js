// The page: a source box, Run and Stop, and an output pane. Compiling happens
// in one Worker and running in another, so the page itself never waits.

import { diagnosticsFor } from "./diagnostics.js";

const $ = (id) => document.getElementById(id);
const source = $("source");
const runButton = $("run");
const stopButton = $("stop");
const output = $("output");
const state = $("state");
const timeoutInput = $("timeout");

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
  // A button that disables itself drops keyboard focus; hand it to the other.
  const active = document.activeElement;
  if (active === runButton && runButton.disabled && !stopButton.disabled) {
    stopButton.focus();
  } else if (active === stopButton && stopButton.disabled && !runButton.disabled) {
    runButton.focus();
  }
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

function startCompiler() {
  const worker = new Worker("compile-worker.js", { type: "module" });
  compiler = worker;
  worker.onmessage = ({ data }) => {
    if (worker !== compiler) {
      return;
    }
    if (data.type === "status") {
      state.textContent = data.text;
    } else if (data.type === "ready") {
      if (phase === "loading") {
        setPhase("idle", "Ready.");
        // Stop during a compile leaves focus on nothing; give it back to Run.
        if (document.activeElement === document.body) {
          runButton.focus();
        }
      }
    } else if (data.type === "failed") {
      setPhase("loading", "The compiler failed to load.");
      show([["error", data.text]]);
    } else if (data.type === "compiled" && data.id === currentId && phase === "compiling") {
      compiled(data);
    }
  };
  worker.onerror = (event) => {
    if (worker !== compiler) {
      return;
    }
    const text = event.message || "the compile Worker failed";
    worker.terminate();
    if (phase === "compiling") {
      // A crash in the middle of a compile: start over with a fresh compiler.
      startCompiler();
      setPhase("loading", "The compiler crashed. Reloading it...");
      show([["error", `The compiler crashed: ${text}`]]);
    } else {
      setPhase("loading", "The compiler failed to load.");
      show([["error", text]]);
    }
  };
}

// Handlers check that their Worker is still the current one, so a message
// from a Worker we have stopped or replaced changes nothing.
function startRunner() {
  const worker = new Worker("run-worker.js", { type: "module" });
  runner = worker;
  worker.onmessage = ({ data }) => {
    if (worker !== runner || phase !== "running") {
      return;
    }
    const trapped = data.trap !== null;
    finishRun(
      [
        // The compiler's warnings, with the refused-functions one only when
        // the program trapped.
        ["warning", diagnosticsFor(pendingDiagnostics, trapped)],
        ["stdout", data.stdout],
        ["stderr", data.stderr],
        ["error", trapped ? `trap: ${data.trap}` : ""],
        ["status", trapped ? "no exit status (trapped)" : `exit status ${data.status}`],
      ],
      "Done.",
    );
  };
  worker.onerror = (event) => {
    if (worker !== runner) {
      return;
    }
    // A Worker that fails to load would fail again, so we do not replace it
    // here: the next Run starts a fresh one, once.
    clearTimeout(timer);
    worker.terminate();
    runner = null;
    show([["error", event.message || "the run Worker failed"]]);
    setPhase("idle", "Run failed.");
  };
}

let pendingDiagnostics = ""; // the compile's diagnostics, for the run that follows

// Ends the run in progress. A finished Worker is dropped for a fresh one, so
// no run sees another's leftovers.
function finishRun(parts, text) {
  clearTimeout(timer);
  runner.terminate();
  startRunner();
  show(parts);
  setPhase("idle", text);
}

function compiled(data) {
  const diagnostics = diagnosticsFor(data.diagnostics, false);
  if (data.bytes === null) {
    const status = data.status === null ? "the compiler trapped" : `compiler exit status ${data.status}`;
    show([
      ["error", diagnostics],
      ["status", status],
    ]);
    setPhase("idle", "Compile failed.");
    return;
  }
  const asked = timeoutInput.value.trim() === "" ? NaN : Number(timeoutInput.value);
  const limit = Number.isNaN(asked) ? 10 : Math.min(600, Math.max(1, asked));
  pendingDiagnostics = data.diagnostics;
  if (runner === null) {
    startRunner();
  }
  setPhase("running", "Running...");
  timer = setTimeout(
    () => finishRun([["error", `Timed out after ${limit} s; the program was stopped.`]], "Timed out."),
    limit * 1000,
  );
  runner.postMessage({ type: "run", bytes: data.bytes });
}

// Stops what is in flight. A running program is stopped by terminating its
// Worker, which needs no cooperation from the program. A compile in flight is
// stopped the same way, which also drops the loaded compiler; the next
// compile reloads it from the HTTP cache.
function stop() {
  clearTimeout(timer);
  // Whatever the stopped Worker still sends is for an id nobody waits on.
  currentId = ++nextId;
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
