// A compile-and-run session for one editor on a page: the editor's
// highlighting, Run and Stop, Format, and the output pane. Compiling happens
// in one Worker, highlighting and formatting in a second, and running in a
// third, so the page itself never waits. The playground and each lesson of
// the tour start one.
//
// The Workers are found beside this module, and each fetches `turkey.wasm`
// relative to itself, so a page in another directory starts the same ones.

import { diagnosticsFor } from "./diagnostics.js";
import { attachEditor, replaceText } from "./editor.js";

/**
 * Starts a session over `elements`, an object holding the page's
 * `source` textarea, the `highlighted` code element beneath it, the `run`,
 * `stop` and `format` buttons, the `output` pane, the `formatError`
 * element beside the editor, and the `state` and `note` status elements.
 *
 * Without `lazy`, the compiler loads at once and Run waits for it. With
 * `lazy`, nothing loads until `load()` is called, or the source is edited,
 * or Run is pressed, which then runs the program once the compiler is ready.
 * `timeLimit()` returns how many seconds a run may take.
 *
 * Returns `{ editor, load, show }`: the editor `attachEditor` returned,
 * `load()`, and `show(parts)`, which fills the output pane with `[kind,
 * text]` parts, each a `<pre>` of that class, leaving out empty ones.
 */
export function startSession(elements, { lazy = false, timeLimit = () => 10 } = {}) {
  const { source, highlighted, output, formatError, state, note } = elements;
  const runButton = elements.run;
  const stopButton = elements.stop;
  const formatButton = elements.format;

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
  const editor = attachEditor(source, highlighted, (text) => {
    pendingHighlight = text;
    flushHighlight();
  });

  let compiler = null; // the compile Worker
  let runner = null; // the run Worker, idle or running
  let phase = "unloaded"; // unloaded | loading | idle | compiling | running
  let runWhenReady = false; // a Run pressed before the compiler was ready
  let nextId = 0;
  let currentId = 0;
  let timer = null;

  function setPhase(next, text) {
    phase = next;
    state.textContent = text;
    runButton.disabled = phase !== "idle" && phase !== "unloaded";
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
    const worker = new Worker(new URL("compile-worker.js", import.meta.url), { type: "module" });
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
          if (runWhenReady) {
            runWhenReady = false;
            startRun();
          } else if (document.activeElement === document.body) {
            // Stop during a compile leaves focus on nothing; give it back to Run.
            runButton.focus();
          }
        }
      } else if (data.type === "failed") {
        runWhenReady = false;
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
        runWhenReady = false;
        setPhase("loading", "The compiler failed to load.");
        show([["error", text]]);
      }
    };
  }

  // Handlers check that their Worker is still the current one, so a message
  // from a Worker we have stopped or replaced changes nothing.
  function startRunner() {
    const worker = new Worker(new URL("run-worker.js", import.meta.url), { type: "module" });
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
    const asked = timeLimit();
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

  function startRun() {
    currentId = ++nextId;
    show([]);
    setPhase("compiling", "Compiling...");
    compiler.postMessage({ type: "compile", id: currentId, source: source.value });
  }

  runButton.addEventListener("click", () => {
    if (phase === "unloaded") {
      runWhenReady = true;
      load();
    } else if (phase === "idle") {
      startRun();
    }
  });
  stopButton.addEventListener("click", stop);

  // -- Highlighting and Format -------------------------------------------------

  // A tools Worker that fails leaves the editor working, unhighlighted, and
  // Format disabled with the reason beside it. It is not restarted: it runs
  // nothing of the user's that could crash it, so it would fail again.
  function startTools() {
    const worker = new Worker(new URL("compile-worker.js", import.meta.url), { type: "module", name: "tools" });
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

  source.addEventListener("input", () => {
    formatError.hidden = true;
    load();
  });

  // Starts the three Workers, once.
  function load() {
    if (phase !== "unloaded") {
      return;
    }
    setPhase("loading", "Loading the compiler...");
    startCompiler();
    startTools();
    startRunner();
  }

  formatButton.disabled = true;
  if (lazy) {
    setPhase("unloaded", "");
  } else {
    load();
  }
  return { editor, load, show };
}
