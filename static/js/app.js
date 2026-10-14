(function () {
  "use strict";

  var map, marker, resolveTimer = null, districtsByState = null, currentState = null, pendingResolve = false;

  var el = function (id) { return document.getElementById(id); };
  var strings = (window.I18N && window.I18N.strings) || {};
  var chipLabels = strings.chips || {};
  function t(key, fallback) { return strings[key] || fallback || ""; }
  var statusEl, summaryEl, predictBtn, monthSelect;

  function init() {
    statusEl = el("resolve-status");
    summaryEl = el("resolved-summary");
    predictBtn = el("btn-predict");
    monthSelect = el("month-select");

    map = L.map("map").setView([22.5, 80.5], 5);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(map);

    marker = L.marker([22.5, 80.5], { draggable: true, autoPan: true }).addTo(map);

    map.on("click", function (event) { movePin(event.latlng.lat, event.latlng.lng); });
    marker.on("dragend", function () {
      var pos = marker.getLatLng();
      movePin(pos.lat, pos.lng);
    });

    el("btn-geo").addEventListener("click", locateMe);
    el("btn-toggle-district").addEventListener("click", toggleDistrictPicker);
    el("state-select").addEventListener("change", onStateChange);
    el("district-select").addEventListener("change", onDistrictChange);
    monthSelect.addEventListener("change", function () { scheduleResolve(); });

    ["input-n", "input-p", "input-k", "input-ph"].forEach(function (id) {
      el(id).addEventListener("input", function () { this.dataset.dirty = "1"; });
    });

    loadDistricts();
    if (navigator.geolocation) {
      statusEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>' + t("gpsWait", "Getting your GPS position...");
      navigator.geolocation.getCurrentPosition(
        function (position) {
          movePin(position.coords.latitude, position.coords.longitude);
          map.setView([position.coords.latitude, position.coords.longitude], 11);
        },
        function () {
          statusEl.innerHTML = '<i class="bi bi-pin-angle me-1"></i>' + t("gpsDenied", "Location permission denied - click the map instead.");
        },
        { timeout: 8000 }
      );
    }
  }

  function toggleDistrictPicker() {
    var picker = el("district-picker");
    picker.classList.toggle("d-none");
    if (!picker.classList.contains("d-none") && !districtsByState) {
      statusEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>' + t("districtLoad", "Loading district list...");
      loadDistricts();
    }
  }

  function loadDistricts() {
    if (districtsByState) return Promise.resolve(districtsByState);
    return fetch("/api/districts")
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        districtsByState = {};
        (payload.data || []).forEach(function (row) {
          var key = row.state;
          if (!districtsByState[key]) districtsByState[key] = [];
          districtsByState[key].push(row.district);
        });
        var select = el("state-select");
        Object.keys(districtsByState).sort().forEach(function (state) {
          var option = document.createElement("option");
          option.value = state;
          option.textContent = state;
          select.appendChild(option);
        });
        return districtsByState;
      })
      .catch(function () {
        statusEl.textContent = t("districtFail", "Could not load the district list.");
        return null;
      });
  }

  function onStateChange() {
    currentState = el("state-select").value;
    var districtSelect = el("district-select");
    var districtPlaceholder = (window.I18N && window.I18N.language === "mr") ? "-- जिल्हा निवडा --" : "-- Select District --";
    districtSelect.innerHTML = '<option value="">' + districtPlaceholder + '</option>';
    districtSelect.disabled = !currentState;
    if (!currentState || !districtsByState) return;
    districtsByState[currentState].forEach(function (district) {
      var option = document.createElement("option");
      option.value = district;
      option.textContent = district;
      districtSelect.appendChild(option);
    });
    clearPinFields();
  }

  function onDistrictChange() {
    var district = el("district-select").value;
    if (!district || !currentState) return;
    el("field-district").value = district;
    el("field-state").value = currentState;
    el("field-lat").value = "";
    el("field-lon").value = "";
    clearChips();
    statusEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>' + t("resolvingDistrict", "Resolving") + " " + district + "...";
    fetch(resolveUrl({ district: district, state: currentState }))
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        if (!payload.ok) return showError(payload.error || t("resolveFail", "Could not resolve that district."));
        applyResolved(payload.data, null, null);
      })
      .catch(function () { showError(t("networkDistrictFail", "Network error while resolving the district.")); });
  }

  function locateMe() {
    if (!navigator.geolocation) return;
    var button = el("btn-geo");
    button.disabled = true;
    statusEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>' + t("gpsWait", "Getting your GPS position...");
    navigator.geolocation.getCurrentPosition(
      function (position) {
        button.disabled = false;
        movePin(position.coords.latitude, position.coords.longitude);
        map.setView([position.coords.latitude, position.coords.longitude], 12);
      },
      function () {
        button.disabled = false;
        statusEl.textContent = t("gpsFailed", "Could not read your GPS position - click the map instead.");
      },
      { timeout: 10000, enableHighAccuracy: true }
    );
  }

  function movePin(lat, lon) {
    marker.setLatLng([lat, lon]);
    el("field-lat").value = lat.toFixed(5);
    el("field-lon").value = lon.toFixed(5);
    el("field-district").value = "";
    el("field-state").value = "";
    scheduleResolve();
  }

  function scheduleResolve() {
    if (!el("field-lat").value && !el("field-district").value) return;
    statusEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>' +
      t("resolving", "Resolving location...");
    predictBtn.disabled = true;
    clearTimeout(resolveTimer);
    resolveTimer = setTimeout(doResolve, 550);
  }

  function resolveUrl(params) {
    var query = [];
    Object.keys(params).forEach(function (key) {
      if (params[key] !== "" && params[key] !== null && params[key] !== undefined) {
        query.push(encodeURIComponent(key) + "=" + encodeURIComponent(params[key]));
      }
    });
    return "/api/resolve?" + query.join("&");
  }

  function doResolve() {
    var lat = el("field-lat").value;
    var lon = el("field-lon").value;
    var district = el("field-district").value;
    var params = { month: monthSelect.value };
    if (lat && lon) { params.lat = lat; params.lon = lon; }
    else if (district) { params.district = district; params.state = el("field-state").value; }
    else return;

    pendingResolve = true;
    fetch(resolveUrl(params))
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        pendingResolve = false;
        if (!payload.ok) return showError(payload.error || t("resolveFail", "Could not resolve this location."));
        applyResolved(payload.data, lat, lon);
      })
      .catch(function () { pendingResolve = false; showError(t("networkFail", "Network error while resolving the location.")); });
  }

  function showError(message) {
    statusEl.innerHTML = '<span class="text-danger"><i class="bi bi-exclamation-triangle me-1"></i>' + escapeHtml(message) + "</span>";
    predictBtn.disabled = true;
  }

  function applyResolved(data, lat, lon) {
    var location = data.location || {};
    var soil = data.soil || {};
    var climate = data.climate || {};
    var available = soil.available || {};

    if (lat && lon) {
      el("field-lat").value = lat;
      el("field-lon").value = lon;
    }
    el("field-district").value = location.district || "";
    el("field-state").value = location.state || "";

    statusEl.innerHTML = '<span class="text-success"><i class="bi bi-check-circle me-1"></i>' + t("locationResolved", "Location resolved.") + '</span>';
    summaryEl.classList.remove("d-none");
    el("sum-place").textContent = [location.village, location.district, location.state].filter(Boolean).join(", ") || t("selectedPoint", "Selected point");
    el("sum-coords").textContent = data.lat.toFixed(4) + ", " + data.lon.toFixed(4);
    el("sum-month").textContent = monthSelect.options[monthSelect.selectedIndex].text;

    var chips = [
      [chipLabels.pH || "pH", soil.ph],
      [chipLabels.texture || "Texture", soil.texture_class],
      ["N", available.n],
      ["P", available.p],
      ["K", available.k],
      [chipLabels.temp || "Temp", climate.temp_c !== null && climate.temp_c !== undefined ? climate.temp_c + " °C" : null],
      [chipLabels.humidity || "Humidity", climate.humidity_pct !== null && climate.humidity_pct !== undefined ? climate.humidity_pct + " %" : null],
      [chipLabels.rainNormal || "Rain normal", climate.rainfall_normal_mm !== null && climate.rainfall_normal_mm !== undefined ? climate.rainfall_normal_mm + " mm" : null],
      [chipLabels.rainActual || "Rain actual", climate.rainfall_actual_mm !== null && climate.rainfall_actual_mm !== undefined ? climate.rainfall_actual_mm + " mm" : null],
    ];
    var chipHost = el("sum-chips");
    chipHost.innerHTML = "";
    chips.forEach(function (pair) {
      if (pair[1] === null || pair[1] === undefined || pair[1] === "") return;
      var badge = document.createElement("span");
      badge.className = "badge text-bg-light border";
      badge.textContent = pair[0] + ": " + pair[1];
      chipHost.appendChild(badge);
    });

    var warnings = data.warnings || [];
    var warnEl = el("sum-warnings");
    if (warnings.length) {
      warnEl.classList.remove("d-none");
      warnEl.innerHTML = warnings.map(function (text) { return '<i class="bi bi-info-circle me-1"></i>' + escapeHtml(text); }).join("<br>");
    } else {
      warnEl.classList.add("d-none");
      warnEl.innerHTML = "";
    }

    prefillSoil("input-n", available.n, available.measured);
    prefillSoil("input-p", available.p, available.measured);
    prefillSoil("input-k", available.k, available.measured);
    prefillSoil("input-ph", soil.ph, soil.ph_source && (soil.ph_source.indexOf("Farmer") === 0 || soil.ph_source.indexOf("\u0936\u0947\u0924\u0915\u0931\u094d\u092f\u093e\u0928\u0947") === 0));

    if (lat && lon) {
      marker.setLatLng([data.lat, data.lon]);
    }
    predictBtn.disabled = false;
  }

  function prefillSoil(id, value, measured) {
    var input = el(id);
    if (value === null || value === undefined || value === "") return;
    if (input.dataset.dirty === "1") return;
    input.value = Math.round(Number(value) * 10) / 10;
    if (measured) input.classList.add("border-success");
  }

  function clearPinFields() {
    el("field-lat").value = "";
    el("field-lon").value = "";
    el("field-district").value = "";
    el("field-state").value = "";
    summaryEl.classList.add("d-none");
    predictBtn.disabled = true;
    statusEl.innerHTML = '<i class="bi bi-pin-angle me-1"></i>' + t("selectDistrict", "Select a district above.");
  }

  function clearChips() {
    el("sum-chips").innerHTML = "";
    summaryEl.classList.add("d-none");
  }

  function escapeHtml(text) {
    var node = document.createElement("span");
    node.textContent = String(text);
    return node.innerHTML;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
