// A compile-and-run session for one program on a page: its files' editors,
// their highlighting and diagnostic marks, Run and Stop, Format, their
// keyboard shortcuts, and the output pane with a tab beside it for the
// program's types. Compiling happens in one Worker, checking, highlighting
// and formatting in a second, and running in a third, so the page itself
// never waits. The playground and each lesson of the tour start one.
//
// The Workers are found beside this module, and each fetches `turkey.wasm`
// relative to itself, so a page in another directory starts the same ones.

import { diagnosticsFor, marksFor, offsetsIn } from "./diagnostics.js";
import { replaceText } from "./editor.js";
import { attachFiles } from "./files.js";
import { attachPanes } from "./panes.js";
import { shortcutFor, shortcutText } from "./shortcuts.js";
import { nameRange, renderTypes, typeMarksFor, typesFor } from "./types.js";

// The most characters the output pane keeps of a run's output. A program that
// prints without end would otherwise grow the page until the tab hangs.
const OUTPUT_CAP = 1 << 20;

/**
 * Starts a session over `elements`, an object holding the page's `files`,
 * the element holding the program's editors as `attachFiles` takes it, the
 * `run` and `format` buttons, the `output` pane, the `formatError` element
 * beside the editor, and the `state` and `note` status elements.
 *
 * The `run` button is also Stop: while a program compiles or runs, its
 * label, icon and accessible name are Stop's, and pressing it stops the
 * program. It keeps one width throughout. Its own content is replaced.
 *
 * Without `lazy`, the compiler loads at once and Run waits for it. With
 * `lazy`, nothing loads until `load()` is called, or the source is edited,
 * or Run is pressed, which then runs the program once the compiler is ready.
 * `timeLimit()` returns how many seconds a run may take.
 *
 * A running program's output appears in the pane as it is written, at most
 * once per animation frame. Stop and the time limit keep what was written.
 * The pane keeps the last `OUTPUT_CAP` characters of a run, with a note when
 * it has dropped earlier ones.
 *
 * With `editable`, the files can be added, renamed and removed, as
 * `attachFiles` says. Run compiles them all, and Format lays out the file
 * shown. Ctrl+Enter (Cmd+Enter on a Mac) presses Run, or Stop while a
 * program runs, and Shift+Alt+F presses Format, from anywhere on the page,
 * as `shortcuts.js` says.
 *
 * The output pane gets tabs (`attachPanes`): Output, and Types, which lists
 * each top-level binding's type as the last check found it, by file, each
 * name a button that selects it in its editor. The editors show a binding's
 * type in a tip when the pointer is over its name. Run and `show` select
 * the Output tab.
 *
 * Returns `{ files, load, show, cancel }`: the file set `attachFiles`
 * returned, `load()`, `show(parts)`, which fills the output pane with `[kind,
 * text]` parts, each a `<pre>` of that class, leaving out empty ones; and
 * `cancel()`, which stops a compile or run in flight, as Stop does, and drops
 * a Run that waits for the compiler.
 */
