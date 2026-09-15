/* Make every in-app dialog draggable from its header. */
(function () {
  function bind(dialog) {
    if (!dialog || dialog.getAttribute("data-drag-bound") === "1") return;
    var handle = dialog.querySelector("[data-dialog-drag], .dialog-head");
    if (!handle) return;
    dialog.setAttribute("data-drag-bound", "1");
    handle.style.cursor = "move";
    var startX = 0;
    var startY = 0;
    var origX = 0;
    var origY = 0;
    var dragging = false;
    handle.addEventListener("mousedown", function (e) {
      if (e.button !== 0) return;
      if (e.target.closest("button, a, input, select, textarea, label")) return;
      dragging = true;
      startX = e.clientX;
      startY = e.clientY;
      var rect = dialog.getBoundingClientRect();
      origX = rect.left;
      origY = rect.top;
      dialog.style.margin = "0";
      dialog.style.position = "fixed";
      dialog.style.left = origX + "px";
      dialog.style.top = origY + "px";
      e.preventDefault();
    });
    document.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      dialog.style.left = origX + (e.clientX - startX) + "px";
      dialog.style.top = origY + (e.clientY - startY) + "px";
    });
    document.addEventListener("mouseup", function () {
      dragging = false;
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
    new MutationObserver(scan).observe(document.body, { childList: true, subtree: true });
  }
})();
