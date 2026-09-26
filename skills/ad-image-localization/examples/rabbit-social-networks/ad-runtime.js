/* ad-runtime.js — shared runtime for HTML ad reconstructions.
 *
 * render.py injects window.__AD__ = {lang, w, h, copy, qa} before the page loads.
 * Opened directly in a browser, it falls back to ?lang=xx&w=..&h=.. and to the
 * <script type="application/json" id="copy-xx"> blocks embedded in the page.
 *
 * Contract for creative.html authors:
 *   data-copy="key"        element text comes from copy[key] (<em>, <strong>, <br>,
 *                          <span class="nowrap"> allowed inside copy strings)
 *   data-fit               shrink font-size until content fits the element's box.
 *                          CSS font-size is the maximum; data-fit-min (px) the floor.
 *   data-fit="line"        same, but forced to a single line
 *   data-qa="text"         text block included in overlap checks (data-copy implies it)
 *   data-qa="keepout"      area text must not cover (faces, product, logo, stickers);
 *                          on <img> cutouts only opaque pixels count
 *   data-min-font="11"     per-element legibility floor for QA (px)
 *   data-qa-solid          text block with its own background (CTA button): overlap
 *                          checks use its full box and contrast checks skip it
 *   data-min-contrast="3"  WCAG contrast floor vs the plate behind the glyphs (default 3)
 *   data-src-size="x_{size}.png"  per-size image (exact-size plates), falls back to src
 *   data-qa-decor          on a container: its text is decorative (e.g. UI inside a
 *                          phone mockup) and is skipped by QA; review it visually
 * Root <html> receives: lang, dir, data-layout, data-tier (xs|sm|lg by short side),
 * data-wb / data-hb (s|m|l width/height buckets), data-ratio, data-size and
 * CSS vars --W / --H (canvas px). Write layout rules against data-layout.
 */
