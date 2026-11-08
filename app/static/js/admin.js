(function () {
  "use strict";

  // Rich-text editor (contenteditable + execCommand). Content is sanitised again on the server.
  var rte = document.getElementById("rte");
  var field = document.getElementById("body-field");
  var form = document.getElementById("article-form");
  if (rte && field && form) {
    try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch (e) {}
    document.getElementById("rte-toolbar").addEventListener("click", function (ev) {
      var btn = ev.target.closest("button[data-cmd]");
      if (!btn) { return; }
      ev.preventDefault();
      rte.focus();
      var cmd = btn.getAttribute("data-cmd"), val = btn.getAttribute("data-val");
      if (cmd === "createLink") {
        var url = window.prompt("Link address (https://...)");
        if (!url) { return; }
        if (!/^(https?:\/\/|mailto:)/i.test(url)) { window.alert("Links must start with http://, https:// or mailto:"); return; }
        document.execCommand("createLink", false, url);
      } else if (cmd === "formatBlock") {
        document.execCommand("formatBlock", false, val);
      } else {
        document.execCommand(cmd, false, null);
      }
    });
    // Paste as plain text to avoid importing foreign styles.
    rte.addEventListener("paste", function (ev) {
      ev.preventDefault();
      var text = (ev.clipboardData || window.clipboardData).getData("text/plain");
      document.execCommand("insertText", false, text);
    });
    form.addEventListener("submit", function () { field.value = rte.innerHTML; });
  }

  // Confirmation dialogs (CSP forbids inline handlers).
  document.addEventListener("submit", function (ev) {
    var msg = ev.target.getAttribute && ev.target.getAttribute("data-confirm");
    var sub = ev.submitter && ev.submitter.getAttribute("data-confirm-btn");
    if ((msg || sub) && !window.confirm(msg || sub)) { ev.preventDefault(); }
  });

  var all = document.getElementById("select-all");
  if (all) {
    all.addEventListener("change", function () {
      document.querySelectorAll('input[name="ids"]').forEach(function (c) { c.checked = all.checked; });
    });
  }
})();
