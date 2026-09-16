/* Preview a naming-key target with a yellow frame and a stuck back control. */
(function () {
  var params = new URLSearchParams(window.location.search);
  if (params.get("naming_preview") !== "1") return;

  document.documentElement.classList.add("naming-preview-mode");
  if (document.body) document.body.classList.add("naming-preview-mode");
  else document.addEventListener("DOMContentLoaded", function () {
    document.body.classList.add("naming-preview-mode");
  });

  var hl = params.get("hl") || "";
  var ret = params.get("ret") || "";
  var targetEl = null;
  var chrome = null;
  var frame = null;
  var back = null;

  function goBack() {
    window.location.href = ret || "/";
  }

  function visibleRect(el) {
    if (!el || !el.getBoundingClientRect) return null;
    var r = el.getBoundingClientRect();
    if (r.width < 2 && r.height < 2) return null;
    return r;
  }

  function revealIfHidden(el) {
    if (!el) return;
    var cur = el;
    while (cur && cur !== document.body) {
      if (cur.tagName === "DETAILS") cur.open = true;
      cur = cur.parentElement;
    }
    try {
      var cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden") {
        el.classList.add("naming-ghost-slot");
        el.style.setProperty("display", el.tagName === "TH" || el.tagName === "TD" ? "table-cell" : "block", "important");
        el.style.setProperty("visibility", "visible", "important");
      }
    } catch (e) {}
  }

  function injectGhost() {
    var ghost = document.createElement("div");
    ghost.className = "naming-ghost-slot naming-ghost-injected";
    ghost.textContent = "جای خالی این مورد";
    var host = document.querySelector("main.content") || document.body;
    if (hl.indexOf("data-nav-") >= 0) {
      host = document.querySelector("nav.nav") || host;
    } else if (hl.indexOf("data-acc-") >= 0) {
      host = document.querySelector("#system-accordion") || host;
    } else if (hl.indexOf("thead") >= 0 || hl.indexOf("data-col") >= 0) {
      var table = document.querySelector("main.content table") || document.querySelector("table");
      if (table && table.tHead && table.tHead.rows[0]) {
        var th = document.createElement("th");
        th.className = "naming-ghost-slot";
        th.textContent = "جای خالی";
        table.tHead.rows[0].appendChild(th);
        return th;
      }
    }
    host.appendChild(ghost);
    return ghost;
  }

  function pickTarget() {
    var nodes = [];
    if (hl) {
      try {
        nodes = Array.prototype.slice.call(document.querySelectorAll(hl));
      } catch (e) {
        nodes = [];
      }
    }
    var ranked = nodes.slice().sort(function (a, b) {
      function score(el) {
        var s = 0;
        if (el.closest && el.closest("thead")) s += 40;
        if (el.tagName === "TH") s += 20;
        if (el.tagName === "DIALOG") s += 15;
        if (el.hasAttribute && el.hasAttribute("data-nav-key")) s += 12;
        if (el.hasAttribute && el.hasAttribute("data-nav-heading")) s += 12;
        if (el.classList && el.classList.contains("naming-ghost-slot")) s += 25;
        var r = el.getBoundingClientRect ? el.getBoundingClientRect() : { width: 0, height: 0 };
        if (r.width > 1 && r.height > 1) s += 10;
        return s;
      }
      return score(b) - score(a);
    });
    var found = ranked[0] || null;
    if (found) revealIfHidden(found);
    if (found && visibleRect(found)) return found;
    if (found) return found;
    return injectGhost();
  }

  function openDialog(el) {
    if (!el) return;
    var dlg = el.tagName === "DIALOG" ? el : (el.closest ? el.closest("dialog") : null);
    if (!dlg) {
      if (hl.indexOf("excel-transfer-dialog") >= 0) dlg = document.getElementById("excel-transfer-dialog");
      if (hl.indexOf("excel-import-dialog") >= 0) dlg = document.getElementById("excel-import-dialog");
    }
    if (dlg && !dlg.open) {
      try {
        if (typeof dlg.show === "function") dlg.show();
        else dlg.setAttribute("open", "");
      } catch (e) {
        try { dlg.setAttribute("open", ""); } catch (e2) {}
      }
    }
  }

  function ensureChrome() {
    if (chrome) return;
    chrome = document.createElement("div");
    chrome.className = "naming-preview-chrome";
    frame = document.createElement("div");
    frame.className = "naming-preview-frame";
    frame.setAttribute("aria-hidden", "true");
    back = document.createElement("a");
    back.className = "naming-preview-back";
    back.href = ret || "/";
    back.textContent = "بازگشت";
    back.addEventListener("click", function (e) {
      e.preventDefault();
      goBack();
    });
    chrome.appendChild(frame);
    chrome.appendChild(back);
    document.body.appendChild(chrome);
  }

  function place() {
    if (!frame || !back || !targetEl) return;
    var r = visibleRect(targetEl);
    if (!r) {
      var fallback = document.querySelector(".topbar-title") || document.body;
      r = fallback.getBoundingClientRect();
    }
    var pad = 5;
    var top = Math.max(4, r.top - pad);
    var left = Math.max(4, r.left - pad);
    var width = Math.max(28, Math.min(window.innerWidth - left - 4, r.width + pad * 2));
    var height = Math.max(22, r.height + pad * 2);
    frame.style.top = top + "px";
    frame.style.left = left + "px";
    frame.style.width = width + "px";
    frame.style.height = height + "px";

    var btnH = back.offsetHeight || 34;
    var btnW = back.offsetWidth || 88;
    var gap = 6;
    var btnTop = top - btnH - gap;
    if (btnTop < 4) btnTop = Math.min(window.innerHeight - btnH - 4, top + height + gap);
    var rtl = (document.documentElement.dir || "") === "rtl";
    back.style.top = btnTop + "px";
    back.style.bottom = "auto";
    if (rtl) {
      var right = Math.max(4, window.innerWidth - left - width);
      back.style.right = right + "px";
      back.style.left = "auto";
    } else {
      back.style.left = Math.min(left, window.innerWidth - btnW - 4) + "px";
      back.style.right = "auto";
    }
  }

  function lockUi() {
    document.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest(".naming-preview-back")) return;
      e.preventDefault();
      e.stopPropagation();
    }, true);
    document.addEventListener("auxclick", function (e) {
      e.preventDefault();
      e.stopPropagation();
    }, true);
    document.addEventListener("submit", function (e) {
      e.preventDefault();
      e.stopPropagation();
    }, true);
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        goBack();
        return;
      }
      if (e.target.closest && e.target.closest(".naming-preview-back")) return;
      var pass = { ArrowUp: 1, ArrowDown: 1, PageUp: 1, PageDown: 1, Home: 1, End: 1 };
      if (pass[e.key]) return;
      e.preventDefault();
      e.stopPropagation();
    }, true);
  }

  function apply() {
    if (!document.body) return;
    document.body.classList.add("naming-preview-mode");
    ensureChrome();
    targetEl = pickTarget();
    openDialog(targetEl);
    if (hl && targetEl && (hl.indexOf("excel-transfer") >= 0 || hl.indexOf("transfer-") >= 0)) {
      openDialog(document.getElementById("excel-transfer-dialog"));
    }
    if (targetEl && targetEl.classList) targetEl.classList.add("naming-preview-target");
    try {
      targetEl.scrollIntoView({ block: "center", inline: "nearest" });
    } catch (e) {}
    requestAnimationFrame(function () {
      requestAnimationFrame(place);
    });
  }

  lockUi();
  window.addEventListener("scroll", place, true);
  window.addEventListener("resize", place);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", apply);
  } else {
    apply();
  }
  setTimeout(apply, 250);
})();
