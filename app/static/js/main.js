(function () {
  "use strict";
  document.documentElement.classList.add("js");

  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    });
  }

  var searchBtn = document.getElementById("search-toggle");
  var panel = document.getElementById("search-panel");
  if (searchBtn && panel) {
    searchBtn.addEventListener("click", function (e) {
      e.preventDefault();
      panel.hidden = !panel.hidden;
      if (!panel.hidden) { document.getElementById("header-q").focus(); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !panel.hidden) { panel.hidden = true; searchBtn.focus(); }
    });
  }

  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var url = btn.getAttribute("data-copy");
      var done = function () { var t = btn.textContent; btn.textContent = "Copied"; setTimeout(function () { btn.textContent = t; }, 1800); };
      if (navigator.clipboard) { navigator.clipboard.writeText(url).then(done); }
    });
  });

  document.querySelectorAll("select[data-autosubmit]").forEach(function (sel) {
    sel.addEventListener("change", function () { sel.form.submit(); });
  });
})();
