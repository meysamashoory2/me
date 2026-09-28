/* Table-settings presentation only. Visible Persian text and ASCII form values
 * are separate; editing a value never changes direction or control placement. */
(function () {
  "use strict";
  var form = document.getElementById("table-layout-save");
  if (!form) return;
  var wrap = document.getElementById("body-wrap");
  var marquee = document.getElementById("body-marquee");
  var lastSubmitter = null;

  function ascii(value) {
    return String(value).replace(/[۰-۹]/g, function (d) { return String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)); })
      .replace(/[٠-٩]/g, function (d) { return String("٠١٢٣٤٥٦٧٨٩".indexOf(d)); }).replace(/٫/g, ".");
  }
  function persian(value) {
    return ascii(value).replace(/[0-9]/g, function (d) { return "۰۱۲۳۴۵۶۷۸۹"[Number(d)]; }).replace(/\./g, "٫");
  }
  function syncNumber(input) {
    var start = input.selectionStart, end = input.selectionEnd;
    var raw = ascii(input.value).trim();
    input.value = persian(input.value);
    if (document.activeElement === input && start !== null) input.setSelectionRange(start, end);
    var number = Number(raw), min = Number(input.dataset.min), max = Number(input.dataset.max);
    var step = Number(input.dataset.step), steps = (number - min) / step;
    var valid = /^\d+(?:\.\d+)?$/.test(raw) && Number.isFinite(number) && number >= min && number <= max && Math.abs(steps - Math.round(steps)) < 1e-8;
    input.setCustomValidity(valid ? "" : "مقدار باید در بازه و با گام تعیین‌شده باشد.");
    form.elements.namedItem(input.dataset.numberFor).value = raw;
    return valid;
  }
  function tint(box) {
    var color = box.querySelector('input[type="color"]'), range = box.querySelector('input[type="range"]');
    box.style.setProperty("--tint", color.value);
    box.style.setProperty("--tint-a", Number(range.value) / 100);
    box.querySelector("output").textContent = persian(range.value) + "٪";
  }
  function syncWrap() {
    marquee.disabled = wrap.checked;
    if (wrap.checked) marquee.checked = false;
  }
  function toast(text, error) {
    var stack = document.getElementById("toast-stack");
    if (!stack) { stack = document.createElement("div"); stack.id = "toast-stack"; stack.className = "toast-stack"; document.body.appendChild(stack); }
    var item = document.createElement("div");
    item.className = "toast " + (error ? "toast-error" : "toast-success");
    item.textContent = text;
    stack.appendChild(item);
    setTimeout(function () { item.remove(); }, 2600);
  }
  function applyFamilyValuesToForm(values) {
    Object.keys(values).forEach(function (name) {
      form.querySelectorAll('[name="' + name + '"]').forEach(function (input) {
        if (input.type === "checkbox") input.checked = !!values[name];
        else if (input.type === "radio") input.checked = input.value === (values[name] ? "show" : "hide");
        else input.value = values[name];
      });
    });
    form.querySelectorAll("[data-number-for]").forEach(function (input) {
      input.value = persian(form.elements.namedItem(input.dataset.numberFor).value);
      syncNumber(input);
    });
    syncWrap();
    form.querySelectorAll("[data-tint]").forEach(tint);
  }
  form.querySelectorAll("[data-number-for]").forEach(function (input) {
    input.addEventListener("input", function (event) { if (!event.isComposing) syncNumber(input); });
    input.addEventListener("compositionend", function () { syncNumber(input); });
    input.addEventListener("change", function () { syncNumber(input); });
    syncNumber(input);
  });
  form.querySelectorAll("[data-tint]").forEach(function (box) {
    box.querySelectorAll("input").forEach(function (input) { input.addEventListener("input", function () { tint(box); }); });
    tint(box);
  });
  wrap.addEventListener("change", syncWrap);
  syncWrap();
  form.querySelectorAll('button[type="submit"],button:not([type])').forEach(function (button) {
    button.addEventListener("click", function () { lastSubmitter = button; });
  });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var submitter = event.submitter || lastSubmitter;
    lastSubmitter = null;
    form.querySelectorAll("[data-number-for]").forEach(syncNumber);
    if (!form.reportValidity()) return;
    var data = new FormData(form);
    if (submitter && submitter.name) data.set(submitter.name, submitter.value);
    fetch(location.href, {method: "POST", credentials: "same-origin", headers: {
      "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": form.elements.namedItem("csrfmiddlewaretoken").value
    }, body: data}).then(function (response) {
      return response.json().then(function (json) { return {ok: response.ok, j: json}; });
    }).then(function (result) {
      if (!result.ok || !result.j.ok) { toast(result.j.error || "ذخیره ممکن نشد.", true); return; }
      var style = document.getElementById("erp-table-layout-css");
      if (style) style.textContent = result.j.css;
      applyFamilyValuesToForm(result.j.layout);
      if (window.ERPTableLayout) window.ERPTableLayout.refreshWidthLocks();
      toast(result.j.message, false);
    }).catch(function () { toast("ارتباط با سرور برقرار نشد.", true); });
  });
})();
