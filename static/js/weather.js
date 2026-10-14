/* Progressive 7-day forecast strip: fetches /api/forecast for any element with
   [data-forecast] and renders day cards + bilingual advisories. Fails silently. */
(function () {
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function lang() { return (window.I18N && window.I18N.language) || document.documentElement.lang || "en"; }
  function num(v) { return (v === null || v === undefined || isNaN(v)) ? "--" : Math.round(v); }

  function dayLabel(iso) {
    var d = new Date(iso + "T00:00:00");
    if (isNaN(d.getTime())) return iso;
    return DAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()];
  }

  function render(el, data) {
    var mr = lang() === "mr";
    var cur = data.current || {};
    var html = "";

    html += '<div class="weather-now">';
    html += '<i class="bi ' + (data.days[0] ? data.days[0].icon : "bi-cloud") + '"></i>';
    html += '<div><strong>' + num(cur.temp_c) + "\u00b0C</strong>";
    html += "<span>" + (mr ? "आता" : "now") + " \u00b7 " + num(cur.humidity_pct) + "% " + (mr ? "आर्द्रता" : "humidity") + "</span></div>";
    html += '<span class="weather-src">' + (data.source || "Open-Meteo") + "</span>";
    html += "</div>";

    html += '<div class="weather-days">';
    (data.days || []).forEach(function (d, i) {
      html += '<div class="weather-day' + (i === 0 ? " is-today" : "") + '">';
      html += "<span>" + (i === 0 ? (mr ? "\u0906\u091c" : "Today") : dayLabel(d.date)) + "</span>";
      html += '<i class="bi ' + d.icon + '"></i>';
      html += '<b>' + num(d.temp_max) + "\u00b0</b>";
      html += "<small>" + num(d.temp_min) + "\u00b0</small>";
      html += '<em><i class="bi bi-droplet"></i>' + num(d.precip_prob) + "%</em>";
      html += "</div>";
    });
    html += "</div>";

    if (data.advisories && data.advisories.length) {
      html += '<div class="weather-notes">';
      data.advisories.forEach(function (a) {
        html += '<div class="weather-note lvl-' + a.level + '"><strong>' +
          (mr ? a.title_mr : a.title_en) + "</strong><span>" + (mr ? a.detail_mr : a.detail_en) + "</span></div>";
      });
      html += "</div>";
    }

    el.innerHTML = html;
    el.classList.add("is-loaded");
  }

  function load(el) {
    var lat = el.getAttribute("data-lat");
    var lon = el.getAttribute("data-lon");
    if (!lat || !lon) return;
    var days = el.getAttribute("data-days") || 7;
    el.innerHTML = '<div class="weather-loading"><span class="spinner"></span> ' +
      (lang() === "mr" ? "\u0905\u0902\u0926\u093e\u091c \u0932\u094b\u0921 \u0939\u094b\u0924 \u0906\u0939\u0947\u2026" : "Loading forecast\u2026") + "</div>";
    fetch("/api/forecast?lat=" + encodeURIComponent(lat) + "&lon=" + encodeURIComponent(lon) + "&days=" + days)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.ok) { el.innerHTML = ""; el.style.display = "none"; return; }
        render(el, data);
      })
      .catch(function () { el.innerHTML = ""; el.style.display = "none"; });
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-forecast]"), load);
  }

  window.addEventListener("vk:language", init);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
