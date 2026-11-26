// A lesson of the tour, as `src/TourSite.gob` writes it: the lesson's prose
// beside its program, in an editor with Run, Stop, Format, Reset and Share.
//
// The compiler loads on the first edit or Run, not with the page, so reading
// a lesson costs no download; until then the output pane shows the output
// recorded when the page was made. Edits are kept in `localStorage` for each
// lesson, under a key that includes a hash of the lesson's program, so a
// lesson whose program has since changed starts from the new one. Share
// copies a link that opens the editor's text in the playground.
//
// A lesson whose program the browser cannot run has no editor, and this
// does nothing on its page.

import { replaceText } from "./editor.js";
import { startSession } from "./session.js";
import { fragmentFor } from "./share.js";

const $ = (id) => document.getElementById(id);
const source = $("source");
const lesson = document.querySelector("main.lesson");

if (source !== null && lesson !== null) {
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

function start() {
  const output = $("output");
  const note = $("note");
  const formatError = $("format-error");
  const resetButton = $("reset");
  const shareButton = $("share");
  // The textarea's own text is the lesson's program, whatever `value` holds.
  const original = source.defaultValue;
  const key = `turkey-tour:${lesson.dataset.lesson}:${fnv1a(original)}`;
  const recorded = [...output.childNodes].map((node) => node.cloneNode(true));

  let saved = null;
  try {
    saved = localStorage.getItem(key);
  } catch {}
  if (saved !== null && saved !== original) {
    source.value = saved;
  }

  const session = startSession(
    {
      source,
      highlighted: $("highlighted"),
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
  if (source.value !== original) {
    note.textContent = "Your edits to this lesson are back. Reset brings back its program.";
    session.load();
  }

  source.addEventListener("input", () => {
    try {
      if (source.value === original) {
        localStorage.removeItem(key);
      } else {
        localStorage.setItem(key, source.value);
      }
    } catch {}
  });
  // Focusing the editor is a sign of an edit to come, so the compiler starts
  // loading then rather than on the first keystroke.
  source.addEventListener("focus", () => session.load());

  // Reset puts the program back through the undo stack, so Ctrl+Z brings the
  // edits back, and shows the recorded output again.
  resetButton.addEventListener("click", () => {
    if (source.value !== original) {
      replaceText(source, original);
      source.setSelectionRange(0, 0);
      source.scrollTop = 0;
    }
    formatError.hidden = true;
    output.replaceChildren(...recorded.map((node) => node.cloneNode(true)));
    note.textContent = "This is the lesson's program again. Undo brings back your edits.";
  });

  // Each "Try it" loads its example into the editor. The example's highlighted
  // code reads exactly as its source does, so its text is the source.
  for (const button of document.querySelectorAll("button.try")) {
    button.disabled = false;
    button.addEventListener("click", () => {
      const code = button.closest(".example").querySelector("pre.shown code");
      replaceText(source, code.textContent);
      source.setSelectionRange(0, 0);
      source.scrollTop = 0;
      session.show([]);
      formatError.hidden = true;
      note.textContent = "The example is in the editor. Run it, or Reset to bring back the lesson's program.";
      source.scrollIntoView({ block: "nearest" });
    });
  }

  shareButton.addEventListener("click", async () => {
    const text = source.value;
    let fragment;
    try {
      fragment = await fragmentFor(text);
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
