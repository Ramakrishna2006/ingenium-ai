/* Analytics charts. Data comes from a JSON block rendered by the server.
   If Chart.js could not load (offline), the "View as table" sections remain the source of truth. */
(function () {
  "use strict";
  const el = document.getElementById("analytics-data");
  if (!el) return;
  if (!window.Chart) {
    document.querySelectorAll(".chart-wrap").forEach((w) => {
      w.outerHTML = '<p class="small-muted">Charts need an internet connection to load the chart library. The data is in the table below.</p>';
    });
    document.querySelectorAll("details > summary").forEach((s) => { if (s.textContent === "View as table") s.parentElement.open = true; });
    return;
  }
  const data = JSON.parse(el.textContent);
  const INDIGO = "#4f46e5", TEAL = "#0d9488", INK = "#5b6478", GRID = "#e5e8ef";

  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.color = INK;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.plugins.tooltip.backgroundColor = "#0f172a";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  const axis = { grid: { color: GRID, drawTicks: false }, border: { display: false }, ticks: { padding: 6 } };

  const vol = document.getElementById("chart-volume");
  if (vol) {
    new Chart(vol, {
      type: "line",
      data: {
        labels: data.daily.map((d) => d.date.slice(5)),
        datasets: [{ label: "Applications", data: data.daily.map((d) => d.count), borderColor: INDIGO, borderWidth: 2,
          backgroundColor: "rgba(79,70,229,.08)", fill: true, tension: 0.25, pointRadius: 0, pointHoverRadius: 5 }],
      },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false } },
        scales: { x: { ...axis, grid: { display: false }, ticks: { maxTicksLimit: 10 } },
                  y: { ...axis, beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  const fun = document.getElementById("chart-funnel");
  if (fun) {
    new Chart(fun, {
      type: "bar",
      data: { labels: data.funnel.map((f) => f.label),
        datasets: [{ label: "Applications", data: data.funnel.map((f) => f.count), backgroundColor: INDIGO,
          borderRadius: 4, borderSkipped: "start", barPercentage: 0.7, categoryPercentage: 0.9 }] },
      options: {
        indexAxis: "y",
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: (ctx) => {
            const f = data.funnel[ctx.dataIndex];
            return ` ${f.count} applications` + (f.conversion !== null ? ` (${f.conversion}% of applied)` : "");
          } } } },
        scales: { x: { ...axis, beginAtZero: true, ticks: { precision: 0 } }, y: { ...axis, grid: { display: false } } },
      },
    });
  }

  const vac = document.getElementById("chart-vacancies");
  if (vac) {
    new Chart(vac, {
      type: "bar",
      data: {
        labels: data.vacancies.map((v) => v.department),
        datasets: [
          { label: "Published", data: data.vacancies.map((v) => v.published), backgroundColor: INDIGO, borderRadius: 4,
            borderColor: "#fff", borderWidth: { top: 2 } },
          { label: "Draft, paused or closed", data: data.vacancies.map((v) => v.total - v.published), backgroundColor: TEAL,
            borderRadius: 4, borderColor: "#fff", borderWidth: { top: 2 } },
        ],
      },
      options: {
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12, boxHeight: 12, useBorderRadius: true, borderRadius: 3 } } },
        interaction: { mode: "index", intersect: false },
        scales: { x: { ...axis, stacked: true, grid: { display: false } }, y: { ...axis, stacked: true, beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }
})();
