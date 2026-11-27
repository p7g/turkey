// The keyboard shortcuts of a session's actions, which work wherever the
// focus is on the page, the editor included:
//
//   Ctrl+Enter, or Cmd+Enter on a Mac    Run
//   Shift+Alt+F, Shift+Option+F on a Mac Format
//
// Many editors and playgrounds use these keys for these actions, so a
// reader's hands may know them already. Ctrl+Enter and Cmd+Enter are the
// browser's own on a link, where they open it in a new tab, so the page
// leaves them to the browser there. The page takes the keys before the
// editor sees them, so Option+Shift+F, which types a letter on a Mac, types
// nothing.
//
// No DOM, so node loads it.

/**
 * Returns the action a `keydown` event asks for, "run" or "format", or null
 * for any other key. A key that an input method is composing with asks
 * nothing. A key held down asks again on each repeat; the caller decides
 * whether a repeat acts.
 */
export function shortcutFor(event) {
  if (event.isComposing) {
    return null;
  }
  const command = event.ctrlKey || event.metaKey;
  if (event.key === "Enter" && command && !event.shiftKey && !event.altKey) {
    return "run";
  }
  if (event.shiftKey && event.altKey && !command && isF(event)) {
    return "format";
  }
  return null;
}

// Returns whether the key is F. A key that types a letter is F when the
// letter is, whatever the layout puts where. Option+Shift on a Mac types a
// symbol instead, and then only the key's place is left to go by: the key
// where QWERTY has F, which on most layouts, AZERTY among them, is F too,
// though on Dvorak it is U.
function isF(event) {
  return /^[a-z]$/i.test(event.key) ? event.key.toLowerCase() === "f" : event.code === "KeyF";
}

/**
 * Returns how a page names the shortcut for `action`, "run" or "format", to
 * a reader on a Mac when `mac` is true, and to anyone else otherwise:
 * `{ text, aria }`, where `text` is for a tooltip and `aria` is the value of
 * an `aria-keyshortcuts` attribute.
 */
export function shortcutText(action, mac) {
  if (action === "run") {
    return mac
      ? { text: "Cmd+Enter", aria: "Meta+Enter Control+Enter" }
      : { text: "Ctrl+Enter", aria: "Control+Enter Meta+Enter" };
  }
  return mac
    ? { text: "Shift+Option+F", aria: "Shift+Alt+F" }
    : { text: "Shift+Alt+F", aria: "Shift+Alt+F" };
}
