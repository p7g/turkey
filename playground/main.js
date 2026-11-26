// The page: a highlighted source editor, Run and Stop, Format and Share, and
// an output pane. Compiling happens in one Worker, highlighting and
// formatting in a second, and running in a third, so the page itself never
// waits.

import { diagnosticsFor } from "./diagnostics.js";
import { attachEditor, replaceText } from "./editor.js";
import { fragmentFor, sourceFromFragment } from "./share.js";

const $ = (id) => document.getElementById(id);
const source = $("source");
const runButton = $("run");
const stopButton = $("stop");
const formatButton = $("format");
const shareButton = $("share");
const output = $("output");
const formatError = $("format-error");
const state = $("state");
const timeoutInput = $("timeout");

const HELLO = `fun main() {
\tprint("Hello, Turkey!")
}
`;

// Past this length a link still works in the browser that made it, but chat
// apps, mail clients and URL shorteners may cut it short, so we say so.
const LONG_LINK = 8000;

try {
  source.value = localStorage.getItem("turkey-playground-source") ?? HELLO;
} catch {
  source.value = HELLO;
}

// Whether the address bar's fragment holds the editor's text: true after a
// link loads or Share makes one, false from the next edit. The first edit
// drops the fragment, so a reload brings back the edits, which `localStorage`
// keeps, and not the text the link held.
let linked = false;
source.addEventListener("input", () => {
  try {
    localStorage.setItem("turkey-playground-source", source.value);
  } catch {}
  if (linked) {
    linked = false;
    history.replaceState(null, "", location.pathname + location.search);
  }
  formatError.hidden = true;
});

// `turkey.wasm`, compiled by the first compile Worker and handed to every
// Worker started after it, or null until then.
let turkeyModule = null;
let tools = null; // the Worker that highlights and formats
let toolsReady = false;
let formatting = null; // the id of the Format in flight, or null

