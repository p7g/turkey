// The source editor: a `<textarea>` whose text is transparent, over a
// `<pre><code>` that shows the same text highlighted. The textarea does all
// the editing, selection and scrolling, and the code element only follows it.
//
// The HTML comes from the compiler, `turkey highlight`, in a Worker, so it
// arrives a few milliseconds after the text it was made from. Until then the
// overlay is patched in place: on every edit, the stretch that changed is
// replaced by plain text, and the spans around it keep their colours. The
// overlay's text therefore always equals the textarea's, which is what keeps
// the two aligned. The HTML is shown only if the text has not changed since
// it was asked for.
//
// `changedRange` needs no DOM, so node loads this module.

/**
 * Starts highlighting `textarea` into `code`, a `display: block` element in a
 * `<pre>` beneath it with the same font, padding and `tab-size`.
 * `requestHighlight(source)` is called with the text, at most once per
 * `delay` milliseconds of typing, and the caller hands the compiler's answer
 * to `highlighted(source, html)` on the object this returns. Call `changed()`
 * on it after setting `textarea.value` from code, which fires no event.
 *
 * Tab inserts a tab. Escape and then Tab moves the focus on, as in other
 * editors that take the Tab key.
 */
export function attachEditor(textarea, code, requestHighlight, { delay = 120 } = {}) {
  let timer = null;
  // The textarea scrolls, and the code element is moved to match. Moving it
  // with a transform, not by scrolling the `<pre>`, keeps the two aligned at
  // the very end too, where a textarea's scrollbars would let it scroll
  // further than a `<pre>` without them.
  const follow = () => {
    code.style.transform = `translate(${-textarea.scrollLeft}px, ${-textarea.scrollTop}px)`;
  };
  const changed = () => {
    patch(code, textarea.value);
    follow();
    clearTimeout(timer);
    timer = setTimeout(() => requestHighlight(textarea.value), delay);
  };
  let escaped = false;
  textarea.addEventListener("keydown", (event) => {
    const plain = !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey;
    if (event.key === "Tab" && plain && !escaped) {
      event.preventDefault();
      insert(textarea, "\t");
    }
    escaped = event.key === "Escape";
  });
  textarea.addEventListener("input", changed);
  textarea.addEventListener("scroll", follow);
  changed();
  return {
    changed,
    highlighted(source, html) {
      if (html !== null && source === textarea.value) {
        // Only the compiler's output is set as HTML: `turkey highlight`
        // escapes `<`, `>`, `&` and `"` in the source, and its own markup is
        // `<span class="...">` and `</span>`.
        code.innerHTML = html;
        follow();
      }
    },
  };
}

/**
 * Replaces the text of `textarea` with `text` as one edit that undo reverts,
 * and fires `input`. Keeps the caret's offset and the scroll position where
 * the new text allows.
 */
export function replaceText(textarea, text) {
  const caret = Math.min(textarea.selectionStart, text.length);
  const { scrollTop, scrollLeft } = textarea;
  textarea.focus();
  textarea.select();
  insert(textarea, text);
  textarea.setSelectionRange(caret, caret);
  textarea.scrollTop = scrollTop;
  textarea.scrollLeft = scrollLeft;
}

// Inserts `text` over the selection. `execCommand` is deprecated, but it is
// the one way to edit a textarea that the browser's undo stack records; where
// it is missing, the edit happens without undo.
function insert(textarea, text) {
  if (!document.execCommand("insertText", false, text)) {
    textarea.setRangeText(text, textarea.selectionStart, textarea.selectionEnd, "end");
    textarea.dispatchEvent(new Event("input"));
  }
}

/**
 * Returns where `next` differs from `old`: `{ start, oldEnd, newEnd }`, such
 * that `next` is `old` with `old.slice(start, oldEnd)` replaced by
 * `next.slice(start, newEnd)`. The stretch is the shortest such, except that
 * it never splits a surrogate pair.
 */
export function changedRange(old, next) {
  const most = Math.min(old.length, next.length);
  let start = 0;
  while (start < most && old.charCodeAt(start) === next.charCodeAt(start)) {
    start++;
  }
  if (start > 0 && start < old.length && isHigh(old.charCodeAt(start - 1))) {
    start--;
  }
  let same = 0; // the length of the common suffix, after `start` in both
  while (
    same < most - start &&
    old.charCodeAt(old.length - 1 - same) === next.charCodeAt(next.length - 1 - same)
  ) {
    same++;
  }
  if (same > 0 && isLow(old.charCodeAt(old.length - same))) {
    same--;
  }
  return { start, oldEnd: old.length - same, newEnd: next.length - same };
}

const isHigh = (unit) => unit >= 0xd800 && unit <= 0xdbff;
const isLow = (unit) => unit >= 0xdc00 && unit <= 0xdfff;

// Makes the text of `code` equal `text` by replacing only the stretch that
// differs with a plain text node.
function patch(code, text) {
  const old = code.textContent;
  if (old === text) {
    return;
  }
  const { start, oldEnd, newEnd } = changedRange(old, text);
  const range = document.createRange();
  range.setStart(...locate(code, start));
  range.setEnd(...locate(code, oldEnd));
  range.deleteContents();
  if (newEnd > start) {
    range.insertNode(document.createTextNode(text.slice(start, newEnd)));
  }
}

// Returns the text node and offset in it at `offset` in the text of `code`.
// An offset between two nodes is the end of the first, so typed text joins
// the token before it until the next highlight.
function locate(code, offset) {
  const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
  let left = offset;
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    if (left <= node.length) {
      return [node, left];
    }
    left -= node.length;
  }
  return [code, code.childNodes.length];
}
