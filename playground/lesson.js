// A lesson of the tour, as `src/TourSite.gob` writes it: the lesson's prose
// beside its program, in an editor with Run, Stop, Format, Reset and Share,
// and a tab for each file of a program that has several.
//
// The compiler loads on the first edit or Run, not with the page, so reading
// a lesson costs no download; until then the output pane shows the output
// recorded when the page was made. Edits are kept in `localStorage` for each
// lesson, every file's, under a key that includes a hash of the lesson's
// program, so a lesson whose program has since changed starts from the new
// one. Share copies a link that opens the files in the playground.
//
// A lesson whose program the browser cannot run has no editor, and this
// does nothing on its page.

import { filesProblem } from "./files.js";
import { startSession } from "./session.js";
import { fragmentFor } from "./share.js";

const $ = (id) => document.getElementById(id);
const filesElement = $("files");
const lesson = document.querySelector("main.lesson");

if (filesElement !== null && lesson !== null) {
  start();
}

// Returns a 32-bit FNV-1a hash of `text`'s UTF-16 code units, in hex. It
// tells one version of a lesson's program from another, and needs no more.
function fnv1a(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

// Returns whether two sets of files, as `[name, text]` pairs, are the same.
function same(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function start() {
  const output = $("output");
  const note = $("note");
  const formatError = $("format-error");
  const resetButton = $("reset");
  const shareButton = $("share");
  // The textareas' own texts are the lesson's program, whatever `value` holds.
  const original = [...filesElement.querySelectorAll(":scope > .editor")].map((panel) => [
    panel.dataset.file,
    panel.querySelector("textarea").defaultValue,
  ]);
  // A program of one file is hashed by its text alone. What is stored is
  // `{"files": PAIRS}` as JSON, or, for a program of one file, may be the
  // text of `main.gob` alone, as a plain string.
  const key = `turkey-tour:${lesson.dataset.lesson}:${fnv1a(
    original.length === 1 ? original[0][1] : JSON.stringify(original),
  )}`;
  const recorded = [...output.childNodes].map((node) => node.cloneNode(true));

  // Returns the files stored for this lesson, or null.
  function stored() {
    let saved = null;
    try {
      saved = localStorage.getItem(key);
    } catch {}
    if (saved === null) {
      return null;
    }
    try {
      const { files } = JSON.parse(saved);
      if (filesProblem(files) === null) {
        return files;
      }
    } catch {}
    return original.length === 1 ? [[original[0][0], saved]] : null;
  }

  const session = startSession(
    {
      files: filesElement,
      run: $("run"),
      stop: $("stop"),
      format: $("format"),
      output,
      formatError,
      state: $("state"),
      note,
    },
    { lazy: true },
  );
  const { files } = session;
  const saved = stored();
  if (saved !== null && !same(saved, original)) {
    files.replace(saved, { quiet: true });
    note.textContent = "Your edits to this lesson are back. Reset brings back its program.";
    session.load();
  }

  // Whether the editor's text is being replaced by an example, which is not
  // an edit of the reader's: what is stored stays as it was, so a reload
  // brings back the reader's own edits, or the program.
  let loadingExample = false;
  files.onChange(() => {
    if (loadingExample) {
      return;
    }
    try {
      if (same(files.all(), original)) {
        localStorage.removeItem(key);
      } else {
        localStorage.setItem(key, JSON.stringify({ files: files.all() }));
      }
    } catch {}
  });
  // Focusing the editor is a sign of an edit to come, so the compiler starts
  // loading then rather than on the first keystroke.
  filesElement.addEventListener("focusin", () => session.load());

  // Reset puts the program back through the undo stack, so Ctrl+Z brings the
  // edits back, and shows the recorded output again. A run in flight is
  // stopped first, so its output cannot replace the recorded output.
  resetButton.addEventListener("click", () => {
    session.cancel();
    if (!same(files.all(), original)) {
      files.replace(original, { undoable: true });
    }
    formatError.hidden = true;
    // `show` selects the Output tab, which the recorded output then fills.
    session.show([]);
    output.replaceChildren(...recorded.map((node) => node.cloneNode(true)));
    note.textContent = "This is the lesson's program again. Undo brings back your edits.";
  });

  // Each "Try it" loads its example into the entry's editor, leaving the
  // other files as they are. The example's highlighted code reads exactly as
  // its source does, so its text is the source.
  for (const button of document.querySelectorAll("button.try")) {
    button.disabled = false;
    button.addEventListener("click", () => {
      const code = button.closest(".example").querySelector("pre.shown code");
      session.cancel();
      loadingExample = true;
      try {
        const [[entry], ...modules] = files.all();
        files.replace([[entry, code.textContent], ...modules], { undoable: true });
      } finally {
        loadingExample = false;
      }
      session.show([]);
      formatError.hidden = true;
      note.textContent = "The example is in the editor. Run it, or Reset to bring back the lesson's program.";
      filesElement.scrollIntoView({ block: "nearest" });
    });
  }

  shareButton.addEventListener("click", async () => {
    let fragment;
    try {
      fragment = await fragmentFor(files.all());
    } catch (error) {
      note.textContent = `No link: ${error.message}.`;
      return;
    }
    const url = new URL(`../playground/${fragment}`, location.href).href;
    let copied = false;
    try {
      await navigator.clipboard.writeText(url);
      copied = true;
    } catch {}
    const link = document.createElement("a");
    link.href = url;
    link.textContent = "open it in the playground";
    note.replaceChildren(copied ? "Link copied; " : "The link could not be copied; ", link, ".");
  });

  resetButton.disabled = false;
  shareButton.disabled = false;
}
