// The tabs over a session's output pane. The pane a run writes to is the
// first tab, "Output", and every other view of the program, such as its
// types, is a tab beside it, with a panel of its own after the output pane.
// A view that is not shown takes no room, so another view costs the page a
// tab and nothing else.

/**
 * Puts tabs over `output`, the output pane, which must have an `id`: one for
 * it, labelled "Output", then one for each `[id, label]` pair in `views`,
 * each with an empty panel of that `id` after the output pane, hidden. Shows
 * the output pane.
 *
 * Returns `{ panel(id), select(id), selected() }`: `panel` returns the panel
 * of the view `id`, `select` shows the pane or panel `id`, by its tab, and
 * `selected` returns the `id` shown.
 */
export function attachPanes(output, views) {
  const list = document.createElement("div");
  list.className = "panes";
  list.setAttribute("role", "tablist");
  list.setAttribute("aria-label", "Results");
  const entries = [[output.id, "Output", output]];
  let after = output;
  for (const [id, label] of views) {
    const panel = document.createElement("div");
    panel.id = id;
    panel.className = "view";
    panel.hidden = true;
    after.after(panel);
    after = panel;
    entries.push([id, label, panel]);
  }
  const tabs = entries.map(([id, label, panel]) => {
    const tab = document.createElement("button");
    tab.type = "button";
    tab.id = `${id}-tab`;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", id);
    tab.textContent = label;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tab.id);
    panel.removeAttribute("aria-label");
    tab.addEventListener("click", () => {
      select(id);
    });
    return tab;
  });
  list.append(...tabs);
  output.before(list);

  let shown = output.id;
  function select(id) {
    shown = id;
    entries.forEach(([each, , panel], i) => {
      const selected = each === id;
      panel.hidden = !selected;
      tabs[i].setAttribute("aria-selected", String(selected));
      tabs[i].tabIndex = selected ? 0 : -1;
    });
  }

  // The arrow keys move along the tabs and show the one they reach, and
  // Home and End go to the first and the last.
  list.addEventListener("keydown", (event) => {
    const at = tabs.indexOf(event.target);
    const to = {
      ArrowLeft: (at - 1 + tabs.length) % tabs.length,
      ArrowRight: (at + 1) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    }[event.key];
    if (at < 0 || to === undefined) {
      return;
    }
    event.preventDefault();
    select(entries[to][0]);
    tabs[to].focus();
  });

  select(output.id);
  return {
    panel: (id) => entries.find(([each]) => each === id)[2],
    select,
    selected: () => shown,
  };
}
