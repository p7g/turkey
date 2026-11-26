// A program's files on the page: one editor per file, each a `.editor` with
// its own textarea and highlighted overlay, and a row of tabs that shows one
// at a time. The first file is the entry, which the compile starts from; it
// is never renamed or removed. Each file keeps its own text, undo history,
// scroll position and highlighting, since switching a tab only hides one
// editor and shows another.
//
// `nameProblem` needs no DOM, so node loads this module.

import { attachEditor, replaceText } from "./editor.js";

/** The entry's file name. It is never renamed, and nothing imports it. */
export const ENTRY = "main.gob";

/** The most files a program has, the entry included. */
export const MAX_FILES = 64;

/** The longest file name, in UTF-16 code units. */
export const MAX_NAME = 128;

// A module's file name: its path, each part a module name's part (a capital
// letter, then letters, digits, `_` and `'`), with `.gob` after the last.
// `src/TourSite.gob` checks a lesson's module files by the same rule, so a
// lesson never has a file this page would refuse.
const MODULE_NAME = /^[A-Z][A-Za-z0-9_']*(\/[A-Z][A-Za-z0-9_']*)*\.gob$/;

// Returns `name` as a message quotes it: cut short past 40 characters, since
// a message lands in a status line that a screen reader reads out whole.
function quoted(name) {
  return `'${name.length > 40 ? name.slice(0, 40) + "..." : name}'`;
}

/**
 * Returns why `name` cannot be a file of a program whose other files are
 * `others`, or null if it can. The entry, `entry` true, is `ENTRY`; every
 * other file is a module, named by its path, such as `Geometry.gob` or
 * `Shapes/Circle.gob`, and no longer than `MAX_NAME`. No two files have the
 * same name.
 */
export function nameProblem(name, others = [], { entry = false } = {}) {
  if (entry) {
    return name === ENTRY ? null : `the entry is ${ENTRY}, not ${quoted(name)}`;
  }
  if (name.length > MAX_NAME) {
    return `${quoted(name)} is longer than ${MAX_NAME} characters`;
  }
  if (!MODULE_NAME.test(name)) {
    return `${quoted(name)} is not a module's file name, such as Geometry.gob or Shapes/Circle.gob`;
  }
  if (others.includes(name)) {
    return `there is already a file named ${quoted(name)}`;
  }
  return null;
}

/**
 * Returns the problem with `pairs`, `[name, source]` pairs with the entry
 * first, as a program's files, or null if there is none: no files, more
 * than `MAX_FILES`, a pair that is not two strings, or a name `nameProblem`
 * refuses.
 */
export function filesProblem(pairs) {
  if (!Array.isArray(pairs) || pairs.length === 0) {
    return "a program has one or more files";
  }
  if (pairs.length > MAX_FILES) {
    return `a program has at most ${MAX_FILES} files`;
  }
  const seen = [];
  for (const [i, pair] of pairs.entries()) {
    if (!Array.isArray(pair) || pair.length !== 2 || pair.some((part) => typeof part !== "string")) {
      return "each file is a name and a source";
    }
    const problem = nameProblem(pair[0], seen, { entry: i === 0 });
    if (problem !== null) {
      return problem;
    }
    seen.push(pair[0]);
  }
  return null;
}

/**
 * Starts the files in `container`, which holds the editors, each a `.editor`
 * element with a `<pre><code>` overlay and a `<textarea>`, as `attachEditor`
 * takes them, and the file's name in its `data-file` attribute. The first is
 * the entry, `ENTRY`. Puts the tabs before the first editor, and shows the
 * entry.
 *
 * With `editable`, the tabs can add, rename and remove files other than the
 * entry; without it, they only switch between files, and are hidden while
 * there is only one. `say(...parts)` reports what an action did, or why it
 * did not, as text and nodes. `requestHighlight(file, text)` is called as
 * `attachEditor` calls its own, with the file it is for.
 *
 * Returns the file set: `all()` returns the files as `[name, text]` pairs,
 * the entry first; `active()` returns the file shown; `byId(id)` returns a
 * file by its `id`, or null once it is removed; `replace(pairs, { undoable,
 * quiet })` makes the files `pairs`, which `filesProblem` accepts, and shows
 * the entry; `onChange(callback)` calls `callback()` after every edit, after
 * a file is added, renamed or removed, and after a `replace` that is not
 * `quiet`. A file is `{ id, name, textarea, editor }`, `editor` being what
 * `attachEditor` returned for it.
 */