export function startSession(elements, { lazy = false, editable = false, timeLimit = () => 10 } = {}) {
  const { output, formatError, state, note } = elements;
  const runButton = elements.run;
  const formatButton = elements.format;
  const mac = /mac|iphone|ipad/i.test(navigator.userAgentData?.platform ?? navigator.platform);
  const runKeys = shortcutText("run", mac);

  // The button holds both labels in one grid cell, Stop's hidden while it
  // is Run and Run's while it is Stop, so it is as wide as the wider label
  // in either state and the toolbar does not move when it changes. A hidden
  // label is out of the accessible name too, which `aria-label` also sets.
  // The icons carry U+FE0E so that no platform draws them as emoji.
  const buttonLabel = (className, icon, text) => {
    const element = document.createElement("span");
    element.className = className;
    const glyph = document.createElement("span");
    glyph.className = "icon";
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = `${icon}︎`;
    element.append(glyph, text);
    return element;
  };
  runButton.replaceChildren(buttonLabel("when-run", "▶", "Run"), buttonLabel("when-stop", "■", "Stop"));

  // `turkey.wasm`, compiled by the first compile Worker and handed to every
  // Worker started after it, or null until then.
  let turkeyModule = null;
  let tools = null; // the Worker that highlights and formats
  let toolsReady = false;
  let formatting = null; // the id of the Format in flight, or null
  let formattingFile = null; // the file it lays out

  // A keystroke highlights at once when none is in flight. At most one is in
  // flight, and only each file's latest text waits behind it, so a burst of
  // keystrokes during a highlight costs one more, and a Format never queues
  // behind many. A request's id is its file's, which its reply carries back.
  let highlighting = false;
  const pendingHighlights = new Map(); // file id -> text
  function flushHighlight() {
    if (pendingHighlights.size > 0 && toolsReady && !highlighting) {
      const [[id, text]] = pendingHighlights;
      pendingHighlights.delete(id);
      tools.postMessage({ type: "highlight", id, source: text });
      highlighting = true;
    }
  }

  // The editors' marks come from `turkey check` on all the files, run in the
  // tools Worker once typing has paused for `IDLE_MS`, and at once when a
  // Run fails to compile. A check takes a large part of a second whatever
  // the program's size, since most of it goes on the Prelude, and the Worker
  // highlights nothing while one runs. Waiting for a pause keeps checks from
  // running mid-word and holding up the highlighting of the next
  // keystrokes. One check is in flight at a time, files already checked are
  // not checked again, and a reply for files since edited, added, renamed
  // or removed is dropped. A check that failed leaves the marks as they
  // were. Marking changes no file's text, so it saves nothing. The same
  // check gives the types, so the Types tab and the editors' type tips are
  // as fresh as the marks, and cost nothing more.
  const IDLE_MS = 500;
  let checking = false;
  let checkAgain = false; // a check was asked for while one was in flight
  let checked = null; // the files the marks were made for, as `keyOf` gives them
  let idle = null;
  const keyOf = (pairs) => JSON.stringify(pairs);
  function checkWhenIdle() {
    clearTimeout(idle);
    idle = setTimeout(checkNow, IDLE_MS);
  }
  function checkNow() {
    clearTimeout(idle);
    if (!toolsReady) {
      return;
    }
    const pairs = files.all();
    if (keyOf(pairs) === checked) {
      return;
    }
    if (checking) {
      checkAgain = true;
      return;
    }
    checking = true;
    tools.postMessage({ type: "check", id: 0, files: pairs });
  }
  function checkedFiles(data) {
    checking = false;
    const key = keyOf(data.files);
    if (data.diagnostics !== null && key === keyOf(files.all())) {
      checked = key;
      const view = typesFor(data.status, data.types, files.list().map((f) => f.name));
      showTypes(view);
      for (const file of files.list()) {
        const problems = marksFor(file.textarea.value, data.diagnostics, file.name);
        const bindings = view.files.find((f) => f.file === file.name)?.bindings ?? [];
        file.editor.mark([...problems, ...typeMarksFor(file.textarea.value, bindings)]);
        markTab(file, problems);
      }
    }
    if (checkAgain) {
      checkAgain = false;
      checkNow();
    }
  }
  // Marks a file's tab with the worst of its marks, so that a file not shown
  // still says it has a problem.
  function markTab(file, marks) {
    const errors = marks.filter((m) => m.severity === "error").length;
    const warnings = marks.length - errors;
    if (marks.length === 0) {
      delete file.tab.dataset.problems;
      file.tab.removeAttribute("title");
      return;
    }
    file.tab.dataset.problems = errors > 0 ? "error" : "warning";
    const counted = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;
    file.tab.title = [
      ...(errors > 0 ? [counted(errors, "error")] : []),
      ...(warnings > 0 ? [counted(warnings, "warning")] : []),
    ].join(", ");
  }

  const files = attachFiles(elements.files, {
    editable,
    say: (...parts) => note.replaceChildren(...parts),
    requestHighlight(file, text) {
      pendingHighlights.set(file.id, text);
      flushHighlight();
      checkWhenIdle();
    },
    // Another program's types would name bindings this one may not have, at
    // positions in other text, so they are forgotten, not kept as out of
    // date.
    onReplace() {
      lastTypes = null;
      awaitTypes();
    },
  });

  // -- The Types tab -----------------------------------------------------------

  const panes = attachPanes(output, [["types", "Types"]]);
  const typesPanel = panes.panel("types");
  // The last view that was current, which a check that finds errors shows
  // again, marked as out of date, rather than leaving the tab empty for
  // every typo.
  let lastTypes = null;
  // Says that the types will come, and when.
  function awaitTypes() {
    let message = "The types appear once the program has been checked.";
    if (!toolsReady) {
      message = lazy
        ? "The types appear once the compiler has loaded: edit the program, or press Run."
        : "The types appear once the compiler has loaded.";
    }
    renderTypes(typesPanel, null, { message });
  }
  awaitTypes();
  function showTypes(view) {
    if (view.current) {
      lastTypes = view;
    }
    if (lastTypes === null) {
      renderTypes(typesPanel, null, {
        message: "The program has errors, so it has no types yet. The underlined problems say why.",
      });
      return;
    }
    renderTypes(typesPanel, view.current ? view : { ...lastTypes, current: false }, { onPick: reveal });
  }
  // Shows the file `name` and selects the binding's name in it.
  function reveal(name, binding) {
    const file = files.list().find((f) => f.name === name);
    if (file === undefined) {
      note.textContent = `There is no file named ${name} now.`;
      return;
    }
    files.show(file);
    const text = file.textarea.value;
    const [start, end] = nameRange(text, offsetsIn(text)(binding.line, binding.column), binding.name);
    file.textarea.focus();
    file.textarea.setSelectionRange(start, end);
  }

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
    // The button is Stop while there is something to stop, and Run when
    // there is nothing in the way of a run. While the compiler loads it is
    // neither, and disabled.
    const stopping = phase === "compiling" || phase === "running";
    const name = stopping ? "Stop" : "Run";
    runButton.disabled = !stopping && phase !== "idle" && phase !== "unloaded";
    runButton.dataset.mode = stopping ? "stop" : "run";
    runButton.setAttribute("aria-label", name);
    runButton.title = `${name} (${runKeys.text})`;
  }

  // Each part of the output is its own element, so stdout, stderr and the
  // status are told apart by style and not only by their text.
  function show(parts) {
    panes.select(output.id);
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

  // -- Streamed output ---------------------------------------------------------

  // The pane of the run in flight, or null: its warnings, a note that output
  // was dropped, and one `<pre>` holding the program's output as a text node
  // per stretch of a stream, stderr's in a styled span. Writes wait in
  // `pending` until the next animation frame, and are cut to the cap there
  // too, since a hidden tab runs no frames.
  let live = null;

  function beginStream(diagnostics) {
    const make = (kind) => {
      const element = document.createElement("pre");
      element.className = kind;
      element.hidden = true;
      return element;
    };
    const warning = make("warning");
    warning.textContent = diagnostics;
    warning.hidden = diagnostics === "";
    const trimmed = make("status");
    trimmed.textContent = `Output before the last ${OUTPUT_CAP / 1024} KB is not shown.`;
    live = { warning, trimmed, body: make("stdout"), nodes: [], size: 0, pending: [], pendingSize: 0, dropped: false, frame: 0 };
    output.replaceChildren(warning, trimmed, live.body);
  }

  function streamed(chunks) {
    for (const [stream, text] of chunks) {
      const end = live.pending[live.pending.length - 1];
      if (end !== undefined && end[0] === stream) {
        end[1] += text;
      } else {
        live.pending.push([stream, text]);
      }
      live.pendingSize += text.length;
    }
    // Drop from the front until at most the cap is left.
    while (live.pendingSize > OUTPUT_CAP) {
      const excess = live.pendingSize - OUTPUT_CAP;
      const first = live.pending[0];
      live.dropped = true;
      if (first[1].length <= excess) {
        live.pending.shift();
        live.pendingSize -= first[1].length;
      } else {
        first[1] = first[1].slice(excess);
        live.pendingSize -= excess;
      }
    }
    if (live.frame === 0) {
      const mine = live;
      live.frame = requestAnimationFrame(() => {
        mine.frame = 0;
        if (mine === live) {
          flushStream();
        }
      });
    }
  }

  // Moves what is pending into the pane and trims the pane to the cap.
  function flushStream() {
    const { body, nodes } = live;
    for (const [stream, text] of live.pending) {
      const node = document.createTextNode(text);
      let element = node;
      if (stream === "stderr") {
        element = document.createElement("span");
        element.className = "stderr";
        element.append(node);
      }
      body.append(element);
      nodes.push({ element, node });
      live.size += text.length;
    }
    live.pending = [];
    live.pendingSize = 0;
    while (live.size > OUTPUT_CAP) {
      const excess = live.size - OUTPUT_CAP;
      const first = nodes[0];
      live.dropped = true;
      if (first.node.length <= excess) {
        first.element.remove();
        nodes.shift();
        live.size -= first.node.length;
      } else {
        first.node.deleteData(0, excess);
        live.size -= excess;
      }
    }
    body.hidden = nodes.length === 0;
    live.trimmed.hidden = !live.dropped;
  }

  // Ends the stream, keeping what it shows, and returns its pane for the
  // run's last lines to follow.
  function endStream() {
    const ended = live;
    cancelAnimationFrame(ended.frame);
    flushStream();
    live = null;
    return ended;
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
          tools?.postMessage({ type: "load", module: turkeyModule, lib: true });
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
      if (data.type === "write") {
        streamed(data.chunks);
        return;
      }
      const trapped = data.trap !== null;
      // The compiler's warnings, with the refused-functions one only when the
      // program trapped.
      live.warning.textContent = diagnosticsFor(pendingDiagnostics, trapped);
      live.warning.hidden = live.warning.textContent === "";
      finishRun(
        [
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
      if (live !== null) {
        endStream();
      }
      show([["error", event.message || "the run Worker failed"]]);
      setPhase("idle", "Run failed.");
    };
  }

  let pendingDiagnostics = ""; // the compile's diagnostics, for the run that follows

  // Ends the run in progress, and follows the output it wrote with `parts`. A
  // finished Worker is dropped for a fresh one, so no run sees another's
  // leftovers.
  function finishRun(parts, text) {
    clearTimeout(timer);
    runner.terminate();
    startRunner();
    append(endStream(), parts);
    setPhase("idle", text);
  }

  // Adds the non-empty `[kind, text]` parts after the pane `ended`.
  function append(ended, parts) {
    ended.body.parentNode.append(
      ...parts
        .filter(([, line]) => line !== "")
        .map(([kind, line]) => {
          const element = document.createElement("pre");
          element.className = kind;
          element.textContent = line;
          return element;
        }),
    );
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
      // The marks show where, if the idle check has not already.
      checkNow();
      return;
    }
    const asked = timeLimit();
    const limit = Number.isNaN(asked) ? 10 : Math.min(600, Math.max(1, asked));
    pendingDiagnostics = data.diagnostics;
    if (runner === null) {
      startRunner();
    }
    setPhase("running", "Running...");
    beginStream(diagnosticsFor(pendingDiagnostics, false));
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
      // Keep what the program wrote, and add the status after it.
      append(endStream(), [["status", "Stopped."]]);
      setPhase("idle", "Stopped.");
      return;
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
    compiler.postMessage({ type: "compile", id: currentId, files: files.all() });
  }

  runButton.addEventListener("click", () => {
    if (phase === "unloaded") {
      runWhenReady = true;
      load();
    } else if (phase === "idle") {
      startRun();
    } else if (phase === "compiling" || phase === "running") {
      stop();
    }
  });

  // -- Highlighting, checking and Format ---------------------------------------

  // A tools Worker that fails leaves the editor working, unhighlighted and
  // unmarked, and Format disabled with the reason beside it. It is not restarted: it runs
  // nothing of the user's that could crash it, so it would fail again.
  function startTools() {
    const worker = new Worker(new URL("compile-worker.js", import.meta.url), { type: "module", name: "tools" });
    tools = worker;
    // It loads once the compile Worker has compiled `turkey.wasm`.
    if (turkeyModule !== null) {
      worker.postMessage({ type: "load", module: turkeyModule, lib: true });
    }
    const failed = (text) => {
      worker.terminate();
      tools = null;
      toolsReady = false;
      highlighting = false;
      checking = false;
      formatting = null;
      formatButton.disabled = true;
      note.textContent = `Highlighting, Format and Types are unavailable: ${text}`;
      renderTypes(typesPanel, null, { message: `Types are unavailable: ${text}` });
    };
    worker.onmessage = ({ data }) => {
      if (data.type === "ready") {
        toolsReady = true;
        formatButton.disabled = false;
        flushHighlight();
        checkNow();
      } else if (data.type === "checked") {
        checkedFiles(data);
      } else if (data.type === "failed") {
        failed(data.text);
      } else if (data.type === "highlighted") {
        highlighting = false;
        files.byId(data.id)?.editor.highlighted(data.source, data.html);
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
    } else if (formattingFile.textarea.value !== data.source || files.active() !== formattingFile) {
      // Replacing the source now would throw away what was typed since Format
      // was pressed.
      note.textContent = "The source changed, or another file was shown, while it was formatted; press Format again.";
    } else if (data.text === data.source) {
      note.textContent = "Already formatted.";
    } else {
      replaceText(formattingFile.textarea, data.text);
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
    formattingFile = files.active();
    tools.postMessage({
      type: "format",
      id: formatting,
      source: formattingFile.textarea.value,
      name: formattingFile.name,
    });
  });

  files.onChange(() => {
    formatError.hidden = true;
    load();
    // A file added, renamed or removed changes the program without an edit.
    checkWhenIdle();
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

  // -- Keyboard shortcuts --------------------------------------------------------

  // Caught before the editor sees the key, so that none types a character,
  // even held down; a held key presses its button once. A shortcut presses
  // its button, so it does what the button would: Ctrl+Enter stops a running
  // program, as the button does then, and a press of a disabled button does
  // nothing. On a link, Ctrl+Enter and Cmd+Enter open it in a new tab, so
  // there they are left to the browser. `setPhase` keeps Run's title.
  const formatKeys = shortcutText("format", mac);
  formatButton.title = `Format (${formatKeys.text})`;
  formatButton.setAttribute("aria-keyshortcuts", formatKeys.aria);
  runButton.setAttribute("aria-keyshortcuts", runKeys.aria);
  document.addEventListener(
    "keydown",
    (event) => {
      const action = shortcutFor(event);
      if (action === null || (action === "run" && event.target.closest?.("a[href]"))) {
        return;
      }
      event.preventDefault();
      if (!event.repeat) {
        (action === "run" ? runButton : formatButton).click();
      }
    },
    { capture: true },
  );

  formatButton.disabled = true;
  if (lazy) {
    setPhase("unloaded", "");
  } else {
    load();
  }
  // Stops what is in flight, and forgets a Run that waits for the compiler.
  function cancel() {
    runWhenReady = false;
    stop();
  }

  return { files, load, show, cancel };
}
