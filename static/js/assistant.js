(function () {
  "use strict";

  var form = document.getElementById("assistant-form");
  if (!form) return;

  var messages = document.getElementById("assistant-messages");
  var input = document.getElementById("assistant-input");
  var sendButton = document.getElementById("assistant-send");
  var csrf = form.getAttribute("data-csrf") || (window.CSRF_TOKEN || "");
  var farmId = window.FARM_CONTEXT_ID || null;
  var storageKey = "vk-farm-chat-" + (farmId || "0");

  var strings = (window.I18N && window.I18N.strings) || {};
  function t(key, fallback) { return strings[key] || fallback || ""; }

  var history = load();
  var lastUserText = "";

  function load() {
    try {
      var raw = localStorage.getItem(storageKey);
      var parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function save() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(history.slice(-40)));
    } catch (e) { /* storage may be disabled */ }
  }

  function addMessage(text, who, extraClass) {
    var node = document.createElement("div");
    node.className = "assistant-msg " + who + (extraClass ? " " + extraClass : "");
    String(text).split("\n").forEach(function (line, index) {
      if (index) node.appendChild(document.createElement("br"));
      node.appendChild(document.createTextNode(line));
    });
    messages.appendChild(node);
    messages.scrollTop = messages.scrollHeight;
    return node;
  }

  function render() {
    messages.innerHTML = "";
    if (!history.length) {
      var placeholder = messages.getAttribute("data-placeholder");
      if (placeholder) {
        var intro = document.createElement("div");
        intro.className = "assistant-msg bot";
        intro.appendChild(document.createTextNode(placeholder));
        messages.appendChild(intro);
      }
      return;
    }
    history.forEach(function (item) {
      addMessage(item.text, item.role === "user" ? "me" : "bot");
    });
  }

  function send(text) {
    if (!text || sendButton.disabled) return;
    lastUserText = text;
    addMessage(text, "me");
    history.push({ role: "user", text: text });
    save();

    sendButton.disabled = true;
    var pending = addMessage("…", "bot", "pending");

    var body = { message: text, history: history.slice(-10) };
    if (farmId) body.farm_id = farmId;

    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(body),
    })
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        sendButton.disabled = false;
        pending.remove();
        var reply = payload.reply || payload.error || t("chatNoReply", "No reply.");
        addMessage(reply, "bot");
        history.push({ role: "assistant", text: reply });
        save();
        if (payload.tasks && payload.tasks.length) {
          payload.tasks.forEach(function (task) {
            var line = t("scheduled", "Scheduled") + ": " + task.title +
              (task.due ? " (" + t("due", "due") + " " + task.due + ")" : "");
            addMessage(line, "bot", "task");
          });
          if (farmId) {
            addMessage(t("refreshCalendar", "Open the Calendar tab to see these tasks on their day."), "bot", "task");
          }
        }
      })
      .catch(function () {
        sendButton.disabled = false;
        pending.remove();
        var err = addMessage(t("chatError", "Could not reach the advisor service. Check your connection."), "bot", "error");
        var retry = document.createElement("button");
        retry.type = "button";
        retry.className = "assistant-retry";
        retry.textContent = t("retry", "Retry");
        retry.addEventListener("click", function () {
          err.remove();
          send(lastUserText);
        });
        err.appendChild(retry);
      });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    send(text);
  });

  render();
})();
