/* Preview a naming-key target with a yellow frame and a back control. */
(function () {
  var params = new URLSearchParams(window.location.search);
  if (params.get("naming_preview") !== "1") return;
  var hl = params.get("hl") || "";
  var ret = params.get("ret") || "";
  function frame(el) {
    if (!el) return;
    el.classList.add("naming-preview-target");
    try {
      el.scrollIntoView({ block: "center", inline: "nearest" });
    } catch (e) {}
  }
  function apply() {
    var target = null;
    if (hl) {
      try {
        target = document.querySelector(hl);
      } catch (e) {
        target = null;
      }
    }
    if (!target) target = document.querySelector("main.content") || document.body;
    frame(target);
    if (!ret) return;
    var btn = document.createElement("a");
    btn.className = "naming-preview-back";
    btn.href = ret;
    btn.textContent = "بازگشت";
    document.body.appendChild(btn);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", apply);
  } else {
    apply();
  }
})();
