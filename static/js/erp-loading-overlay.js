/**
 * Campus ERP — global loading overlay
 * ErpLoading.show('Optional message');
 * ErpLoading.hide();
 * Forms: add data-erp-loading="Message" (runs after native constraint validation)
 */
(function () {
  var overlay;
  var titleEl;
  var subEl;
  var depth = 0;

  function elements() {
    if (!overlay) {
      overlay = document.getElementById("erp-loading-overlay");
      if (overlay) {
        titleEl = overlay.querySelector(".erp-loading-title");
        subEl = overlay.querySelector(".erp-loading-sub");
      }
    }
    return overlay;
  }

  function applyMessage(message) {
    if (!titleEl) return;
    var text = message == null || message === "" ? "Loading…" : String(message);
    titleEl.textContent = text;
    if (subEl) {
      subEl.textContent =
        text.indexOf("…") !== -1 || text.indexOf("...") !== -1
          ? "This will only take a moment."
          : "Please wait while we complete your request.";
    }
  }

  window.ErpLoading = {
    show: function (message) {
      var el = elements();
      if (!el) return;
      depth += 1;
      applyMessage(message);
      el.removeAttribute("hidden");
      requestAnimationFrame(function () {
        el.classList.add("is-visible");
      });
      el.setAttribute("aria-hidden", "false");
      document.documentElement.classList.add("erp-loading-active");
    },

    hide: function () {
      var el = elements();
      if (!el) return;
      depth = Math.max(0, depth - 1);
      if (depth > 0) return;
      el.classList.remove("is-visible");
      el.setAttribute("aria-hidden", "true");
      document.documentElement.classList.remove("erp-loading-active");
      window.setTimeout(function () {
        if (depth === 0) el.setAttribute("hidden", "");
      }, 280);
    },

    forceHide: function () {
      depth = 0;
      var el = elements();
      if (!el) return;
      el.classList.remove("is-visible");
      el.setAttribute("hidden", "");
      el.setAttribute("aria-hidden", "true");
      document.documentElement.classList.remove("erp-loading-active");
    },
  };

  document.addEventListener(
    "DOMContentLoaded",
    function () {
      document.querySelectorAll("form[data-erp-loading]").forEach(function (form) {
        form.addEventListener("submit", function () {
          if (typeof form.checkValidity === "function" && !form.checkValidity()) return;
          var msg = form.getAttribute("data-erp-loading");
          window.ErpLoading.show(msg || "Please wait…");
        });
      });
    },
    { passive: true }
  );

  window.addEventListener("pageshow", function (ev) {
    if (ev.persisted) window.ErpLoading.forceHide();
  });
})();
