// Loaded in <head> on every page, so the theme is set before the page is drawn.

// Theme: the visitor's saved choice, else the system preference; the toggle in the nav switches and saves it.
let savedTheme = null;
try { savedTheme = localStorage.getItem("theme"); } catch {}
document.documentElement.dataset.theme = savedTheme || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
document.addEventListener("click", (event) => {
  if (!event.target.closest("#theme")) return;
  const theme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch {}
});

// Index: filter rows by free text and by one exact rule or evidence-kind tag (a chip, a card or a #tag link),
// and sort by a column, numerically for .num columns, using a cell's data-key when it has one.
document.addEventListener("DOMContentLoaded", () => {
  const tbody = document.querySelector("#blocks tbody");
  if (!tbody) return;
  const input = document.getElementById("filter");
  const rows = [...tbody.rows];
  const toggles = [...document.querySelectorAll("[data-filter]")];
  const shown = document.getElementById("shown");
  let tag = decodeURIComponent(location.hash.slice(1));
  function apply() {
    const q = input.value.trim().toLowerCase();
    let count = 0;
    for (const row of rows) {
      row.hidden = (tag !== "" && !row.dataset.tags.split(" ").includes(tag)) || !row.dataset.search.includes(q);
      if (!row.hidden) count++;
    }
    for (const toggle of toggles) toggle.setAttribute("aria-pressed", toggle.dataset.filter === tag);
    shown.textContent = count;
  }
  input.addEventListener("input", apply);
  for (const toggle of toggles) {
    toggle.addEventListener("click", () => {
      tag = tag === toggle.dataset.filter ? "" : toggle.dataset.filter;
      history.replaceState(null, "", tag ? "#" + tag : location.pathname);
      apply();
    });
  }
  document.getElementById("filters").hidden = false;
  apply();

  const headers = [...document.querySelectorAll("#blocks th")];
  headers.forEach((th, column) => {
    th.querySelector("button").addEventListener("click", () => {
      const ascending = th.getAttribute("aria-sort") !== "ascending";
      const numeric = th.classList.contains("num");
      const key = (row) => row.cells[column].dataset.key ?? row.cells[column].textContent;
      rows.sort((a, b) => {
        const x = key(a), y = key(b);
        if (x === "" || y === "") return (x === "") - (y === "");
        const order = numeric ? x - y : x.localeCompare(y);
        return ascending ? order : -order;
      });
      tbody.append(...rows);
      for (const other of headers) other.removeAttribute("aria-sort");
      th.setAttribute("aria-sort", ascending ? "ascending" : "descending");
    });
  });
});
