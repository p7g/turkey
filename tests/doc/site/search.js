'use strict';
(function () {
  const box = document.getElementById('search');
  const main = document.getElementById('main');
  const page = main.innerHTML;
  const entries = searchIndex.map(([name, text, html]) =>
    ({name: name.toLowerCase(), text: text.toLowerCase(), html}));
  // Exact names first, then names that start with the query, then names
  // that contain it, then any other match.
  function search(query) {
    const q = query.trim().toLowerCase();
    if (!q) { main.innerHTML = page; return; }
    const rank = e =>
      e.name === q ? 0 : e.name.startsWith(q) ? 1 : e.name.includes(q) ? 2 : 3;
    const all = entries.filter(e => e.text.includes(q))
      .sort((a, b) => rank(a) - rank(b));
    const hits = all.slice(0, 200);
    const shown = all.length > hits.length ? `, the first ${hits.length} shown` : '';
    main.innerHTML = `<h1>${all.length} results${shown}</h1>`
      + hits.map(e => e.html).join('');
  }
  // Brings back the page and scrolls to the element the address names.
  function reveal() {
    box.value = '';
    search('');
    let id = location.hash.slice(1);
    try { id = decodeURIComponent(id); } catch (e) { /* typed by hand, not encoded */ }
    const target = document.getElementById(id);
    if (target) target.scrollIntoView();
  }
  box.addEventListener('input', () => search(box.value));
  window.addEventListener('hashchange', reveal);
  // A result on this page whose address is the current one changes no hash.
  main.addEventListener('click', event => {
    const a = event.target.closest('a');
    if (a && box.value && a.href === location.href) {
      event.preventDefault();
      reveal();
    }
  });
})();
