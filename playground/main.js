// The page: a highlighted source editor, Run and Stop, Format and Share, and
// an output pane. `session.js` compiles, runs, highlights and formats; this
// keeps the editor's text across visits, makes and opens share links, and
// fills the picker of example programs.

import { replaceText } from "./editor.js";
import { startSession } from "./session.js";
import { fragmentFor, sourceFromFragment } from "./share.js";

const $ = (id) => document.getElementById(id);
const source = $("source");
const shareButton = $("share");
const formatError = $("format-error");
const timeoutInput = $("timeout");
const note = $("note"); // what Format and Share last did

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
});

const { editor, show } = startSession(
  {
    source,
    highlighted: $("highlighted"),
    run: $("run"),
    stop: $("stop"),
    format: $("format"),
    output: $("output"),
    formatError,
    state: $("state"),
    note,
  },
  {
    timeLimit: () => (timeoutInput.value.trim() === "" ? NaN : Number(timeoutInput.value)),
  },
);

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

// The picker lists the tour's lesson programs, from `../tour/examples.json`,
// which the deploy writes beside the lessons, so the tour and the picker run
// the same source and the test harness checks it. Each entry's file is
// relative to the manifest. Choosing one replaces the editor's text through
// the browser's undo stack, so Ctrl+Z brings the previous text back, and so no
// confirmation is asked. A page served without the tour keeps the picker
// disabled and works as before.
const picker = $("examples");
let picking = 0; // the pick in flight, so a slow fetch never overwrites a later one
let files = {}; // each entry's file, by name, as a URL

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
    files = Object.fromEntries(entries.map((e) => [e.name, new URL(e.file, response.url).href]));
    picker.disabled = false;
  } catch {}
}

picker.addEventListener("change", async () => {
  const name = picker.value;
  if (name === "") {
    return;
  }
  const mine = ++picking;
  let text;
  try {
    const response = await fetch(files[name]);
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

loadLink();
loadExamples();
