/* Headless browser acceptance against synthetic pages exported by
 * catalog.tests_general_table_ui. No application server or database is used.
 * Usage: NODE_PATH=<existing playwright modules> node scripts/tests/general_tables.cjs <fixture.json>
 * BROWSER_CHANNEL defaults to msedge; no browser/dependency is installed here.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const root = path.resolve(__dirname, "../..");
const fixtures = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

(async function () {
  const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || "msedge", headless: true });
  let assertions = 0;
  function equal(actual, expected, label) { assert.deepEqual(actual, expected, label); assertions++; }
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    let activeCss = null, posted = null;
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.origin !== "http://table-ui.test") return route.abort();
      if (url.pathname.startsWith("/static/")) {
        const rel = url.pathname.slice(8);
        const base = rel.startsWith("admin/") ? path.join(root, ".venv/Lib/site-packages/django/contrib/admin/static") : path.join(root, "static");
        const filename = path.resolve(base, rel);
        if (!filename.startsWith(base + path.sep) || !fs.existsSync(filename)) return route.fulfill({ status: 404, body: "" });
        return route.fulfill({ path: filename });
      }
      const entry = Object.values(fixtures.pages).find(p => p.url === url.pathname + url.search);
      if (!entry) return route.fulfill({ status: 404, body: "No live endpoint in this test" });
      if (route.request().method() === "POST") {
        posted = route.request().postData();
        const family = url.searchParams.get("family") || "general";
        return route.fulfill({ json: { ok: true, message: "اعمال شد", css: fixtures.defaultCss, layout: fixtures.defaults[family] } });
      }
      let html = entry.html;
      if (activeCss) html = html.replace(/(<style id="erp-table-layout-css">)[\s\S]*?(<\/style>)/, "$1" + activeCss + "$2");
      return route.fulfill({ body: html, contentType: "text/html; charset=utf-8" });
    });
    async function open(name, css = null) {
      activeCss = css;
      await page.goto("http://table-ui.test" + fixtures.pages[name].url);
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    }
    async function styles(selector, props) {
      return page.locator(selector).first().evaluate((el, keys) => Object.fromEntries(keys.map(k => [k, getComputedStyle(el)[k]])), props);
    }
    const h = 'main[data-table-type="general"] thead th';
    for (const name of Object.keys(fixtures.pages).filter(n => !n.startsWith("settings_"))) {
      await open(name, fixtures.cases.on.css);
      equal(await page.locator('main[data-table-type="general"]').count(), 1, name + " classification");
      if (await page.locator(h).count()) {
        equal((await styles(h, ["fontSize"])).fontSize, "18px", name + " header size");
        equal((await styles(h, ["backgroundColor"])).backgroundColor, "rgb(255, 0, 170)", name + " header color");
      }
    }
    for (const name of ["print_form_list", "report_list", "admin:catalog_product_changelist"]) {
      await open(name, fixtures.cases.on.css);
      const admin = name.startsWith("admin:");
      const table = admin ? "#result_list" : (name === "report_list" ? "#report-list-table" : "#form-list-table");
      const header = table + " thead th";
      const body = table + (admin ? " tbody th" : " tbody td.cell-nav .list-title");
      equal((await styles(body, ["fontSize"])).fontSize, "20px", name + " nested/body TH size");
      equal((await styles(header, ["borderLeftWidth", "borderBottomWidth", "borderRightWidth", "backgroundImage", "boxShadow"])),
        {borderLeftWidth:"1px",borderBottomWidth:"1px",borderRightWidth:"0px",backgroundImage:"none",boxShadow:"none"}, name + " uniform single header grid");
      equal((await styles(table + " tbody td", ["borderLeftWidth", "borderBottomWidth", "backgroundImage"])),
        {borderLeftWidth:"1px",borderBottomWidth:"1px",backgroundImage:"none"}, name + " uniform body grid");
      const label = table + (admin ? " thead .admin-th-label" : " thead .th-sort");
      equal((await styles(label, ["fontSize", "color", "fontWeight", "whiteSpace"])),
        {fontSize:"18px",color:"rgb(30, 58, 95)",fontWeight:"700",whiteSpace:"normal"}, name + " header text adapter");
      if (admin) equal((await styles(body, ["backgroundColor"])).backgroundColor, "rgba(0, 0, 0, 0)", "admin body TH is not a header");
      // Constrain real columns and use deliberately long text to distinguish
      // wrapping from an inherited CSS declaration that has no visual effect.
      await page.locator(table).evaluate(t => {
        t.style.width = t.style.minWidth = "600px";
        t.querySelectorAll("col").forEach(c => { c.style.width = "100px"; });
        t.querySelectorAll("thead th").forEach(th => { th.style.width = th.style.minWidth = th.style.maxWidth = "100px"; });
      });
      const textSelector = admin ? table + " tbody th > a" : body;
      await page.locator(textSelector).first().evaluate(el => { el.textContent = "متن طولانی آزمایشی برای بررسی شکستن سطر ".repeat(5); });
      async function textHeight() { return page.locator(textSelector).first().evaluate(el => { const r = document.createRange(); r.selectNodeContents(el); return r.getBoundingClientRect().height; }); }
      const wrapped = await textHeight();
      await page.locator("#erp-table-layout-css").evaluate((el, css) => { el.textContent = css; }, fixtures.cases.off.css);
      equal((await styles(header, ["borderLeftWidth", "borderBottomWidth", "boxShadow"])),
        {borderLeftWidth:"0px",borderBottomWidth:"0px",boxShadow:"none"}, name + " hidden header borders");
      equal((await styles(table + " tbody td", ["borderLeftWidth"])).borderLeftWidth, "1px", "body borders remain independent");
      assert.ok(wrapped > (await textHeight()) * 1.5, name + " long text really wraps ON and stays single-line OFF"); assertions++;
      await page.locator("#erp-table-layout-css").evaluate((el, css) => { el.textContent = css; }, fixtures.cases.body_off.css);
      equal((await styles(table + " tbody td", ["borderLeftWidth", "borderBottomWidth"])), {borderLeftWidth:"0px",borderBottomWidth:"0px"}, "body OFF");
      equal((await styles(header, ["borderLeftWidth"])).borderLeftWidth, "1px", "header independent of body columns");
    }
    await open("systemic_intelligence", fixtures.cases.on.css);
    await page.locator("table tbody").first().evaluate(body => {
      body.innerHTML = "<tr><td>سلول یک</td><td>سلول دو</td></tr>";
      const tr = body.rows[0]; tr.classList.add("is-row-selected"); tr.cells[0].classList.add("is-cell-focus");
    });
    equal((await styles("table tbody tr.is-row-selected td.is-cell-focus", ["backgroundColor", "outlineColor"])),
      {backgroundColor:"rgb(171, 205, 239)",outlineColor:"rgb(101, 67, 33)"}, "Smart Planning selected cell/focus");
    equal((await styles("table tbody tr.is-row-selected td:nth-child(2)", ["backgroundColor"])).backgroundColor, "rgb(18, 52, 86)", "Smart Planning selected row");

    async function ensureProductTable() {
      if (await page.locator(".product-data-table").count()) return;
      await page.locator('main[data-table-type="general"]').evaluate(main => {
        main.insertAdjacentHTML("beforeend", '<div class="table-scroll"><table id="product-data-test-table" class="table table-compact product-data-table view-mode"><thead><tr><th>کد</th><th>نام قطعه</th><th>گروه</th></tr></thead><tbody><tr><td><span class="cell-view">۱۲۳</span></td><td><span class="cell-view">نام محصول آزمایشی</span></td><td><span class="cell-view">گروه آزمایشی</span></td></tr></tbody></table></div>');
      });
      await page.evaluate(() => window.ERPTableLayout.enhanceAll());
    }
    await open("product_data", fixtures.cases.short.css);
    await ensureProductTable();
    const widths = await page.locator(".product-data-table thead th").evaluateAll(ths => ths.map((th, i) => 120 + i * 10));
    const key = "erp.table.colwidths.product_data.product-data-test-table.c" + widths.length;
    await page.evaluate(({key, widths}) => localStorage.setItem(key, JSON.stringify(widths)), {key, widths});
    async function productState() { return page.evaluate(key => ({saved:localStorage.getItem(key), widths:[...document.querySelectorAll(".product-data-table thead th")].map(th => th.getBoundingClientRect().width), width:document.querySelector(".product-data-table").getBoundingClientRect().width}), key); }
    await open("product_data", fixtures.cases.short.css);
    await ensureProductTable();
    const before = await productState();
    await page.locator("#erp-table-layout-css").evaluate((el, css) => { el.textContent = css; }, fixtures.cases.tall.css);
    equal(await productState(), before, "Product widths/persistence after live row-height change");
    await open("product_data", fixtures.cases.tall.css);
    await ensureProductTable();
    equal(await productState(), before, "Product widths/persistence after reload with changed row height");

    for (const family of ["general", "calculation", "report"]) {
      await open("settings_" + family);
      const input = page.locator('[data-number-for="row_height_px"]');
      const controlState = () => page.locator(".layout-tint").first().evaluate(box => ({
        direction:getComputedStyle(box).direction, display:getComputedStyle(box).display,
        children:[...box.children].map(el=>({x:el.getBoundingClientRect().x,width:el.getBoundingClientRect().width,direction:getComputedStyle(el).direction}))
      }));
      const initial = await controlState();
      equal(await input.inputValue(), String(fixtures.defaults[family].row_height_px).replace(/\d/g,d=>"۰۱۲۳۴۵۶۷۸۹"[d]), "initial Persian value");
      await input.focus();
      await input.fill("55");
      equal(await input.inputValue(), "۵۵", "Persian while editing");
      equal((await styles('[data-number-for="row_height_px"]', ["direction", "textAlign", "unicodeBidi"])), {direction:"ltr",textAlign:"center",unicodeBidi:"isolate"}, "stable numeric bidi");
      equal(await controlState(), initial, "editing leaves range/color order fixed");
      await page.locator('[data-number-for="body_font_size_px"]').fill("18.4");
      equal(await page.locator('[name="body_font_size_px"]').inputValue(), "18.4", "ASCII canonical decimal");
      await input.fill("81");
      equal(await input.evaluate(el=>el.checkValidity()), false, "max validation");
      await input.fill("22.5");
      equal(await input.evaluate(el=>el.checkValidity()), false, "integer step validation");
      await input.fill("55");
      await page.locator('[name="header_alpha"]').evaluate(el=>{el.value="0";el.dispatchEvent(new Event("input",{bubbles:true}));el.value="100";el.dispatchEvent(new Event("input",{bubbles:true}));});
      equal(await controlState(), initial, "slider endpoint leaves orientation fixed");
      equal(await page.locator(".layout-tint output").first().textContent(), "۱۰۰٪", "Persian percent");
      posted = null;
      await page.locator(".layout-page-actions .btn-primary-soft").click();
      await page.waitForFunction(()=>document.querySelector(".toast-success"));
      assert.match(posted, /name="row_height_px"\r\n\r\n55/); assertions++;
      equal(await controlState(), initial, "Apply stable layout");
      await page.locator('[name="family_reset"]').click();
      await page.waitForTimeout(100);
      equal(await controlState(), initial, "Reset stable layout");
      await page.reload();
      equal(await controlState(), initial, "Refresh stable layout");
    }
    console.log(`PASS: ${assertions} browser assertions (real rendered fixtures, isolated HTTP interception, no database).`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
