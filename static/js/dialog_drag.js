/* Floating dialogs: titlebar drag, viewport clamp, click-outside to close. */
(function () {
  var mask = null;
  var openDialogs = [];

  function ensureMask() {
    if (mask) return mask;
    mask = document.createElement("div");
    mask.className = "dialog-float-mask";
    mask.setAttribute("aria-hidden", "true");
    mask.addEventListener("mousedown", function (e) {
      e.preventDefault();
      closeTop();
    });
    document.body.appendChild(mask);
    return mask;
  }

  function showMask() {
    ensureMask().classList.add("is-on");
  }

  function hideMask() {
    if (mask && !openDialogs.length) mask.classList.remove("is-on");
  }

  function closeTop() {
    var dlg = openDialogs[openDialogs.length - 1];
    if (!dlg) return;
    if (dlg.close) dlg.close();
    else dlg.removeAttribute("open");
  }

  function clampPoint(dialog, left, top) {
    var rect = dialog.getBoundingClientRect();
    var w = rect.width || dialog.offsetWidth || 320;
    var h = rect.height || dialog.offsetHeight || 160;
    var maxL = Math.max(8, window.innerWidth - w - 8);
    var maxT = Math.max(8, window.innerHeight - h - 8);
    return {
      left: Math.min(Math.max(8, left), maxL),
      top: Math.min(Math.max(8, top), maxT),
    };
  }

  function pin(dialog, left, top) {
    var pos = clampPoint(dialog, left, top);
    dialog.style.setProperty("position", "fixed", "important");
    dialog.style.setProperty("margin", "0", "important");
    dialog.style.setProperty("inset", "auto", "important");
    dialog.style.setProperty("right", "auto", "important");
    dialog.style.setProperty("bottom", "auto", "important");
    dialog.style.setProperty("transform", "none", "important");
    dialog.style.setProperty("left", pos.left + "px", "important");
    dialog.style.setProperty("top", pos.top + "px", "important");
  }

  function center(dialog) {
    var rect = dialog.getBoundingClientRect();
    var w = rect.width || dialog.offsetWidth || 320;
    var h = rect.height || dialog.offsetHeight || 160;
    pin(dialog, (window.innerWidth - w) / 2, (window.innerHeight - h) / 2);
  }

  function titleFrom(dialog) {
    var existing = dialog.querySelector(".dialog-titlebar");
    if (existing && (existing.textContent || "").trim()) return "";
    var head = dialog.querySelector(".dialog-head h3, .dialog-head h2, .dialog-title, [data-dialog-title]");
    if (head) return (head.textContent || "").trim();
    return "پنجره";
  }

  function ensureTitlebar(dialog) {
    var bar = dialog.querySelector(".dialog-titlebar");
    if (bar) return bar;
    var title = titleFrom(dialog);
    bar = document.createElement("div");
    bar.className = "dialog-titlebar";
    bar.setAttribute("data-dialog-drag", "");
    bar.textContent = title || "پنجره";
    var body = dialog.querySelector("[data-dialog-body]") || dialog;
    body.insertBefore(bar, body.firstChild);
    dialog.querySelectorAll(".dialog-head [data-dialog-close], .dialog-head .btn-ghost[value='close']").forEach(function (btn) {
      btn.hidden = true;
    });
    return bar;
  }

  function trackOpen(dialog) {
    if (openDialogs.indexOf(dialog) < 0) openDialogs.push(dialog);
    showMask();
    requestAnimationFrame(function () { center(dialog); });
  }

  function trackClose(dialog) {
    openDialogs = openDialogs.filter(function (d) { return d !== dialog; });
    hideMask();
  }

  function bind(dialog) {
    if (!dialog || dialog.getAttribute("data-drag-bound") === "1") return;
    dialog.setAttribute("data-drag-bound", "1");
    dialog.classList.add("dialog-floating");
    var handle = ensureTitlebar(dialog);

    if (typeof dialog.showModal === "function" && !dialog._erpShowModal) {
      dialog._erpShowModal = dialog.showModal.bind(dialog);
      dialog.showModal = function () {
        if (typeof dialog.show === "function") dialog.show();
        else dialog.setAttribute("open", "");
        trackOpen(dialog);
      };
    }
    if (typeof dialog.show === "function" && !dialog._erpShow) {
      dialog._erpShow = dialog.show.bind(dialog);
      dialog.show = function () {
        dialog._erpShow();
        trackOpen(dialog);
      };
    }
    dialog.addEventListener("close", function () { trackClose(dialog); });

    var dragging = false;
    var startX = 0;
    var startY = 0;
    var origX = 0;
    var origY = 0;
    var activePointer = null;

    function onMove(e) {
      if (!dragging || (activePointer != null && e.pointerId !== activePointer)) return;
      pin(dialog, origX + (e.clientX - startX), origY + (e.clientY - startY));
    }
    function onUp(e) {
      if (activePointer != null && e.pointerId !== activePointer) return;
      dragging = false;
      activePointer = null;
      window.removeEventListener("pointermove", onMove, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("pointercancel", onUp, true);
    }

    handle.addEventListener("pointerdown", function (e) {
      if (e.button != null && e.button !== 0) return;
      if (e.target.closest && e.target.closest("button, a, input, select, textarea")) return;
      var rect = dialog.getBoundingClientRect();
      if (!rect.width || !rect.height) center(dialog);
      rect = dialog.getBoundingClientRect();
      dragging = true;
      activePointer = e.pointerId;
      startX = e.clientX;
      startY = e.clientY;
      origX = rect.left;
      origY = rect.top;
      pin(dialog, origX, origY);
      window.addEventListener("pointermove", onMove, true);
      window.addEventListener("pointerup", onUp, true);
      window.addEventListener("pointercancel", onUp, true);
      e.preventDefault();
    });
  }

  function scan() {
    document.querySelectorAll("dialog.dialog").forEach(bind);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scan);
  } else {
    scan();
  }
  if (window.MutationObserver) {
    new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
  }
  window.addEventListener("resize", function () {
    openDialogs.forEach(function (dlg) {
      var r = dlg.getBoundingClientRect();
      pin(dlg, r.left, r.top);
    });
  });
})();
