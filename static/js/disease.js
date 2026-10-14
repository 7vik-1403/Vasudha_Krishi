/* Leaf-photo disease detection widget. Drop/choose an image -> POST /api/disease
   -> render the Gemini diagnosis. Degrades to an inline message when disabled. */
(function () {
  function lang() { return (window.I18N && window.I18N.language) || document.documentElement.lang || "en"; }
  function t(en, mr) { return lang() === "mr" ? mr : en; }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function renderReply(text) {
    return escapeHtml(text)
      .replace(/\n/g, "<br>")
      .replace(/^\s*(\d+)[.)]\s/gm, "<br><b>$1.</b> ");
  }

  function init(widget) {
    var input = widget.querySelector('input[type="file"]');
    var drop = widget.querySelector("[data-drop]");
    var preview = widget.querySelector("[data-preview]");
    var submit = widget.querySelector("[data-submit]");
    var output = widget.querySelector("[data-diagnosis]");
    var cropSelect = widget.querySelector("[data-crop]");
    var farmId = widget.getAttribute("data-farm-id") || "";
    if (!input || !submit) return;

    var file = null;

    function setFile(f) {
      if (!f) return;
      if (!/^image\/(jpeg|png|webp)$/i.test(f.type) && !/\.(jpe?g|png|webp)$/i.test(f.name)) {
        output.innerHTML = '<div class="diagnosis-box err">' + t("Please choose a JPEG, PNG or WebP image.", "\u0915\u0943\u092a\u092f\u093e JPEG, PNG \u0915\u093f\u0902\u0935\u093e WebP \u092a\u094d\u0930\u0924\u093f\u092e\u093e \u0928\u093f\u0935\u0921\u093e.") + "</div>";
        return;
      }
      file = f;
      if (preview) {
        preview.innerHTML = "";
        var img = document.createElement("img");
        img.src = URL.createObjectURL(f);
        preview.appendChild(img);
        preview.classList.add("has-image");
      }
      submit.disabled = false;
      output.innerHTML = "";
    }

    input.addEventListener("change", function () { setFile(input.files[0]); });

    if (drop) {
      ["dragenter", "dragover"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("is-over"); });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("is-over"); });
      });
      drop.addEventListener("drop", function (e) {
        if (e.dataTransfer && e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
      });
      drop.addEventListener("click", function (e) { if (e.target !== input) input.click(); });
    }

    submit.addEventListener("click", function () {
      if (!file) return;
      var fd = new FormData();
      fd.append("image", file);
      if (cropSelect && cropSelect.value) fd.append("crop", cropSelect.value);
      if (farmId) fd.append("farm_id", farmId);

      submit.disabled = true;
      output.innerHTML = '<div class="diagnosis-box loading"><span class="spinner"></span> ' +
        t("Analysing the leaf\u2026", "\u092a\u093e\u0928 \u0924\u092a\u093e\u0938\u0924 \u0906\u0939\u0947\u2026") + "</div>";

      fetch("/api/disease", { method: "POST", body: fd })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
          var d = res.data || {};
          if (d.ok) {
            var badge = d.issue ? '<span class="diag-tag">' + escapeHtml(d.issue) +
              (d.confidence ? ' \u00b7 ' + escapeHtml(d.confidence) : "") + "</span>" : "";
            output.innerHTML = '<div class="diagnosis-box ok">' + badge + "<p>" + renderReply(d.reply) + "</p></div>";
            if (window.VKToast) window.VKToast(t("Diagnosis ready", "\u0928\u093f\u0926\u093e\u0928 \u0924\u092f\u093e\u0930"), "success");
          } else {
            output.innerHTML = '<div class="diagnosis-box err">' + renderReply(d.reply || d.error || t("Could not analyse the photo.", "\u092a\u094d\u0930\u0924\u093f\u092e\u093e \u0924\u092a\u093e\u0938\u0924\u093e \u0906\u0932\u0940 \u0928\u093e\u0939\u0940.")) + "</div>";
          }
        })
        .catch(function () {
          output.innerHTML = '<div class="diagnosis-box err">' + t("Network error. Please try again.", "\u0928\u0947\u091f\u0935\u0930\u094d\u0915 \u0924\u094d\u0930\u0941\u091f\u0940. \u092a\u0941\u0928\u094d\u0939\u093e \u092a\u094d\u0930\u092f\u0924\u094d\u0928 \u0915\u0930\u093e.") + "</div>";
        })
        .finally(function () { submit.disabled = false; });
    });
  }

  function boot() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-disease]"), init);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