// At most one highlight is in flight, and only the latest text waits behind
// it. A highlight that outlasts the editor's debounce would otherwise queue
// the next ones, and a Format behind them.
let highlighting = false;
let pendingHighlight = null;
function flushHighlight() {
  if (pendingHighlight !== null && toolsReady && !highlighting) {
    tools.postMessage({ type: "highlight", id: 0, source: pendingHighlight });
    pendingHighlight = null;
    highlighting = true;
  }
}
const editor = attachEditor(source, $("highlighted"), (text) => {
  pendingHighlight = text;
  flushHighlight();
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
  worker.postMessage({ type: "load", module: turkeyModule, lib: true });
  worker.onmessage = ({ data }) => {
    if (worker !== compiler) {
      return;
    }
    if (data.type === "status") {
      state.textContent = data.text;
    } else if (data.type === "ready") {
      if (turkeyModule === null) {
        turkeyModule = data.module;
        tools?.postMessage({ type: "load", module: turkeyModule, lib: false });
      }
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

// -- Highlighting and Format ---------------------------------------------------

const note = $("note"); // what Format and Share last did

// A tools Worker that fails leaves the editor working, unhighlighted, and
// Format disabled with the reason beside it. It is not restarted: it runs
// nothing of the user's that could crash it, so it would fail again.
function startTools() {
  const worker = new Worker("compile-worker.js", { type: "module", name: "tools" });
  tools = worker;
  // It loads once the compile Worker has compiled `turkey.wasm`.
  if (turkeyModule !== null) {
    worker.postMessage({ type: "load", module: turkeyModule, lib: false });
  }
  const failed = (text) => {
    worker.terminate();
    tools = null;
    toolsReady = false;
    highlighting = false;
    formatting = null;
    formatButton.disabled = true;
    note.textContent = `Highlighting and Format are unavailable: ${text}`;
  };
  worker.onmessage = ({ data }) => {
    if (data.type === "ready") {
      toolsReady = true;
      formatButton.disabled = false;
      flushHighlight();
    } else if (data.type === "failed") {
      failed(data.text);
    } else if (data.type === "highlighted") {
      highlighting = false;
      editor.highlighted(data.source, data.html);
      flushHighlight();
    } else if (data.type === "formatted" && data.id === formatting) {
      formatted(data);
    }
  };
  worker.onerror = (event) => failed(event.message || "the Worker failed");
}

function formatted(data) {
  formatting = null;
  formatButton.disabled = false;
  if (data.text === null) {
    // The error goes beside the editor, not in the output pane, which still
    // holds the last run's output.
    const status = data.status === null ? "the formatter trapped" : `format exit status ${data.status}`;
    formatError.textContent = `${data.diagnostics.trim()}\n${status}`;
    formatError.hidden = false;
    note.textContent = "Format failed; the source is unchanged.";
  } else if (source.value !== data.source) {
    // Replacing the source now would throw away what was typed since Format
    // was pressed.
    note.textContent = "The source changed while it was formatted; press Format again.";
  } else if (data.text === data.source) {
    note.textContent = "Already formatted.";
  } else {
    replaceText(source, data.text);
    note.textContent = "Formatted.";
  }
}

let nextFormat = 0;
formatButton.addEventListener("click", () => {
  if (!toolsReady || formatting !== null) {
    return;
  }
  formatting = ++nextFormat;
  formatButton.disabled = true;
  formatError.hidden = true;
  note.textContent = "Formatting...";
  tools.postMessage({ type: "format", id: formatting, source: source.value });
});

// -- Share links -----------------------------------------------------------------

shareButton.addEventListener("click", async () => {
  const text = source.value;
  let fragment;
  try {
    fragment = await fragmentFor(text);
  } catch (error) {
    note.textContent = `No link: ${error.message}.`;
    return;
  }
  // A link for text since edited would hold the wrong source.
  if (source.value !== text) {
    note.textContent = "The source changed while the link was made; press Share again.";
    return;
  }
  history.replaceState(null, "", fragment);
  linked = true;
  const url = location.href;
  let copied = false;
  try {
    await navigator.clipboard.writeText(url);
    copied = true;
  } catch {}
  const said = copied ? "Link copied." : "The link is in the address bar.";
  note.textContent =
    url.length > LONG_LINK
      ? `${said} It is ${url.length.toLocaleString("en")} characters long, and some apps cut links that long short.`
      : said;
});

// Shows the source the fragment holds, if it holds one. A fragment that
// cannot be read leaves the editor as it was and says why.
async function loadLink() {
  let text;
  try {
    text = await sourceFromFragment(location.hash);
  } catch (error) {
    note.textContent = `This link cannot be opened: ${error.message}.`;
    return;
  }
  if (text !== null) {
    source.value = text;
    editor.changed();
    linked = true;
  }
}
window.addEventListener("hashchange", loadLink);

// -- Examples ------------------------------------------------------------------

// The picker lists `examples/index.json`, which the deploy writes from the
// suite's recorded programs. Choosing one replaces the editor's text through
// the browser's undo stack, so Ctrl+Z brings the previous text back, and so
// no confirmation is asked. A page served without the manifest keeps the
// picker disabled and works as before.
const picker = $("examples");
let picking = 0; // the pick in flight, so a slow fetch never overwrites a later one

async function loadExamples() {
  try {
    const response = await fetch("examples/index.json");
    if (!response.ok) {
      return;
    }
    const entries = await response.json();
    picker.append(
      ...entries.map(({ name, title }) => {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = title;
        return option;
      }),
    );
    picker.disabled = false;
    picker.dataset.files = JSON.stringify(Object.fromEntries(entries.map((e) => [e.name, e.file])));
  } catch {}
}

picker.addEventListener("change", async () => {
  const name = picker.value;
  if (name === "") {
    return;
  }
  const mine = ++picking;
  const entry = JSON.parse(picker.dataset.files)[name];
  let text;
  try {
    const response = await fetch(entry);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    text = await response.text();
  } catch (error) {
    note.textContent = `The example could not be loaded: ${error.message}.`;
    return;
  }
  if (mine !== picking) {
    return;
  }
  replaceText(source, text);
  source.setSelectionRange(0, 0);
  source.scrollTop = 0;
  show([]);
  formatError.hidden = true;
  note.textContent = `Loaded "${picker.selectedOptions[0].textContent}". Undo brings back the previous text.`;
  picker.value = "";
});

setPhase("loading", "Loading the compiler...");
formatButton.disabled = true;
startCompiler();
startTools();
startRunner();
loadLink();
loadExamples();
