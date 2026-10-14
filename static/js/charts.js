(function () {
  "use strict";

  if (!window.Chart) return;

  var registry = [];

  function cssVar(name, fallback) {
    var value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (value && value.trim()) || fallback;
  }

  function palette() {
    return {
      ink: cssVar("--ink", "#0e1f17"),
      muted: cssVar("--muted", "#667a70"),
      line: cssVar("--line", "rgba(14,31,23,0.10)"),
      brand: cssVar("--brand-600", "#16a34a"),
      brand2: cssVar("--brand-400", "#4ade80"),
      sun: cssVar("--sun-500", "#f59e0b"),
      sky: cssVar("--sky-600", "#0284c7"),
      clay: cssVar("--clay-600", "#dc2626"),
      surface: cssVar("--surface", "#ffffff"),
      grid: cssVar("--chart-grid", "rgba(14,31,23,0.08)"),
    };
  }

  function rgba(hex, alpha) {
    if (!hex || hex[0] !== "#") return hex;
    var value = hex.slice(1);
    if (value.length === 3) value = value.split("").map(function (c) { return c + c; }).join("");
    var num = parseInt(value, 16);
    return "rgba(" + ((num >> 16) & 255) + "," + ((num >> 8) & 255) + "," + (num & 255) + "," + alpha + ")";
  }

  function baseOptions(extra) {
    var c = palette();
    var options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 550 },
      plugins: {
        legend: { display: false, labels: { color: c.ink, font: { family: "Plus Jakarta Sans", weight: "600" } } },
        tooltip: {
          backgroundColor: "rgba(14,31,23,0.92)",
          titleFont: { family: "Plus Jakarta Sans" },
          bodyFont: { family: "Plus Jakarta Sans" },
          padding: 10,
          cornerRadius: 8,
        },
      },
      scales: {
        x: {
          ticks: { color: c.muted, font: { family: "Plus Jakarta Sans", size: 11 } },
          grid: { color: c.grid, drawBorder: false },
        },
        y: {
          beginAtZero: true,
          ticks: { color: c.muted, font: { family: "Plus Jakarta Sans", size: 11 } },
          grid: { color: c.grid, drawBorder: false },
        },
      },
    };
    return Object.assign(options, extra || {});
  }

  function gradient(context, top, bottom) {
    var chart = context.chart;
    var area = chart.chartArea;
    if (!area) return top;
    var fill = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    fill.addColorStop(0, top);
    fill.addColorStop(1, bottom);
    return fill;
  }

  function register(config) {
    var instance = new Chart(config);
    registry.push(instance);
    return instance;
  }

  function make(type) {
    return function (canvas, data, options) {
      if (!canvas) return null;
      return register({
        type: type,
        data: data,
        options: options || baseOptions(),
      });
    };
  }

  function retheme() {
    registry.forEach(function (chart) {
      try {
        chart.destroy();
        chart.update();
      } catch (error) {
        /* ignore charts removed from the DOM */
      }
    });
  }

  window.addEventListener("vk:theme", function () {
    // Simplest reliable approach for a small dashboard: reload chart data colours.
    registry = registry.filter(function (chart) { return chart && chart.canvas && document.body.contains(chart.canvas); });
  });

  window.VKCharts = {
    bar: make("bar"),
    line: make("line"),
    doughnut: make("doughnut"),
    radar: make("radar"),
    palette: palette,
    options: baseOptions,
    gradient: gradient,
    rgba: rgba,
    retheme: retheme,
  };
})();