(function () {
  "use strict";
  const RTL = new Set(["ar", "he", "fa", "ur", "yi", "ps", "ckb"]);
  const params = new URLSearchParams(location.search);
  const cfg = window.__AD__ || {};
  const W = cfg.w || +params.get("w") || innerWidth;
  const H = cfg.h || +params.get("h") || innerHeight;
  const lang = cfg.lang || params.get("lang") || document.documentElement.lang || "en";
  const base = lang.split("-")[0].toLowerCase();

  function classify(w, h) {
    const r = w / h;
    if (r >= 5) return "strip";          // 320x50, 728x90, 970x90
    if (r >= 2.3) return "billboard";    // 970x250, 320x100
    if (r >= 1.4) return "landscape";    // 1200x628, 1920x1080
    if (r >= 0.9) return "square";       // 1200x1200, 300x250
    if (r >= 0.7) return "portrait";     // 1080x1350
    if (r >= 0.4) return "story";        // 1080x1920, 300x600
    return "skyscraper";                 // 160x600
  }
  const minSide = Math.min(W, H);
  const tier = minSide < 120 ? "xs" : minSide < 400 ? "sm" : "lg";
  const wb = W < 400 ? "s" : W < 1000 ? "m" : "l";   // width bucket
  const hb = H < 400 ? "s" : H < 1000 ? "m" : "l";   // height bucket

  const root = document.documentElement;
  root.lang = lang;
  root.dir = RTL.has(base) ? "rtl" : "ltr";
  root.dataset.layout = cfg.layout || classify(W, H);
  root.dataset.tier = tier;
  root.dataset.wb = wb;
  root.dataset.hb = hb;
  root.dataset.ratio = (W / H).toFixed(3);
  root.dataset.size = `${W}x${H}`;
  root.style.setProperty("--W", W + "px");
  root.style.setProperty("--H", H + "px");

  function embeddedCopy(l) {
    const el = document.getElementById("copy-" + l) || document.getElementById("copy-" + l.split("-")[0]);
    return el ? JSON.parse(el.textContent) : null;
  }
  const copy = cfg.copy || embeddedCopy(lang) || embeddedCopy("en") || {};

  const ALLOWED = /^(em|strong|br|span|b|i)$/i;
  function safeHTML(str) {
    const tpl = document.createElement("template");
    tpl.innerHTML = String(str);
    tpl.content.querySelectorAll("*").forEach((n) => {
      if (!ALLOWED.test(n.tagName)) n.replaceWith(document.createTextNode(n.textContent));
      else [...n.attributes].forEach((a) => a.name !== "class" && n.removeAttribute(a.name));
    });
    return tpl.innerHTML;
  }

  const missing = [];
  function fillCopy() {
    document.querySelectorAll("[data-copy]").forEach((el) => {
      const key = el.dataset.copy;
      if (copy[key] === undefined || copy[key] === null) { missing.push(key); return; }
      el.innerHTML = safeHTML(copy[key]);
    });
  }

  // Overflow = content spills past the box, or glyphs run into the horizontal
  // padding (flex/nowrap text can do that without changing scrollWidth).
  function overflows(el) {
    if (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1) return true;
    const cs = getComputedStyle(el);
    const pl = parseFloat(cs.paddingLeft) || 0, pr = parseFloat(cs.paddingRight) || 0;
    if (!pl && !pr) return false;
    const r = el.getBoundingClientRect(), ink = inkRect(el);
    return ink.left < r.left + pl - 1 || ink.right > r.right - pr + 1;
  }
  function fit(el) {
    if (el.dataset.fit === "line") el.style.whiteSpace = "nowrap";
    const max = parseFloat(getComputedStyle(el).fontSize);
    const min = parseFloat(el.dataset.fitMin || 6);
    el.style.fontSize = max + "px";
    if (!overflows(el)) return max;
    let lo = min, hi = max;
    for (let i = 0; i < 14; i++) {
      const mid = (lo + hi) / 2;
      el.style.fontSize = mid + "px";
      if (overflows(el)) hi = mid; else lo = mid;
    }
    el.style.fontSize = lo + "px";
    return lo;
  }
  function visible(el) {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || +cs.opacity === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !el.closest("[hidden]") && el.offsetParent !== null;
  }
  function inter(a, b) {
    const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
    const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
    return x * y;
  }
  // Per-line glyph boxes, and their union: the text itself, not the element box.
  function lineRects(el) {
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = [...range.getClientRects()].filter((r) => r.width && r.height);
    return rects.length ? rects : [el.getBoundingClientRect()];
  }
  function inkRect(el) {
    const rects = lineRects(el);
    if (rects.length === 1) return rects[0];
    return rects.reduce((a, r) => ({
      left: Math.min(a.left, r.left), top: Math.min(a.top, r.top),
      right: Math.max(a.right, r.right), bottom: Math.max(a.bottom, r.bottom),
    }), { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity });
  }

  // Overlap area between a text rect and a keep-out. For <img> keep-outs (cutouts)
  // only opaque pixels count, so transparent corners of a PNG do not raise alarms.
  // Needs --allow-file-access-from-files for file:// pages (render.py sets it).
  function keepoutCoverage(k, r) {
    const kr = k.getBoundingClientRect();
    const box = inter(r, kr);
    if (!box || k.tagName !== "IMG") return box;
    try {
      const x0 = Math.max(r.left, kr.left), y0 = Math.max(r.top, kr.top);
      const x1 = Math.min(r.right, kr.right), y1 = Math.min(r.bottom, kr.bottom);
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(kr.width / 4)); c.height = Math.max(1, Math.round(kr.height / 4));
      const g = c.getContext("2d");
      g.drawImage(k, 0, 0, c.width, c.height);
      const sx = c.width / kr.width, sy = c.height / kr.height;
      const ix = Math.floor((x0 - kr.left) * sx), iy = Math.floor((y0 - kr.top) * sy);
      const iw = Math.max(1, Math.ceil((x1 - x0) * sx)), ih = Math.max(1, Math.ceil((y1 - y0) * sy));
      const data = g.getImageData(ix, iy, iw, ih).data;
      let solid = 0;
      for (let i = 3; i < data.length; i += 4) if (data[i] > 128) solid++;
      return box * (solid / (iw * ih));
    } catch (e) { return box; }
  }

  // ---- contrast: text colour vs the background plate actually behind the glyphs
  function srgbLum([r, g, b]) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  }
  function parseColor(str) {
    const m = str.match(/rgba?\(([^)]+)\)/);
    return m ? m[1].split(",").slice(0, 3).map((v) => parseFloat(v)) : null;
  }
  let plateCtx = null;
  function plateSampler() {
    if (plateCtx !== null) return plateCtx;
    plateCtx = false;
    const plate = document.querySelector(".plate");
    if (!plate) return plateCtx;
    try {
      const c = document.createElement("canvas");
      c.width = W; c.height = H;
      const g = c.getContext("2d", { willReadFrequently: true });
      const m = new DOMMatrix(getComputedStyle(plate).transform === "none" ? undefined : getComputedStyle(plate).transform);
      if (m.a < 0) { g.translate(W, 0); g.scale(-1, 1); }
      g.drawImage(plate, 0, 0, W, H);
      g.getImageData(0, 0, 1, 1);
      plateCtx = g;
    } catch (e) { plateCtx = false; }
    return plateCtx;
  }
  // Worst-case (10th percentile) contrast of each text run against the plate pixels
  // under its line boxes. Skips text inside elements with their own background.
  function contrastIssues(el, id) {
    const g = plateSampler();
    if (!g || el.hasAttribute("data-qa-solid") || el.closest("[data-qa-solid]")) return [];
    const out = [];
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent.trim()) continue;
      const col = parseColor(getComputedStyle(node.parentElement).color);
      if (!col) continue;
      const range = document.createRange(); range.selectNodeContents(node);
      const ratios = [];
      for (const r of range.getClientRects()) {
        const x0 = Math.max(0, Math.floor(r.left)), y0 = Math.max(0, Math.floor(r.top));
        const w = Math.min(W, Math.ceil(r.right)) - x0, h = Math.min(H, Math.ceil(r.bottom)) - y0;
        if (w < 2 || h < 2) continue;
        const d = g.getImageData(x0, y0, w, h).data;
        const lt = srgbLum(col);
        for (let i = 0; i < d.length; i += 4 * 7) {
          const lb = srgbLum([d[i], d[i + 1], d[i + 2]]);
          ratios.push((Math.max(lt, lb) + 0.05) / (Math.min(lt, lb) + 0.05));
        }
      }
      if (!ratios.length) continue;
      ratios.sort((a, b) => a - b);
      const p10 = ratios[Math.floor(ratios.length * 0.1)];
      const min = parseFloat(el.dataset.minContrast || 3);
      if (p10 < min) out.push({ id, level: "warn", issue: "low_contrast", value: +p10.toFixed(2), text: node.textContent.trim().slice(0, 24) });
    }
    return out;
  }

  function runQA() {
    const issues = [];
    const texts = [...document.querySelectorAll('[data-copy], [data-qa="text"]')]
      .filter((el) => visible(el) && !el.closest("[data-qa-decor]"));
    const keepouts = [...document.querySelectorAll('[data-qa="keepout"]')].filter(visible);
    const layout = root.dataset.layout;
    const defaultMin = tier === "xs" ? 8 : tier === "sm" ? 9 : 14;
    const report = [];
    texts.forEach((el) => {
      const id = el.dataset.copy || el.id || el.className;
      const r = inkRect(el);
      const fs = parseFloat(getComputedStyle(el).fontSize);
      const minFont = parseFloat(el.dataset.minFont || defaultMin);
      report.push({ id, font_px: +fs.toFixed(1), box: [r.left, r.top, r.right, r.bottom].map((v) => Math.round(v)) });
      if (!el.textContent.trim()) issues.push({ id, level: "error", issue: "empty_text" });
      issues.push(...contrastIssues(el, id));
      if (r.left < -1 || r.top < -1 || r.right > W + 1 || r.bottom > H + 1)
        issues.push({ id, level: "error", issue: "text_outside_canvas" });
      if (el.hasAttribute("data-fit") && overflows(el))
        issues.push({ id, level: "error", issue: "text_overflow_after_fit" });
      if (fs < minFont) issues.push({ id, level: "warn", issue: `font_below_${minFont}px`, value: +fs.toFixed(1) });
      const lines = lineRects(el);
      keepouts.forEach((k) => {
        if (k.contains(el)) return;
        const own = lines.reduce((a, l) => a + l.width * l.height, 0);
        const ratio = own ? lines.reduce((a, l) => a + keepoutCoverage(k, l), 0) / own : 0;
        if (ratio > 0.02)
          issues.push({ id, level: "warn", issue: "covers_keepout", keepout: k.dataset.name || k.className, ratio: +ratio.toFixed(2) });
      });
      if (layout === "story" && W >= 1000 && (r.top < H * 0.13 || r.bottom > H * 0.8))
        issues.push({ id, level: "warn", issue: "outside_story_safe_zone" });
    });
    // Pairwise overlap between top-level text blocks (ignore nested elements).
    const tops = texts.filter((el) => !texts.some((o) => o !== el && o.contains(el)));
    for (let i = 0; i < tops.length; i++)
      for (let j = i + 1; j < tops.length; j++) {
        // Solid blocks (buttons, badges) count by their whole box, not just glyphs.
        const box = (el) => el.hasAttribute("data-qa-solid") ? el.getBoundingClientRect() : inkRect(el);
        const a = box(tops[i]), b = box(tops[j]);
        const area = inter(a, b);
        const small = Math.min((a.right - a.left) * (a.bottom - a.top), (b.right - b.left) * (b.bottom - b.top));
        if (small && area / small > 0.05 && !tops[i].closest("[data-qa-group]"))
          issues.push({ id: `${tops[i].dataset.copy}|${tops[j].dataset.copy}`, level: "warn", issue: "text_overlap" });
      }
    missing.forEach((key) => issues.push({ id: key, level: "error", issue: "missing_copy_key" }));
    const fonts = [...document.fonts].filter((f) => f.status === "loaded").map((f) => `${f.family} ${f.weight}`);
    const failedFonts = [...document.fonts].filter((f) => f.status === "error").map((f) => `${f.family} ${f.weight}`);
    failedFonts.forEach((f) => issues.push({ id: f, level: "warn", issue: "font_failed_to_load" }));
    return { lang, dir: root.dir, layout, tier, size: [W, H], issues, texts: report, fonts_loaded: [...new Set(fonts)] };
  }

  // <img data-src-size="work/layers/plates/plate_{size}.png" src="work/layers/plate.png">
  // swaps in the exact-size plate built by `decompose.py extend`, falling back to src.
  function sizedSources() {
    return Promise.all([...document.querySelectorAll("img[data-src-size]")].map((img) => new Promise((done) => {
      const fallback = img.getAttribute("src");
      const sized = img.dataset.srcSize.replace("{size}", `${W}x${H}`);
      img.onload = () => done();
      img.onerror = () => { img.onerror = null; img.onload = () => done(); img.src = fallback; };
      img.src = sized;
    })));
  }

  async function boot() {
    fillCopy();
    await sizedSources();
    // Painters registered by scene-kit.js (procedural backgrounds) run per size.
    for (const hook of window.__adHooks || []) {
      try { await hook({ W, H, lang, dir: root.dir, layout: root.dataset.layout, tier, wb, hb }); }
      catch (e) { console.error("ad hook failed", e); }
    }
    document.dispatchEvent(new CustomEvent("ad:copy", { detail: { lang, layout: root.dataset.layout, W, H } }));
    try {
      const sample = document.body.innerText.slice(0, 400);
      const families = new Set();
      document.querySelectorAll("*").forEach((el) => families.add(getComputedStyle(el).fontFamily.split(",")[0].trim()));
      await Promise.all([...families].map((f) => document.fonts.load(`700 32px ${f}`, sample).catch(() => null)));
      await document.fonts.ready;
    } catch (e) { /* fonts optional */ }
    await Promise.all([...document.images].map((img) => img.complete ? null : new Promise((r) => { img.onload = img.onerror = r; })));
    // Fit parents before children so nested fits see final boxes.
    document.querySelectorAll("[data-fit]").forEach(fit);
    document.dispatchEvent(new CustomEvent("ad:fitted"));
    window.__adQA = runQA();
    window.__adReady = true;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
