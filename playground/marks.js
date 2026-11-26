// The editor's diagnostic marks: a wavy underline under each span the checker
// reported, red for an error and amber for a warning, and the message in a
// tip beside it.
//
// The underlines are drawn by a layer of their own, a `<pre>` over the
// highlighted one and under the textarea, holding the same text in a
// transparent colour with each marked stretch in a `<span>`. It shares the
// editor's font, padding and tab size and is moved by the same transform, so
// it lays the text out exactly as the textarea does; it is rebuilt from the
// textarea's text on every change, so it never holds other text. Keeping the
// marks out of the highlighted layer leaves that layer's text equal to the
// textarea's, which its own patching relies on.
//
// Both layers ignore the pointer, so the tip is found by hit-testing the
// marked spans' boxes against the pointer over the textarea. For the
// keyboard, the tip also shows while the caret is in or at the edge of a
// marked span, a status region reads it out, F8 and Shift+F8 move the caret
// to the next and previous mark, and Escape hides the tip.

import { shiftMarks } from "./diagnostics.js";

/**
 * Starts marking `textarea`, whose overlay `<pre>` is `overlay`. Returns `{
 * set(marks), changed(range), follow() }`: `set` replaces the marks with
 * `marks`, as `marksFor` in `diagnostics.js` makes them for the textarea's
 * text now; `changed` carries them through an edit, and must be called after
 * every change to the text with what `changedRange` in `editor.js` returns
 * for it; `follow` moves the layer to the textarea's scroll position.
 */
