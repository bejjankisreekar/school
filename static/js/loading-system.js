/**
 * School ERP — global loading behaviors
 *
 * Opt-out: <form data-no-global-loading> — <a data-no-loading>
 * Customize: data-loading-message data-loading-label on POST forms
 * Full overlay: data-loading-overlay (multipart POST always shows overlay)
 * Skeletons: [data-erp-table-skeleton] with .erp-table-skeleton-overlay + .erp-table-live
 * API: SchoolERPLoading.showOverlay / hideOverlay / resetOverlay / fetchWithFeedback / showSectionError
 */
(function (window, document) {
  "use strict";

  var overlay;
  var overlayText;
  var progressRoot;
  var overlayCount = 0;
  var btnSnapshot = new WeakMap();

  function q(sel, root) {
    return (root || document).querySelector(sel);
  }

  function init() {
    overlay = document.getElementById("erp-loading-overlay");
    overlayText = document.getElementById("erp-loading-overlay-text");
    progressRoot = document.getElementById("erp-nav-progress");
  }

  function showOverlay(message) {
    if (!overlay) init();
    if (!overlay) return;
    overlayCount++;
    if (overlayText && message) overlayText.textContent = message;
    overlay.classList.add("is-visible");
    overlay.setAttribute("aria-hidden", "false");
    document.documentElement.classList.add("erp-loading-locked");
  }

  function hideOverlay() {
    overlayCount = Math.max(0, overlayCount - 1);
    if (overlayCount > 0) return;
    if (!overlay) init();
    if (!overlay) return;
    overlay.classList.remove("is-visible");
    overlay.setAttribute("aria-hidden", "true");
    document.documentElement.classList.remove("erp-loading-locked");
  }

  function resetOverlay() {
    overlayCount = 0;
    if (!overlay) init();
    if (overlay) {
      overlay.classList.remove("is-visible");
      overlay.setAttribute("aria-hidden", "true");
    }
    document.documentElement.classList.remove("erp-loading-locked");
  }

  function startNavigation() {
    if (!progressRoot) init();
    if (!progressRoot) return;
    progressRoot.classList.add("is-active");
  }

  function finishNavigation() {
    if (!progressRoot) init();
    if (progressRoot) progressRoot.classList.remove("is-active");
  }

  function isModifiedClick(e) {
    return e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey;
  }

  function sameOriginLink(a) {
    var href = a.getAttribute("href");
    if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0 || href.indexOf("mailto:") === 0) {
      return false;
    }
    try {
      var u = new URL(href, window.location.href);
      return u.origin === window.location.origin;
    } catch (err) {
      return false;
    }
  }

  function setSubmitButtonLoading(form, submitter, label) {
    var btn = submitter;
    if (!btn || (btn.tagName !== "BUTTON" && btn.tagName !== "INPUT")) {
      btn = form.querySelector('button[type="submit"]');
    }
    if (!btn) btn = form.querySelector('input[type="submit"]');
    if (!btn) return;

    if (!btnSnapshot.has(btn)) {
      if (btn.tagName === "BUTTON") {
        btnSnapshot.set(btn, { html: btn.innerHTML, width: btn.offsetWidth });
      } else {
        btnSnapshot.set(btn, { value: btn.value, width: btn.offsetWidth });
      }
    }
    var snap = btnSnapshot.get(btn);
    var w = Math.max(snap.width || 0, 128);
    btn.style.minWidth = w + "px";
    btn.disabled = true;

    if (btn.tagName === "BUTTON") {
      btn.innerHTML =
        '<span class="erp-inline-spinner" aria-hidden="true"></span><span>' +
        (label || "Saving…") +
        "</span>";
    } else {
      btn.value = label || "Saving…";
    }
  }

  function onFormSubmit(e) {
    var form = e.target;
    if (!form || form.tagName !== "FORM") return;
    if (form.hasAttribute("data-no-global-loading")) return;

    var method = (form.getAttribute("method") || "get").toLowerCase();
    var submitter = e.submitter || null;

    if (method === "get") {
      startNavigation();
      return;
    }

    if (method !== "post") return;

    var enctype = (form.getAttribute("enctype") || "").toLowerCase();
    var isMultipart = enctype.indexOf("multipart") !== -1;
    var customMsg = form.getAttribute("data-loading-message");
    var overlayMsg = customMsg;
    if (!overlayMsg) {
      overlayMsg = isMultipart ? "Uploading file…" : "Processing request…";
    }

    if (form.hasAttribute("data-loading-overlay") || isMultipart) {
      showOverlay(overlayMsg);
    }

    var btnLabel = form.getAttribute("data-loading-label");
    if (!btnLabel) btnLabel = isMultipart ? "Uploading…" : "Saving…";

    setSubmitButtonLoading(form, submitter, btnLabel);
  }

  function onLinkClick(e) {
    var a = e.target.closest && e.target.closest("a[href]");
    if (!a || isModifiedClick(e)) return;
    if (a.hasAttribute("data-no-loading")) return;
    if (a.getAttribute("target") === "_blank") return;
    if (a.hasAttribute("download")) return;
    if (!sameOriginLink(a)) return;

    try {
      var abs = new URL(a.href, window.location.href);
      if (abs.pathname === window.location.pathname && abs.search === window.location.search) {
        return;
      }
    } catch (err2) {
      return;
    }

    startNavigation();
  }

  /** Table / list: fade out skeleton overlay after paint */
  function initTableSkeletons() {
    document.querySelectorAll("[data-erp-table-skeleton]").forEach(function (host) {
      var skel = host.querySelector(".erp-table-skeleton-overlay");
      var live = host.querySelector(".erp-table-live");
      if (!skel || !live) return;

      requestAnimationFrame(function () {
        skel.classList.add("erp-skeleton-fade-out");
        live.classList.add("erp-skeleton-fade-in");
        window.setTimeout(function () {
          skel.setAttribute("hidden", "");
        }, 240);
      });
    });
  }

  /** Reports hub */
  function initReportsSkeleton() {
    var skel = document.getElementById("erp-reports-skeleton");
    var body = document.getElementById("erp-reports-body");
    if (!skel || !body) return;
    skel.setAttribute("hidden", "");
    body.hidden = false;
  }

  function showSectionError(container, message) {
    var el = typeof container === "string" ? document.querySelector(container) : container;
    if (!el) return;
    el.innerHTML =
      '<div class="erp-load-error card border-danger border-2 my-3">' +
      '<div class="card-body text-center py-4">' +
      '<p class="mb-3 text-danger-emphasis">' +
      (message || "Something went wrong while loading data.") +
      "</p>" +
      '<button type="button" class="btn btn-primary rounded-pill px-4 erp-retry-btn">Retry</button>' +
      "</div></div>";
    var btn = el.querySelector(".erp-retry-btn");
    if (btn) btn.addEventListener("click", function () {
      window.location.reload();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    init();
    finishNavigation();
    resetOverlay();

    document.body.addEventListener("click", onLinkClick, true);
    document.addEventListener("submit", onFormSubmit, true);

    initTableSkeletons();
    initReportsSkeleton();
  });

  window.addEventListener("load", function () {
    finishNavigation();
  });

  window.addEventListener("pageshow", function (ev) {
    finishNavigation();
    resetOverlay();
    if (ev.persisted) finishNavigation();
  });

  function fetchWithFeedback(url, options, overlayMessage) {
    showOverlay(overlayMessage || "Loading…");
    return fetch(url, options || {})
      .then(function (res) {
        resetOverlay();
        return res;
      })
      .catch(function (err) {
        resetOverlay();
        throw err;
      });
  }

  window.SchoolERPLoading = {
    showOverlay: showOverlay,
    hideOverlay: hideOverlay,
    resetOverlay: resetOverlay,
    startNavigation: startNavigation,
    finishNavigation: finishNavigation,
    showSectionError: showSectionError,
    fetchWithFeedback: fetchWithFeedback,
  };
})(window, document);
