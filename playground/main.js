// The page: a highlighted source editor with a tab per file, Run and Stop,
// Format and Share, and an output pane. `session.js` compiles, runs,
// highlights and formats; this keeps the files across visits, makes and opens
// share links, and fills the picker of example programs.

import { startSession } from "./session.js";
import { ENTRY, filesProblem } from "./files.js";
import { fragmentFor, filesFromFragment } from "./share.js";

const $ = (id) => document.getElementById(id);
const shareButton = $("share");
const formatError = $("format-error");
const timeoutInput = $("timeout");
const note = $("note"); // what Format, Share and the tabs last did

const HELLO = `fun main() {
\tprint("Hello, Turkey!")
}
`;

// Past this length a link still works in the browser that made it, but chat
// apps, mail clients and URL shorteners may cut it short, so we say so.
const LONG_LINK = 8000;

// `localStorage` holds the files as JSON, `[name, text]` pairs with the entry
// first, under FILES. SOURCE may hold the text of `main.gob` alone, the only
// file, as a plain string; it is read when FILES is missing, and removed
// once FILES is written.
const FILES = "turkey-playground-files";
const SOURCE = "turkey-playground-source";

// Returns the files the last visit left, or the greeting.
function savedFiles() {
  try {
    const saved = localStorage.getItem(FILES);
    if (saved !== null) {
      const pairs = JSON.parse(saved);
      if (filesProblem(pairs) === null) {
        return pairs;
      }
    }
    return [[ENTRY, localStorage.getItem(SOURCE) ?? HELLO]];
  } catch {
    return [[ENTRY, HELLO]];
  }
}

const { files, show } = startSession(
  {
    files: $("files"),
    run: $("run"),
    stop: $("stop"),
    format: $("format"),
    output: $("output"),
    formatError,
    state: $("state"),
    note,
  },
  {
    editable: true,
    timeLimit: () => (timeoutInput.value.trim() === "" ? NaN : Number(timeoutInput.value)),
  },
);
files.replace(savedFiles(), { quiet: true });

// Whether the address bar's fragment holds the files: true after a link
// loads or Share makes one, false from the next change. The first change
// drops the fragment, so a reload brings back the edits, which `localStorage`
// keeps, and not the files the link held.
let linked = false;
files.onChange(() => {
  try {
    localStorage.setItem(FILES, JSON.stringify(files.all()));
    localStorage.removeItem(SOURCE);
  } catch {}
  if (linked) {
    linked = false;
    history.replaceState(null, "", location.pathname + location.search);
  }
});

// -- Share links -----------------------------------------------------------------

shareButton.addEventListener("click", async () => {
  const shared = files.all();
  let fragment;
  try {
    fragment = await fragmentFor(shared);
  } catch (error) {
    note.textContent = `No link: ${error.message}.`;
    return;
  }
  // A link for files since changed would hold the wrong program.
  if (JSON.stringify(files.all()) !== JSON.stringify(shared)) {
    note.textContent = "The files changed while the link was made; press Share again.";
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

// Shows the files the fragment holds, if it holds any. A fragment that
// cannot be read leaves the editor as it was and says why.
async function loadLink() {
  let pairs;
  try {
    pairs = await filesFromFragment(location.hash);
  } catch (error) {
    note.textContent = `This link cannot be opened: ${error.message}.`;
    return;
  }
  if (pairs !== null) {
    // Quietly, so that what the link holds is neither saved nor taken for
    // an edit that drops the fragment, until the reader's first edit.
    files.replace(pairs, { quiet: true });
    linked = true;
  }
}
window.addEventListener("hashchange", loadLink);

// -- Examples ------------------------------------------------------------------

// The picker lists the tour's lesson programs, from `../tour/examples.json`,
// which the deploy writes beside the lessons, so the tour and the picker run
// the same source and the test harness checks it. Each entry's file, and each
// of its `modules`, is relative to the manifest. Choosing one replaces the
// files, the entry's text through the browser's undo stack, so Ctrl+Z brings
// the previous text back, and so no confirmation is asked. A page served
// without the tour keeps the picker disabled and works as before.
const picker = $("examples");
let picking = 0; // the pick in flight, so a slow fetch never overwrites a later one
let examples = {}; // each entry's files, by name, as [file name, URL] pairs

async function loadExamples() {
  try {
    const response = await fetch("../tour/examples.json");
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
    const url = (file) => new URL(file, response.url).href;
    examples = Object.fromEntries(
      entries.map((e) => [
        e.name,
        [[ENTRY, url(e.file)], ...(e.modules ?? []).map((m) => [m.name, url(m.file)])],
      ]),
    );
    picker.disabled = false;
  } catch {}
}

picker.addEventListener("change", async () => {
  const name = picker.value;
  if (name === "") {
    return;
  }
  const mine = ++picking;
  let pairs;
  try {
    pairs = await Promise.all(
      examples[name].map(async ([file, url]) => {
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return [file, await response.text()];
      }),
    );
  } catch (error) {
    note.textContent = `The example could not be loaded: ${error.message}.`;
    return;
  }
  if (mine !== picking) {
    return;
  }
  files.replace(pairs, { undoable: true });
  show([]);
  formatError.hidden = true;
  note.textContent = `Loaded "${picker.selectedOptions[0].textContent}". Undo brings back the previous text.`;
  picker.value = "";
});

loadLink();
loadExamples();
