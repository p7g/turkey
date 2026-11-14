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
    const rank = e => e.name === q ? 0 : e.name.startsWith(q) ? 1 : e.name.includes(q) ? 2 : 3;
    const all = entries.filter(e => e.text.includes(q)).sort((a, b) => rank(a) - rank(b));
    const hits = all.slice(0, 200);
    const shown = all.length > hits.length ? `, the first ${hits.length} shown` : '';
    main.innerHTML = `<h1>${all.length} results${shown}</h1>` + hits.map(e => e.html).join('');
  }
  box.addEventListener('input', () => search(box.value));
  window.addEventListener('hashchange', () => {
    box.value = '';
    search('');
    let id = location.hash.slice(1);
    try { id = decodeURIComponent(id); } catch (e) { /* typed by hand, not encoded */ }
    const target = document.getElementById(id);
    if (target) target.scrollIntoView();
  });
})();
