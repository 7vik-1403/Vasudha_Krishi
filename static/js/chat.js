(function () {
  "use strict";

  var panel, messages, input, form, sendButton, history = [];
  var strings = (window.I18N && window.I18N.strings) || {};
  function t(key, fallback) { return strings[key] || fallback || ""; }

  function el(id) { return document.getElementById(id); }

  function init() {
    panel = el("chat-panel");
    messages = el("chat-messages");
    input = el("chat-input");
    form = el("chat-form");
    sendButton = el("chat-send");

    if (window.FARM_CONTEXT_ID) {
      var context = el("chat-context");
      var name = el("chat-context-name");
      if (context && name) {
        context.classList.remove("d-none");
        name.textContent = window.FARM_CONTEXT_NAME || ("farm #" + window.FARM_CONTEXT_ID);
      }
    }

    el("chat-toggle").addEventListener("click", function () {
      panel.classList.toggle("open");
      panel.setAttribute("aria-hidden", panel.classList.contains("open") ? "false" : "true");
      if (panel.classList.contains("open")) input.focus();
    });
    el("chat-close").addEventListener("click", function () {
      panel.classList.remove("open");
    });
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      send();
    });
  }

  function addMessage(text, who, extraClass) {
    var node = document.createElement("div");
    node.className = "chat-msg " + who + (extraClass ? " " + extraClass : "");
    text.split("\n").forEach(function (line, index) {
      if (index) node.appendChild(document.createElement("br"));
      node.appendChild(document.createTextNode(line));
    });
    messages.appendChild(node);
    messages.scrollTop = messages.scrollHeight;
    return node;
  }

  function send() {
    var text = input.value.trim();
    if (!text || sendButton.disabled) return;

    addMessage(text, "me");
    input.value = "";
    sendButton.disabled = true;
    var pending = addMessage("...", "bot", "pending");

    var body = { message: text, history: history.slice(-10) };
    if (window.FARM_CONTEXT_ID) body.farm_id = window.FARM_CONTEXT_ID;

    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": el("chat-csrf").value },
      body: JSON.stringify(body),
    })
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        sendButton.disabled = false;
        pending.remove();
        var reply = payload.reply || payload.error || t("chatNoReply", "No reply.");
        addMessage(reply, "bot");
        history.push({ role: "user", text: text });
        history.push({ role: "assistant", text: reply });
        if (payload.tasks && payload.tasks.length) {
          payload.tasks.forEach(function (task) {
            addMessage(t("scheduled", "Scheduled") + ": " + task.title + " (" + t("due", "due") + " " + task.due + ")", "bot", "task");
          });
          if (window.FARM_CONTEXT_ID) {
            var link = document.createElement("div");
            link.className = "chat-msg task";
            link.textContent = t("refreshCalendar", "Refresh the calendar to see them on the right day.");
            messages.appendChild(link);
            messages.scrollTop = messages.scrollHeight;
          }
        }
      })
      .catch(function () {
        sendButton.disabled = false;
        pending.remove();
        addMessage(t("chatError", "Could not reach the advisor service. Check your connection."), "bot", "error");
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
