(function () {
  "use strict";

  var tasks = [];
  var root, gridEl, titleEl, detailEl;
  var year, month, selectedDate = null;
  var todayStr = null;
  var lang = (window.I18N && window.I18N.language) || "en";
  var strings = (window.I18N && window.I18N.strings) || {};
  function t(key, fallback) { return strings[key] || fallback || ""; }
  var monthNames = (window.I18N && window.I18N.monthNames) || [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
  ];
  var weekdays = (window.I18N && window.I18N.weekdays)
    || ((lang === "mr")
      ? ["रवि", "सोम", "मंगळ", "बुध", "गुरु", "शुक्र", "शनि"]
      : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]);

  function el(id) { return document.getElementById(id); }

  function init() {
    root = el("cal-root");
    if (!root) return;
    gridEl = el("cal-grid");
    titleEl = el("cal-title");
    detailEl = el("cal-detail");

    try {
      tasks = JSON.parse(el("farm-tasks").textContent || "[]");
    } catch (err) {
      tasks = [];
    }
    tasks = tasks.filter(function (task) { return !!task.date; });

    year = parseInt(root.dataset.year, 10) || new Date().getFullYear();
    month = parseInt(root.dataset.month, 10) || new Date().getMonth() + 1;
    todayStr = root.dataset.today || "";

    el("cal-prev").addEventListener("click", function () { step(-1); });
    el("cal-next").addEventListener("click", function () { step(1); });
    el("cal-today").addEventListener("click", function () {
      var parts = todayStr.split("-");
      year = parseInt(parts[0], 10);
      month = parseInt(parts[1], 10);
      selectedDate = null;
      render();
    });

    render();
  }

  function step(delta) {
    month += delta;
    if (month > 12) { month = 1; year += 1; }
    if (month < 1) { month = 12; year -= 1; }
    selectedDate = null;
    render();
  }

  function iso(y, m, d) {
    var mm = m < 10 ? "0" + m : "" + m;
    var dd = d < 10 ? "0" + d : "" + d;
    return y + "-" + mm + "-" + dd;
  }

  function byDate() {
    var map = {};
    tasks.forEach(function (task) {
      if (!map[task.date]) map[task.date] = [];
      map[task.date].push(task);
    });
    return map;
  }

  function render() {
    var map = byDate();
    titleEl.textContent = monthNames[month - 1] + " " + year;
    gridEl.innerHTML = "";

    var firstDay = new Date(year, month - 1, 1).getDay();
    var daysInMonth = new Date(year, month, 0).getDate();
    var daysPrev = new Date(year, month - 1, 0).getDate();
    var totalCells = Math.ceil((firstDay + daysInMonth) / 7) * 7;

    for (var i = 0; i < totalCells; i += 1) {
      var dayNum, cellMonth = month, cellYear = year, muted = false;
      if (i < firstDay) {
        dayNum = daysPrev - firstDay + i + 1;
        cellMonth = month - 1; muted = true;
        if (cellMonth < 1) { cellMonth = 12; cellYear = year - 1; }
      } else if (i >= firstDay + daysInMonth) {
        dayNum = i - (firstDay + daysInMonth) + 1;
        cellMonth = month + 1; muted = true;
        if (cellMonth > 12) { cellMonth = 1; cellYear = year + 1; }
      } else {
        dayNum = i - firstDay + 1;
      }

      var date = iso(cellYear, cellMonth, dayNum);
      var dayTasks = map[date] || [];
      var cell = document.createElement("div");
      cell.className = "cal-day" + (muted ? " muted" : "") + (date === todayStr ? " today" : "") +
        (date === selectedDate ? " selected" : "");
      cell.dataset.date = date;

      var no = document.createElement("span");
      no.className = "cal-dayno";
      no.textContent = dayNum;
      cell.appendChild(no);

      if (dayTasks.length) {
        var count = document.createElement("span");
        count.className = "d-count";
        count.textContent = dayTasks.length;
        cell.appendChild(count);

        var dots = document.createElement("div");
        dots.className = "cal-dots";
        dayTasks.slice(0, 5).forEach(function (task) {
          var dot = document.createElement("i");
          dot.className = "cal-dot k-" + (task.kind || "other");
          dot.title = lang === "mr" ? task.title_mr || task.title_en : task.title_en;
          dots.appendChild(dot);
        });
        cell.appendChild(dots);
      }

      cell.addEventListener("click", onDayClick);
      gridEl.appendChild(cell);
    }

    if (selectedDate) showDetail(selectedDate);
    else detailEl.hidden = true;
  }

  function onDayClick(event) {
    selectedDate = event.currentTarget.dataset.date;
    render();
  }

  function showDetail(date) {
    var map = byDate();
    var dayTasks = map[date] || [];
    detailEl.hidden = false;
    detailEl.innerHTML = "";

    var heading = document.createElement("h4");
    var d = new Date(date + "T00:00:00");
    var label = isNaN(d.getTime()) ? date : (weekdays[d.getDay()] + ", " + d.getDate() + " " + monthNames[d.getMonth()] + " " + d.getFullYear());
    heading.textContent = label;
    detailEl.appendChild(heading);

    if (!dayTasks.length) {
      var empty = document.createElement("div");
      empty.className = "small text-muted";
      empty.textContent = t("noWork", "No work scheduled on this day.");
      detailEl.appendChild(empty);
      return;
    }

    dayTasks.forEach(function (task) {
      var row = document.createElement("div");
      row.className = "day-task" + (task.status === "done" ? " done" : "");

      var bar = document.createElement("span");
      bar.className = "k-bar k-" + (task.kind || "other");
      row.appendChild(bar);

      var body = document.createElement("div");
      body.className = "grow";

      var title = document.createElement("div");
      title.className = "t-title";
      title.textContent = lang === "mr" ? task.title_mr || task.title_en : task.title_en;
      body.appendChild(title);

      var detail = document.createElement("div");
      detail.className = "t-detail";
      detail.textContent = lang === "mr" ? task.detail_mr || task.detail_en || "" : task.detail_en || "";
      body.appendChild(detail);

      if (task.source === "ai") {
        var ai = document.createElement("span");
        ai.className = "mini-chip ai";
        ai.textContent = "Gemini";
        body.appendChild(ai);
      }

      row.appendChild(body);

      var toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "tick";
      toggle.title = task.status === "done" ? t("movePending", "Move back to pending") : t("markDone", "Mark done");
      toggle.innerHTML = task.status === "done"
        ? '<i class="bi bi-arrow-counterclockwise"></i>'
        : '<i class="bi bi-check2"></i>';
      toggle.addEventListener("click", function () { toggleTask(task.id); });
      row.appendChild(toggle);

      detailEl.appendChild(row);
    });
  }

  function toggleTask(taskId) {
    var form = document.createElement("form");
    form.method = "post";
    form.action = "/tasks/" + taskId + "/toggle";

    var token = document.querySelector('meta[name="csrf-token"]');
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "csrf_token";
    input.value = token ? token.content : (window.CSRF_TOKEN || "");
    form.appendChild(input);

    document.body.appendChild(form);
    form.submit();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
