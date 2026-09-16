/**
 * Shared keyboard + click navigation for data tables.
 * - Arrow keys move between cells/rows
 * - Selected row: blue highlight (.is-row-selected)
 * - Current cell: non-blue background (.is-cell-focus)
 * - Single click selects; DOUBLE-CLICK or ENTER enters next level
 *   (data-href or data-drill-index / erp-row-activate)
 * - Activating a non-drillable row on a table that carries
 *   data-lastlevel-msg dispatches `erp-row-inactive` so the page can show a hint
 * - Cursor: pointer on links; pointer on the selected row only if it has a next page
 */
(function () {
  function isEditableTarget(el) {
    if (!el || !el.closest) return false;
    if (el.isContentEditable) return true;
    var tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || tag === "button") {
      return true;
    }
    return !!el.closest(".ts-wrapper, .excel-grid-wrap, [data-editing='1'], .col-ops, a.btn, button, select");
  }

  function bodyRows(table) {
    return Array.prototype.slice.call(
      table.querySelectorAll("tbody tr")
    ).filter(function (tr) {
      if (tr.style.display === "none") return false;
      return tr.querySelectorAll("td").length > 0 && !tr.classList.contains("empty-row");
    });
  }

  function focusableCells(tr) {
    return Array.prototype.slice.call(tr.children).filter(function (td) {
      if (td.tagName !== "TD" && td.tagName !== "TH") return false;
      if (td.classList.contains("col-ops") || td.classList.contains("row-actions")) return false;
      if (td.classList.contains("excel-row-head") || td.classList.contains("excel-corner")) return false;
      return true;
    });
  }

  function rowCanActivate(tr) {
    if (!tr) return false;
    return !!(tr.getAttribute("data-href") || tr.hasAttribute("data-drill-index"));
  }

  function activateRow(tr) {
    if (!tr) return false;
    var href = tr.getAttribute("data-href");
    if (href) {
      window.location.href = href;
      return true;
    }
    if (tr.hasAttribute("data-drill-index")) {
      tr.dispatchEvent(new CustomEvent("erp-row-activate", { bubbles: true }));
      return true;
    }
    return false;
  }

  function scrollCellIntoView(cell) {
    if (!cell) return;
    var scroller = cell.closest(".table-scroll, .table-scroll-wide, .table-wrap");
    if (!scroller) {
      try {
        cell.scrollIntoView({ block: "nearest", inline: "nearest" });
      } catch (e) {}
      return;
    }
    var cellRect = cell.getBoundingClientRect();
    var scRect = scroller.getBoundingClientRect();
    var stickyTh = scroller.querySelector("thead th");
    var headH = 0;
    if (stickyTh) {
      var headRect = stickyTh.getBoundingClientRect();
      headH = Math.max(0, Math.min(headRect.height, headRect.bottom - scRect.top));
    }
    var pad = 2;
    var topLimit = scRect.top + headH + pad;
    var bottomLimit = scRect.bottom - pad;
    if (cellRect.top < topLimit) {
      scroller.scrollTop -= topLimit - cellRect.top;
    } else if (cellRect.bottom > bottomLimit) {
      scroller.scrollTop += cellRect.bottom - bottomLimit;
    }
    var leftLimit = scRect.left + pad;
    var rightLimit = scRect.right - pad;
    var overflowX = 0;
    if (cellRect.left < leftLimit) overflowX = cellRect.left - leftLimit;
    else if (cellRect.right > rightLimit) overflowX = cellRect.right - rightLimit;
    if (overflowX) {
      var rtl = false;
      try {
        rtl = getComputedStyle(scroller).direction === "rtl";
      } catch (err) {}
      scroller.scrollLeft += rtl ? -overflowX : overflowX;
    }
  }

  function paint(table, selRow, selCol) {
    table.querySelectorAll("tbody tr.is-row-selected").forEach(function (tr) {
      tr.classList.remove("is-row-selected");
    });
    table.querySelectorAll("td.is-cell-focus, th.is-cell-focus").forEach(function (td) {
      td.classList.remove("is-cell-focus");
    });
    var rows = bodyRows(table);
    if (!rows.length || selRow < 0 || selRow >= rows.length) return;
    var tr = rows[selRow];
    tr.classList.add("is-row-selected");
    tr.classList.toggle("is-drillable", rowCanActivate(tr));
    var cells = focusableCells(tr);
    if (!cells.length) return;
    var ci = Math.max(0, Math.min(selCol, cells.length - 1));
    var cell = cells[ci];
    cell.classList.add("is-cell-focus");
    scrollCellIntoView(cell);
    if (window.ERPTableLayout && typeof window.ERPTableLayout.onCellSelected === "function") {
      window.ERPTableLayout.onCellSelected(cell);
    }
  }

  function bindTable(table) {
    if (!table || table.dataset.tableNavBound === "1") return;
    if (table.classList.contains("excel-grid")) return;
    if (table.classList.contains("plan-matrix-table")) return;
    table.dataset.tableNavBound = "1";
    table.classList.add("js-table-nav");
    if (!table.hasAttribute("tabindex")) table.setAttribute("tabindex", "0");
    bodyRows(table).forEach(function (tr) {
      tr.classList.toggle("is-drillable", rowCanActivate(tr));
    });

    var selRow = -1;
    var selCol = 0;
    var rtl = (table.getAttribute("dir") || document.documentElement.getAttribute("dir") || "rtl") === "rtl";

    function selectAt(ri, ci) {
      var rows = bodyRows(table);
      if (!rows.length) return;
      selRow = Math.max(0, Math.min(ri, rows.length - 1));
      var cells = focusableCells(rows[selRow]);
      selCol = cells.length ? Math.max(0, Math.min(ci, cells.length - 1)) : 0;
      paint(table, selRow, selCol);
    }
    // Expose the per-table selector so pages can restore a selection
    // (e.g. re-highlight the row a report was drilled from after going back).
    table._erpNavSelect = selectAt;

    // Enter the next level for a drillable row; otherwise, if the table opts in
    // with data-lastlevel-msg, tell the page so it can show an "آخرین سطح" hint.
    function activateOrHint(tr) {
      if (rowCanActivate(tr)) {
        activateRow(tr);
        return;
      }
      var msg = table.getAttribute("data-lastlevel-msg");
      if (msg != null) {
        tr.dispatchEvent(new CustomEvent("erp-row-inactive", {
          bubbles: true,
          detail: { message: msg },
        }));
      }
    }

    function rowFromEvent(e) {
      if (e.target.closest(".col-ops, button, select, input, textarea, label, a.btn")) return null;
      var td = e.target.closest("td, th");
      if (!td || !table.contains(td)) return null;
      var tr = td.closest("tr");
      if (!tr || !table.contains(tr)) return null;
      var rows = bodyRows(table);
      var ri = rows.indexOf(tr);
      if (ri < 0) return null;
      var cells = focusableCells(tr);
      var ci = cells.indexOf(td);
      if (ci < 0) ci = 0;
      return { tr: tr, ri: ri, ci: ci };
    }

    // Single click only selects the row (drilling now needs a double-click).
    table.addEventListener("click", function (e) {
      var hit = rowFromEvent(e);
      if (!hit) return;
      selectAt(hit.ri, hit.ci);
      table.focus({ preventScroll: true });
      window._erpNavTable = table;
    });

    // Double click enters the next level (or hints when already at the last one).
    table.addEventListener("dblclick", function (e) {
      var hit = rowFromEvent(e);
      if (!hit) return;
      e.preventDefault();
      selectAt(hit.ri, hit.ci);
      table.focus({ preventScroll: true });
      activateOrHint(hit.tr);
    });

    // Suppress the browser's word/line text selection triggered by a
    // double/triple click (detail > 1), while leaving normal click-and-drag
    // selection intact (a drag starts from a single-click mousedown, detail 1).
    table.addEventListener("mousedown", function (e) {
      if (e.detail > 1 && !isEditableTarget(e.target)) {
        e.preventDefault();
      }
    });

    function handleNavKey(e) {
      if (table.getAttribute("data-editing") === "1") return false;
      var rows = bodyRows(table);
      if (!rows.length) return false;
      if (selRow < 0) {
        if (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectAt(0, 0);
          return true;
        }
        return false;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        selectAt(selRow + 1, selCol);
        return true;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        selectAt(selRow - 1, selCol);
        return true;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        selectAt(selRow, rtl ? selCol + 1 : selCol - 1);
        return true;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        selectAt(selRow, rtl ? selCol - 1 : selCol + 1);
        return true;
      }
      if (e.key === "Enter" || e.key === " ") {
        var tr = rows[selRow];
        if (!tr) return false;
        e.preventDefault();
        activateOrHint(tr);
        return true;
      }
      return false;
    }
    table._erpNavKey = handleNavKey;

    table.addEventListener("keydown", function (e) {
      if (isEditableTarget(document.activeElement) && document.activeElement !== table) return;
      handleNavKey(e);
    });
  }

  function init(root) {
    var scope = root || document;
    scope.querySelectorAll("table.table, table.pcx-table, .results table").forEach(bindTable);
  }

  function restoreFocusFromUrl() {
    var params = new URLSearchParams(window.location.search);
    var key = params.get("focus_key") || "";
    var id = params.get("focus_id") || "";
    if (!key && !id) return;
    var tr = null;
    if (key) tr = document.querySelector('table.js-table-nav tbody tr[data-key="' + key.replace(/"/g, "") + '"]');
    if (!tr && id) tr = document.querySelector('table.js-table-nav tbody tr[data-id="' + id.replace(/"/g, "") + '"]');
    if (!tr) return;
    var table = tr.closest("table");
    if (!table) return;
    var rows = bodyRows(table);
    var ri = rows.indexOf(tr);
    if (ri < 0) return;
    window.ERPTableNav.select(table, ri, 0);
    window._erpNavTable = table;
    try { table.focus({ preventScroll: true }); } catch (e) {}
  }

  window.ERPTableNav = {
    init: init,
    bind: bindTable,
    activateRow: activateRow,
    select: function (table, ri, ci) {
      if (table && typeof table._erpNavSelect === "function") {
        table._erpNavSelect(ri, ci || 0);
      }
    },
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { init(); restoreFocusFromUrl(); });
  } else {
    init();
    restoreFocusFromUrl();
  }

  document.addEventListener("keydown", function (e) {
    if (isEditableTarget(e.target)) return;
    var table = window._erpNavTable;
    if (!table || typeof table._erpNavKey !== "function") {
      var selected = document.querySelector("table.js-table-nav tbody tr.is-row-selected");
      table = selected ? selected.closest("table") : document.querySelector("table.js-table-nav");
    }
    if (!table || typeof table._erpNavKey !== "function") return;
    if (document.activeElement === table) return;
    table._erpNavKey(e);
  });

  function isAppHome() {
    var p = (window.location.pathname || "/").replace(/\/+$/, "") || "/";
    return p === "/";
  }

  function isReportViewer() {
    return !!(document.body && document.body.hasAttribute("data-report-level"));
  }

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (document.documentElement.classList.contains("naming-preview-mode")) return;
    if (isReportViewer()) return;
    if (document.querySelector("dialog[open]")) return;
    if (isEditableTarget(e.target)) return;
    if (isAppHome()) return;
    if (window.history.length <= 1) return;
    e.preventDefault();
    window.history.back();
  });
})();
