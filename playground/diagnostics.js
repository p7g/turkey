// Filters the compiler's diagnostics for the page, and turns the ones `check`
// returns into marks on the editor's text. No DOM, so node loads it.

// The wasm32 backend refuses some functions and compiles each to a trap. It
// warns once per compile that has any, "main.gob: warning: N functions use
// operations the wasm32 backend does not compile yet ...", and counts the
// library's refused functions along with the user's, whether the program
// calls them or not.
const REFUSED = /functions? use operations the wasm32 backend does not compile yet/;

// Returns the diagnostics to show for a program, trimmed. The refused-functions
// warning is dropped unless the run `trapped`: a program that compiles and
// exits never reached a refused function, so the warning is noise, and one
// that traps may have reached one, so the warning is the clue to why.
export function diagnosticsFor(diagnostics, trapped) {
  return diagnostics
    .split("\n")
    .filter((line) => trapped || !REFUSED.test(line))
    .join("\n")
    .trim();
}

/**
 * Returns the marks for `diagnostics`, what `check` in `host.js` returns for
 * `text` as the module `file`: one `{ start, end, severity, label, notes }`
 * for each diagnostic in that file that has a position, in the order given.
 * `start` and `end` are UTF-16 offsets into `text`, as a textarea counts
 * them, and `start < end` unless `text` has no character on that line to
 * mark. `severity` is "error" or "warning". `label` is the text to show for
 * the diagnostic, and `notes` holds one text for each of its notes, which
 * names the note's file when it is not `file`.
 *
 * The compiler counts lines and columns from 1, and columns in code points.
 * A column past the end of its line is taken as the line's end, and a line
 * past the end of the text as the text's end.
 */
export function marksFor(text, diagnostics, file = "main.gob") {
  const offset = offsetsIn(text);
  const marks = [];
  for (const d of diagnostics) {
    if (d.file !== file || d.line === null) {
      continue;
    }
    let start = offset(d.line, d.column);
    let end = Math.max(start, offset(d.endLine, d.endColumn));
    // A diagnostic between two characters, such as at a missing quote, marks
    // the character after it, or else the one before, so that it shows.
    if (start === end) {
      if (end < text.length && text.charCodeAt(end) !== NEWLINE) {
        end += unitsAt(text, end);
      } else if (start > 0 && text.charCodeAt(start - 1) !== NEWLINE) {
        start -= start > 1 && isLow(text.charCodeAt(start - 1)) ? 2 : 1;
      }
    }
    marks.push({
      start,
      end,
      severity: d.severity === "error" ? "error" : "warning",
      label: `${d.stage}: ${d.message}${d.code === null ? "" : ` [${d.code}]`}`,
      notes: d.notes.map((n) => `${noteWhere(n, d.file)}: note: ${n.message}`),
    });
  }
  return marks;
}

const NEWLINE = 10;

/**
 * Returns a function from a position the compiler writes in `text`, a
 * 1-based line and column counted in code points, to its UTF-16 offset, as a
 * textarea counts. A column past the end of its line is taken as the line's
 * end, and a line past the end of the text as the text's end.
 */
export function offsetsIn(text) {
  // Where each line starts, as a UTF-16 offset.
  const lines = [0];
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) === NEWLINE) {
      lines.push(i + 1);
    }
  }
  return (line, column) => {
    if (line > lines.length) {
      return text.length;
    }
    let at = lines[line - 1];
    const lineEnd = line < lines.length ? lines[line] - 1 : text.length;
    for (let left = column - 1; left > 0 && at < lineEnd; left--) {
      at += unitsAt(text, at);
    }
    return at;
  };
}

// Returns where a note is, for its text: its line in the diagnostic's own
// file, and its file and line in any other.
function noteWhere(note, file) {
  if (note.line === null) {
    return note.file;
  }
  return note.file === file ? `line ${note.line}` : `${note.file}:${note.line}`;
}

// How many UTF-16 units the code point at `at` in `text` takes.
const unitsAt = (text, at) => (text.codePointAt(at) > 0xffff ? 2 : 1);

const isLow = (unit) => unit >= 0xdc00 && unit <= 0xdfff;

/**
 * Returns `marks` carried through an edit that replaced `start` up to
 * `oldEnd` of the old text with what is `start` up to `newEnd` of the new,
 * as `changedRange` in `editor.js` returns one. A mark that ends before the
 * edit stays, one that starts after it moves with the text, and one the edit
 * reaches into is dropped: what it marked has changed, and the next check
 * says whether it still applies.
 */
export function shiftMarks(marks, { start, oldEnd, newEnd }) {
  const kept = [];
  for (const mark of marks) {
    if (mark.end <= start) {
      kept.push(mark);
    } else if (mark.start >= oldEnd) {
      kept.push({ ...mark, start: mark.start + newEnd - oldEnd, end: mark.end + newEnd - oldEnd });
    }
  }
  return kept;
}
