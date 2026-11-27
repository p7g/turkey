// The keyboard shortcuts of a session's actions, which work wherever the
// focus is on the page, the editor included:
//
//   Ctrl+Enter, or Cmd+Enter on a Mac    Run
//   Shift+Alt+F, Shift+Option+F on a Mac Format
//
// Many editors and playgrounds use these keys for these actions, so a
// reader's hands may know them already. Neither is a browser's own shortcut.
// The page takes both before the editor sees them, so Option+Shift+F, which
// types a letter on a Mac, types nothing.
//
// No DOM, so node loads it.

/**
 * Returns the action a `keydown` event asks for, "run" or "format", or null
 * for any other key. A key held down asks once, and a key that an input
 * method is composing with asks nothing.
 */
export function shortcutFor(event) {
  if (event.repeat || event.isComposing) {
    return null;
  }
  const command = event.ctrlKey || event.metaKey;
  if (event.key === "Enter" && command && !event.shiftKey && !event.altKey) {
    return "run";
  }
  // By the key's place, not its character: Option+Shift+F types a letter on
  // a Mac, which `key` would hold instead of "F".
  if (event.code === "KeyF" && event.shiftKey && event.altKey && !command) {
    return "format";
  }
  return null;
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
