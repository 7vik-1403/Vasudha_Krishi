(function () {
  "use strict";

  var STORAGE_KEY = "vk-theme";

  function preferred() {
    try {
      var saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved === "dark" || saved === "light") return saved;
    } catch (error) {
      /* storage disabled - fall through to the media query */
    }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.setAttribute("content", theme);
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      var dark = theme === "dark";
      button.setAttribute("aria-pressed", dark ? "true" : "false");
      var icon = button.querySelector("i");
      if (icon) icon.className = dark ? "bi bi-sun-fill" : "bi bi-moon-stars";
    });
    window.dispatchEvent(new CustomEvent("vk:theme", { detail: { theme: theme } }));
  }

  function toggle() {
    var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch (error) {
      /* ignore */
    }
    apply(next);
  }

  apply(preferred());

  document.addEventListener("DOMContentLoaded", function () {
    apply(document.documentElement.getAttribute("data-theme") || preferred());
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      button.addEventListener("click", toggle);
    });
  });

  window.VKTheme = { toggle: toggle, current: preferred };

  function toast(message, kind) {
    var stack = document.getElementById("toast-stack");
    if (!stack) return;
    var node = document.createElement("div");
    node.className = "vk-toast vk-toast-" + (kind || "info");
    var icon = kind === "success" ? "check-circle" : (kind === "danger" || kind === "warning" ? "exclamation-triangle" : "info-circle");
    node.innerHTML = '<i class="bi bi-' + icon + '"></i><span></span>';
    node.querySelector("span").textContent = message;
    stack.appendChild(node);
    requestAnimationFrame(function () { node.classList.add("show"); });
    setTimeout(function () {
      node.classList.remove("show");
      setTimeout(function () { node.remove(); }, 250);
    }, 4200);
  }

  window.VKToast = toast;
})();