export function attachFiles(container, { editable = false, say = () => {}, requestHighlight = () => {} } = {}) {
  const panels = [...container.querySelectorAll(":scope > .editor")];
  const template = panels[0];
  const tabs = document.createElement("div");
  tabs.className = "tabs";
  const list = document.createElement("div");
  list.className = "tablist";
  list.setAttribute("role", "tablist");
  list.setAttribute("aria-label", "Files");
  tabs.append(list);
  const actions = document.createElement("div");
  actions.className = "file-actions";
  const addButton = button("Add file", () => add());
  const renameButton = button("Rename", () => startRename(active));
  const removeButton = button("Remove", () => remove(active));
  if (editable) {
    actions.append(addButton, renameButton, removeButton);
    tabs.append(actions);
  }
  container.insertBefore(tabs, template);

  const listeners = [];
  // Counts the changes, so that an Undo offered for a removal can tell
  // whether the files have changed since.
  let version = 0;
  let quieted = false; // whether a quiet `replace` is under way
  const changed = () => {
    version++;
    if (!quieted) {
      listeners.forEach((listener) => listener());
    }
  };

  let nextId = 0;
  const files = [];
  let active = null;

  function button(text, onClick) {
    const element = document.createElement("button");
    element.type = "button";
    element.textContent = text;
    element.addEventListener("click", onClick);
    return element;
  }

  // Makes a file of `panel`, an editor already in the container, with a tab.
  function adopt(panel, name) {
    const file = {
      id: ++nextId,
      name,
      panel,
      textarea: panel.querySelector("textarea"),
      tab: document.createElement("button"),
      editor: null,
    };
    panel.id ||= `file-${file.id}`;
    panel.setAttribute("role", "tabpanel");
    file.tab.type = "button";
    file.tab.setAttribute("role", "tab");
    file.tab.setAttribute("aria-controls", panel.id);
    file.tab.addEventListener("click", () => show(file));
    file.textarea.addEventListener("input", changed);
    file.editor = attachEditor(file.textarea, panel.querySelector("code"), (text) => requestHighlight(file, text));
    files.push(file);
    label(file);
    return file;
  }

  // Keeps what shows the file's name in step with it.
  function label(file) {
    file.panel.dataset.file = file.name;
    file.tab.textContent = file.name;
    file.textarea.setAttribute("aria-label", `Turkey source, ${file.name}`);
  }

  // Returns a new editor holding `text`, after the last, made like the
  // entry's.
  function panelFor(text) {
    const panel = document.createElement("div");
    panel.className = template.className;
    const pre = template.querySelector("pre").cloneNode(false);
    pre.append(document.createElement("code"));
    const textarea = template.querySelector("textarea").cloneNode(false);
    textarea.removeAttribute("id");
    textarea.value = text;
    panel.append(pre, textarea);
    container.insertBefore(panel, files[files.length - 1].panel.nextSibling);
    return panel;
  }

  function show(file) {
    active = file;
    for (const f of files) {
      const selected = f === file;
      f.panel.hidden = !selected;
      f.tab.setAttribute("aria-selected", String(selected));
      f.tab.tabIndex = selected ? 0 : -1;
    }
    renameButton.disabled = removeButton.disabled = file === files[0];
    file.editor.changed();
  }

  // The tabs keep their order, so the arrow keys move along them, and Home
  // and End go to the first and the last.
  list.addEventListener("keydown", (event) => {
    const at = files.findIndex((f) => f.tab === event.target);
    const to = {
      ArrowLeft: (at - 1 + files.length) % files.length,
      ArrowRight: (at + 1) % files.length,
      Home: 0,
      End: files.length - 1,
    }[event.key];
    if (at < 0 || to === undefined) {
      return;
    }
    event.preventDefault();
    const next = files[to];
    show(next);
    next.tab.focus();
  });

  function layout() {
    list.replaceChildren(...files.map((f) => f.tab));
    tabs.hidden = !editable && files.length === 1;
  }

  function add() {
    if (files.length >= MAX_FILES) {
      say(`A program has at most ${MAX_FILES} files.`);
      return;
    }
    const names = files.map((f) => f.name);
    let n = 1;
    while (names.includes(n === 1 ? "Module.gob" : `Module${n}.gob`)) {
      n++;
    }
    const file = adopt(panelFor(""), n === 1 ? "Module.gob" : `Module${n}.gob`);
    layout();
    show(file);
    changed();
    startRename(file);
  }

  // Puts an input over the tab of `file`, holding its name. Enter renames
  // the file, if the name can be its, and otherwise says why and leaves the
  // input open. Leaving the input renames it too, and keeps the old name for
  // a name it cannot have, since holding the focus there would trap it.
  // Escape keeps the old name.
  function startRename(file) {
    if (file === files[0]) {
      return;
    }
    const input = document.createElement("input");
    input.className = "rename";
    input.value = file.name;
    input.setAttribute("aria-label", `New name for ${file.name}`);
    input.spellcheck = false;
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("autocomplete", "off");
    input.size = Math.max(12, file.name.length + 2);
    let done = false;
    // `how` is "enter", "blur" or "escape".
    const finish = (how) => {
      if (done) {
        return;
      }
      const name = input.value.trim();
      if (how !== "escape" && name !== file.name) {
        const problem = nameProblem(
          name,
          files.filter((f) => f !== file).map((f) => f.name),
        );
        if (problem !== null) {
          say(`Not renamed: ${problem}.`);
          if (how === "enter") {
            return;
          }
        } else {
          file.name = name;
          label(file);
          say(`Renamed to ${name}.`);
          changed();
        }
      }
      done = true;
      input.replaceWith(file.tab);
      if (how !== "blur") {
        file.tab.focus();
      }
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        finish("enter");
      } else if (event.key === "Escape") {
        event.preventDefault();
        finish("escape");
      }
    });
    input.addEventListener("blur", () => finish("blur"));
    file.tab.replaceWith(input);
    input.focus();
    input.setSelectionRange(0, file.name.length - ".gob".length);
  }

  // Removes `file`, and offers to bring it back until the next change to
  // the files, after which it might come back into another program.
  function remove(file) {
    const at = files.indexOf(file);
    if (at <= 0) {
      return;
    }
    files.splice(at, 1);
    file.panel.remove();
    layout();
    show(files[at - 1]);
    changed();
    const removedAt = version;
    const undo = button("Undo", () => {
      if (version !== removedAt) {
        say(`The files have changed since, so ${file.name} stays removed.`);
        return;
      }
      container.insertBefore(file.panel, files[Math.min(at, files.length) - 1].panel.nextSibling);
      files.splice(Math.min(at, files.length), 0, file);
      layout();
      show(file);
      changed();
      say(`${file.name} is back.`);
    });
    say(`Removed ${file.name}. `, undo);
  }

  // Makes the files `pairs`, keeping the editor, and so the undo history, of
  // each file whose name stays. With `undoable`, the entry's new text is one
  // edit that undo reverts; a file that is not shown cannot be edited that
  // way, so the others' texts are set outright.
  //
  // With `quiet`, no `onChange` callback is called: files that a link or
  // the page's storage brings back are not the reader's edit, and are saved
  // only once the reader edits them.
  function replace(pairs, { undoable = false, quiet = false } = {}) {
    quieted = quiet;
    try {
      replaceAll(pairs, undoable);
    } finally {
      quieted = false;
    }
  }

  function replaceAll(pairs, undoable) {
    const [entry, ...modules] = files;
    show(entry);
    setText(entry, pairs[0][1], undoable);
    entry.textarea.setSelectionRange(0, 0);
    entry.textarea.scrollTop = 0;
    const kept = new Map(modules.map((f) => [f.name, f]));
    files.length = 1;
    for (const [name, text] of pairs.slice(1)) {
      const file = kept.get(name);
      if (file === undefined) {
        adopt(panelFor(text), name);
      } else {
        kept.delete(name);
        files.push(file);
        container.insertBefore(file.panel, files[files.length - 2].panel.nextSibling);
        setText(file, text, false);
      }
    }
    for (const file of kept.values()) {
      file.panel.remove();
    }
    layout();
    show(entry);
    changed();
  }

  function setText(file, text, undoable) {
    if (file.textarea.value === text) {
      return;
    }
    if (undoable && file === active) {
      replaceText(file.textarea, text);
    } else {
      file.textarea.value = text;
      file.editor.changed();
    }
  }

  for (const [i, panel] of panels.entries()) {
    adopt(panel, i === 0 ? ENTRY : panel.dataset.file);
  }
  layout();
  show(files[0]);

  return {
    all: () => files.map((f) => [f.name, f.textarea.value]),
    active: () => active,
    byId: (id) => files.find((f) => f.id === id) ?? null,
    replace,
    onChange(callback) {
      listeners.push(callback);
    },
  };
}