export function attachMarks(textarea, overlay) {
  const pre = document.createElement("pre");
  pre.className = "marks";
  pre.setAttribute("aria-hidden", "true");
  const code = document.createElement("code");
  pre.append(code);
  overlay.after(pre);

  const tip = document.createElement("div");
  tip.className = "diagnostic-tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.append(tip);
  // What the caret's tip says, for a screen reader. The tip itself is not a
  // live region, so a tip the pointer opens is not read out.
  const spoken = document.createElement("div");
  spoken.className = "visually-hidden";
  spoken.setAttribute("role", "status");
  overlay.parentElement.after(spoken);

  let marks = [];
  let text = textarea.value;
  // Each marked stretch of the layer: `{ start, end, element, covering }`,
  // where `covering` lists the indices of the marks over it.
  let stretches = [];

  function render() {
    stretches = [];
    if (marks.length === 0) {
      code.replaceChildren();
      return;
    }
    // Cut the text at every mark's ends, and give each piece the marks over it.
    const cuts = [...new Set(marks.flatMap((m) => [m.start, m.end]))].sort((a, b) => a - b);
    const last = cuts[cuts.length - 1];
    const nodes = [];
    let at = 0;
    for (const cut of cuts) {
      if (cut > at) {
        const covering = marks.flatMap((m, i) => (m.start <= at && cut <= m.end ? [i] : []));
        const piece = text.slice(at, cut);
        if (covering.length === 0) {
          nodes.push(document.createTextNode(piece));
        } else {
          const element = document.createElement("span");
          const error = covering.some((i) => marks[i].severity === "error");
          element.className = error ? "squiggle-error" : "squiggle-warning";
          element.textContent = piece;
          nodes.push(element);
          stretches.push({ start: at, end: cut, element, covering });
        }
      }
      at = cut;
    }
    // Nothing after the last mark can move a mark, so it is left out.
    if (at !== last) {
      nodes.push(document.createTextNode(text.slice(at, last)));
    }
    code.replaceChildren(...nodes);
  }

  function follow() {
    code.style.transform = `translate(${-textarea.scrollLeft}px, ${-textarea.scrollTop}px)`;
  }
  // A tip left in place would point at text that has scrolled away.
  textarea.addEventListener("scroll", () => hide());

  // -- The tip ---------------------------------------------------------------

  let shownFor = null; // "pointer" or "caret" while the tip shows
  let shownKey = "";

  function show(covering, anchor, by) {
    const key = `${by}:${covering.join(",")}`;
    if (!tip.hidden && key === shownKey) {
      return;
    }
    const box = textarea.getBoundingClientRect();
    if (anchor.bottom < box.top || anchor.top > box.bottom) {
      return hide();
    }
    const lines = covering.flatMap((i) => [[marks[i].severity, marks[i].label], ...marks[i].notes.map((n) => ["note", n])]);
    tip.replaceChildren(
      ...lines.map(([kind, line]) => {
        const element = document.createElement("div");
        element.className = kind;
        element.textContent = line;
        return element;
      }),
    );
    tip.hidden = false;
    shownFor = by;
    shownKey = key;
    // Below the stretch, or above it where there is no room below; never
    // past the window's sides.
    const height = tip.offsetHeight;
    const width = tip.offsetWidth;
    const below = anchor.bottom + 4;
    tip.style.top = `${below + height > window.innerHeight ? Math.max(0, anchor.top - 4 - height) : below}px`;
    tip.style.left = `${Math.max(0, Math.min(anchor.left, window.innerWidth - width))}px`;
    if (by === "caret") {
      spoken.textContent = lines.map(([, line]) => line).join(". ");
    }
  }

  function hide() {
    tip.hidden = true;
    shownFor = null;
    shownKey = "";
    spoken.textContent = "";
  }

  // Returns the marked stretch whose box holds the point, and that box.
  function stretchAt(x, y) {
    for (const stretch of stretches) {
      for (const box of stretch.element.getClientRects()) {
        if (x >= box.left && x <= box.right && y >= box.top && y <= box.bottom) {
          return [stretch, box];
        }
      }
    }
    return [null, null];
  }

  let hiding = null;
  const hideSoon = () => {
    clearTimeout(hiding);
    hiding = setTimeout(() => shownFor === "pointer" && hide(), 200);
  };
  textarea.addEventListener("mousemove", ({ clientX, clientY }) => {
    const [stretch, box] = stretchAt(clientX, clientY);
    if (stretch !== null) {
      clearTimeout(hiding);
      show(stretch.covering, box, "pointer");
    } else if (shownFor === "pointer") {
      hideSoon();
    }
  });
  textarea.addEventListener("mouseleave", () => shownFor === "pointer" && hideSoon());
  // The tip stays while the pointer is on it, so its text can be selected.
  tip.addEventListener("mouseenter", () => clearTimeout(hiding));
  tip.addEventListener("mouseleave", () => shownFor === "pointer" && hideSoon());

  // Shows the tip for the marks at the caret, or hides a tip the caret showed.
  function atCaret() {
    if (document.activeElement !== textarea || textarea.selectionStart !== textarea.selectionEnd) {
      return shownFor === "caret" && hide();
    }
    const caret = textarea.selectionStart;
    const covering = marks.flatMap((m, i) => (m.start <= caret && caret <= m.end ? [i] : []));
    const stretch = stretches.find((s) => s.start <= caret && caret <= s.end && s.covering.some((i) => covering.includes(i)));
    if (stretch === undefined) {
      return shownFor === "caret" && hide();
    }
    show(covering, stretch.element.getClientRects()[0], "caret");
  }
  for (const event of ["keyup", "mouseup", "focus"]) {
    textarea.addEventListener(event, atCaret);
  }
  textarea.addEventListener("blur", () => shownFor === "caret" && hide());

  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !tip.hidden) {
      hide();
    } else if (event.key === "F8" && !event.ctrlKey && !event.metaKey && !event.altKey && marks.length > 0) {
      event.preventDefault();
      jump(event.shiftKey ? -1 : 1);
    }
  });

  // Moves the caret to the start of the next mark after it, or with `-1` the
  // last one before it, wrapping around, and scrolls it into view.
  function jump(direction) {
    const starts = [...new Set(marks.map((m) => m.start))].sort((a, b) => a - b);
    const caret = textarea.selectionStart;
    const to =
      direction > 0
        ? (starts.find((s) => s > caret) ?? starts[0])
        : (starts.findLast((s) => s < caret) ?? starts[starts.length - 1]);
    textarea.setSelectionRange(to, to);
    const stretch = stretches.find((s) => s.start === to);
    if (stretch !== undefined) {
      const box = stretch.element.getClientRects()[0];
      const view = textarea.getBoundingClientRect();
      const pad = 24;
      if (box.top < view.top + pad || box.bottom > view.bottom - pad) {
        textarea.scrollTop += box.top - view.top - view.height / 3;
      }
      if (box.left < view.left + pad || box.right > view.right - pad) {
        textarea.scrollLeft += box.left - view.left - pad;
      }
    }
  }

  return {
    set(next) {
      marks = next;
      text = textarea.value;
      render();
      hide();
      atCaret();
    },
    changed(range) {
      marks = shiftMarks(marks, range);
      text = textarea.value;
      render();
      hide();
    },
    follow,
  };
}
