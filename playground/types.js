// The Types view: the inferred type of each top-level binding of the
// program, from the `types` that `check` in `host.js` returns, listed by file
// in the output pane's Types tab, and shown in the tip when the pointer is
// over a binding's name in the editor.
//
// Everything but `renderTypes` needs no DOM, so node loads this module.

import { offsetsIn } from "./diagnostics.js";

/**
 * Returns what the Types view shows for one check of a program whose files
 * are named `names`, the entry first: `{ current, files }`. `diagnostics`
 * and `types` are what `check` returned. `files` holds one `{ file,
 * bindings }` for each file in `names` that has a binding, in the order of
 * `names`, and each binding is `{ name, type, line, column }`, in the order
 * of their positions in the file; a binding in a file not in `names` is left
 * out. `current` is false when the check found an error before the types
 * were known, so `types` is empty and says nothing about the program as it
 * is; it is true otherwise, even with `files` empty.
 */
export function typesFor(diagnostics, types, names) {
  const errors = diagnostics.some((d) => d.severity === "error");
  // The compiler writes the types once inference succeeds, and an error
  // found later, by a lint denied or by lowering, leaves them in place, so
  // any types at all say they are the program's.
  const current = types.length > 0 || !errors;
  const byFile = new Map(names.map((name) => [name, []]));
  for (const { file, name, type, line, column } of types) {
    byFile.get(file)?.push({ name, type, line, column });
  }
  const files = [];
  for (const [file, bindings] of byFile) {
    if (bindings.length > 0) {
      bindings.sort((a, b) => a.line - b.line || a.column - b.column);
      files.push({ file, bindings });
    }
  }
  return { current, files };
}

/**
 * Returns where the name `name` is in `text`, as `[start, end]` UTF-16
 * offsets, for a binding the compiler places at `offset`: the start of its
 * declaration, which for a `fun` is the keyword and for a variable is the
 * name. The name is looked for from `offset` to the end of its line; when it
 * is not there, both ends are `offset`.
 */
export function nameRange(text, offset, name) {
  const lineEnd = text.indexOf("\n", offset);
  const line = text.slice(offset, lineEnd < 0 ? text.length : lineEnd);
  // A name stands alone: not the start of a longer name, nor its end.
  const word = /[A-Za-z0-9_']/;
  for (let at = line.indexOf(name); at >= 0; at = line.indexOf(name, at + 1)) {
    const before = line[at - 1];
    const after = line[at + name.length];
    if ((before === undefined || !word.test(before)) && (after === undefined || !word.test(after))) {
      return [offset + at, offset + at + name.length];
    }
  }
  return [offset, offset];
}

/**
 * Returns the marks for the bindings of `bindings`, as `typesFor` lists
 * them for one file, whose text is `text`: one `{ start, end, severity:
 * "type", label, notes }` over each binding's name that can be found, as
 * `nameRange` finds it, whose label is `NAME : TYPE`. `marks.js` shows such a
 * mark's label when the pointer is over it, and draws no underline.
 */
export function typeMarksFor(text, bindings) {
  const offset = offsetsIn(text);
  const marks = [];
  for (const { name, type, line, column } of bindings) {
    const [start, end] = nameRange(text, offset(line, column), name);
    if (start < end) {
      marks.push({ start, end, severity: "type", label: `${name} : ${type}`, notes: [] });
    }
  }
  return marks;
}

/**
 * Fills `container` with `view`, as `typesFor` returns it, or with only
 * `message` when `view` is null. A view that is not `current` is the last
 * one that was, shown under a line that says so. Each binding's name is a
 * button that calls `onPick(file, binding)`. Every text is set as text, so
 * nothing the compiler writes is read as HTML.
 */
export function renderTypes(container, view, { message = "", onPick = () => {} } = {}) {
  const say = (text) => {
    const element = document.createElement("p");
    element.className = "types-state";
    element.textContent = text;
    return element;
  };
  container.classList.toggle("stale", view !== null && !view.current);
  if (view === null) {
    container.replaceChildren(say(message));
    return;
  }
  const parts = [];
  if (!view.current) {
    parts.push(say("The program has errors. These are the types from the last time it type-checked."));
  }
  if (view.files.length === 0) {
    parts.push(say("The program has no top-level bindings."));
  }
  const several = view.files.length > 1;
  for (const { file, bindings } of view.files) {
    if (several) {
      const heading = document.createElement("h3");
      heading.textContent = file;
      parts.push(heading);
    }
    const list = document.createElement("ul");
    list.className = "bindings";
    for (const binding of bindings) {
      const item = document.createElement("li");
      const name = document.createElement("button");
      name.type = "button";
      name.className = "binding";
      name.textContent = binding.name;
      name.title = `Show ${binding.name} in ${file}`;
      name.addEventListener("click", () => onPick(file, binding));
      const type = document.createElement("code");
      type.textContent = ` : ${binding.type}`;
      item.append(name, type);
      list.append(item);
    }
    parts.push(list);
  }
  container.replaceChildren(...parts);
}
