/**
 * Optional per-paper mark components (theory / practical / …).
 * Binds a container with .mc-rows and a hidden input holding JSON [{name, marks}, …].
 */
(function () {
  "use strict";

  function parseJson(v) {
    try {
      var j = JSON.parse(v || "[]");
      return Array.isArray(j) ? j : [];
    } catch (e) {
      return [];
    }
  }

  function rowHtml(name, marks) {
    var n = name == null ? "" : String(name);
    var m = marks == null || marks === "" ? 0 : marks;
    return (
      '<div class="input-group input-group-sm mb-2 mc-row">' +
      '<span class="input-group-text">Component</span>' +
      '<input type="text" class="form-control mc-name" value="" placeholder="e.g. Theory" />' +
      '<span class="input-group-text">Max</span>' +
      '<input type="number" class="form-control mc-marks" min="0" step="1" />' +
      '<button type="button" class="btn btn-outline-danger mc-remove" title="Remove" aria-label="Remove row">' +
      '<i class="bi bi-x-lg"></i></button></div>'
    );
  }

  function sync(root, hidden) {
    var rows = root.querySelectorAll(".mc-row");
    var out = [];
    rows.forEach(function (row) {
      var nmEl = row.querySelector(".mc-name");
      var mkEl = row.querySelector(".mc-marks");
      var nm = (nmEl && nmEl.value) ? nmEl.value.trim() : "";
      var mk = mkEl && mkEl.value !== "" ? parseInt(mkEl.value, 10) : 0;
      if (nm) {
        out.push({ name: nm, marks: isNaN(mk) ? 0 : mk });
      }
    });
    hidden.value = JSON.stringify(out);
    var tot = 0;
    out.forEach(function (x) {
      tot += x.marks;
    });
    // Total may live above the editor (sibling), e.g. "Subjects, dates & times" per-subject rows.
    var tEl = root.querySelector(".mc-total-val");
    if (!tEl && root.parentElement) {
      tEl = root.parentElement.querySelector(".mc-total-val");
    }
    if (!tEl) {
      var card = root.closest(".ec-subject-marking-card");
      if (card) tEl = card.querySelector(".mc-total-val");
    }
    if (!tEl) {
      var cell = root.closest("td");
      if (cell) tEl = cell.querySelector(".mc-total-val");
    }
    if (tEl) tEl.textContent = String(tot);
    var evt = new CustomEvent("examMcChange", { detail: { total: tot, components: out } });
    root.dispatchEvent(evt);
  }

  function wireRow(row, root, hidden) {
    row.querySelectorAll(".mc-name, .mc-marks").forEach(function (inp) {
      inp.addEventListener("input", function () {
        sync(root, hidden);
      });
    });
    var rm = row.querySelector(".mc-remove");
    if (rm) {
      rm.addEventListener("click", function () {
        row.remove();
        sync(root, hidden);
      });
    }
  }

  function bindAddDelegation(root, hidden) {
    if (root.hasAttribute("data-mc-delegated")) return;
    root.setAttribute("data-mc-delegated", "1");
    root.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-mc-add]");
      if (!btn || !root.contains(btn)) return;
      var rowsWrap = root.querySelector(".mc-rows");
      if (!rowsWrap) return;
      var w = document.createElement("div");
      w.innerHTML = rowHtml();
      var row = w.firstElementChild;
      rowsWrap.appendChild(row);
      wireRow(row, root, hidden);
      sync(root, hidden);
    });
  }

  window.ErpExamMc = {
    /**
     * @param {string} [templateJson] — when hidden is empty and no data-mc-empty-default, seed from this JSON string (e.g. copy from another editor).
     */
    init: function (root, hiddenOrId, templateJson) {
      var hidden =
        typeof hiddenOrId === "string"
          ? document.getElementById(hiddenOrId)
          : hiddenOrId;
      if (!root || !hidden) return;

      var rowsWrap = root.querySelector(".mc-rows");
      if (!rowsWrap) return;

      var data = parseJson(hidden.value);
      if (!data.length && templateJson) {
        data = parseJson(templateJson);
      }
      rowsWrap.innerHTML = "";
      if (!data.length) {
        var def = root.getAttribute("data-mc-empty-default");
        if (def === "1") {
          data = [
            { name: "Theory", marks: 60 },
            { name: "Practical", marks: 20 },
            { name: "Internal / Other", marks: 20 },
          ];
        } else if (def === "2") {
          data = [
            { name: "Theory", marks: 80 },
            { name: "Practical", marks: 20 },
          ];
        } else {
          data = [{ name: "", marks: 0 }];
        }
      }
      data.forEach(function (item) {
        var wrap = document.createElement("div");
        wrap.innerHTML = rowHtml();
        var row = wrap.firstElementChild;
        rowsWrap.appendChild(row);
        var nm = row.querySelector(".mc-name");
        var mk = row.querySelector(".mc-marks");
        if (nm) nm.value = item.name || "";
        if (mk) mk.value = String(item.marks != null ? item.marks : 0);
        wireRow(row, root, hidden);
      });

      bindAddDelegation(root, hidden);
      sync(root, hidden);
    },
    parseJson: parseJson,
  };
})();
