/* scene-kit.js — procedural background primitives for ad reconstructions.
 *
 * Instead of stretching an inpainted plate, the agent can re-draw the background
 * natively at every canvas size: sky, glow, clouds, mountain ridges, hills,
 * tree lines, grass fields, flowers, bokeh, sparkles, grain. All randomness is
 * seeded, so every language renders the identical scene for a given size.
 *
 * Usage in creative.html:
 *   <canvas class="plate" id="scene"></canvas>
 *   <script src="scene-kit.js"></script>
 *   <script>
 *     SceneKit.register((S, c) => { ... draw with S.* ... });   // c = {W,H,layout,dir,...}
 *   </script>
 *   <script src="ad-runtime.js"></script>     // runtime awaits registered painters
 *
 * Palette: sample colours from the source (decompose.py palette) instead of guessing.
 */
(function () {
  "use strict";

  // ---------------------------------------------------------------- random + noise
  function rng(seed) {
    let a = typeof seed === "number" ? seed >>> 0 : [...String(seed)].reduce((h, ch) => Math.imul(h ^ ch.charCodeAt(0), 16777619), 2166136261) >>> 0;
    const r = () => {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    r.range = (lo, hi) => lo + r() * (hi - lo);
    r.pick = (arr) => arr[Math.floor(r() * arr.length)];
    r.gauss = () => (r() + r() + r() - 1.5) / 1.5;
    return r;
  }
  // 1-D value noise with smooth interpolation, fBm over octaves. Returns ~[-1, 1].
  function noise1D(seed) {
    const r = rng(seed), table = Array.from({ length: 512 }, () => r() * 2 - 1);
    const at = (x) => {
      const i = Math.floor(x), f = x - i, s = f * f * (3 - 2 * f);
      return table[i & 511] * (1 - s) + table[(i + 1) & 511] * s;
    };
    return (x, octaves = 4) => {
      let v = 0, amp = 1, freq = 1, norm = 0;
      for (let o = 0; o < octaves; o++) { v += at(x * freq + o * 31.7) * amp; norm += amp; amp *= 0.5; freq *= 2.03; }
      return v / norm;
    };
  }

  // ---------------------------------------------------------------- colour helpers
  function hex(c) {
    if (Array.isArray(c)) return c;
    const m = c.replace("#", "");
    const n = parseInt(m.length === 3 ? m.split("").map((x) => x + x).join("") : m, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function mix(a, b, t) { a = hex(a); b = hex(b); return a.map((v, i) => Math.round(v + (b[i] - v) * t)); }
  function rgba(c, alpha = 1) { const [r, g, b] = hex(c); return `rgba(${r},${g},${b},${alpha})`; }
  function jitter(c, r, amt) { return hex(c).map((v) => Math.max(0, Math.min(255, Math.round(v + (r() - 0.5) * 2 * amt)))); }

  // Offscreen layer, composited later (optionally blurred for depth of field).
  function layer(W, H) {
    const cv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    return cv.getContext("2d");
  }
  function composite(ctx, lctx, { blur = 0, alpha = 1, mode = "source-over" } = {}) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.globalCompositeOperation = mode;
    if (blur > 0.05) ctx.filter = `blur(${blur}px)`;
    ctx.drawImage(lctx.canvas, 0, 0);
    ctx.restore();
  }

  // ---------------------------------------------------------------- primitives
  /** Vertical gradient. stops: [[0,'#1c9bf9'],[0.6,'#bfe4fb'],[1,'#e6f4fc']] over y0..y1 */
  function sky(ctx, W, H, stops, y0 = 0, y1 = H) {
    const g = ctx.createLinearGradient(0, y0, 0, y1);
    stops.forEach(([p, c]) => g.addColorStop(p, rgba(c)));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
  }

  /** Soft radial light (sun haze, glow behind copy). */
  function glow(ctx, x, y, r, color = "#ffffff", alpha = 0.8, sx = 1, sy = 1) {
    ctx.save();
    ctx.translate(x, y); ctx.scale(sx, sy);
    const g = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
    g.addColorStop(0, rgba(color, alpha));
    g.addColorStop(0.45, rgba(color, alpha * 0.45));
    g.addColorStop(1, rgba(color, 0));
    ctx.fillStyle = g;
    ctx.fillRect(-r, -r, 2 * r, 2 * r);
    ctx.restore();
  }

  /** Cumulus cloud: a flat-bottomed cluster of shaded puffs. w = width in px. */
  function cloud(ctx, x, y, w, { seed = 1, light = "#ffffff", shade = "#cfe3f5", alpha = 1, puffs = 9, flat = 0.28 } = {}) {
    const r = rng(seed);
    const l = layer(ctx.canvas.width, ctx.canvas.height);
    const base = y, list = [];
    for (let i = 0; i < puffs; i++) {
      const t = i / (puffs - 1);
      const px = x + (t - 0.5) * w * 0.85 + r.range(-0.04, 0.04) * w;
      const bump = Math.sin(Math.PI * t) ** 0.8;
      const pr = w * (0.1 + 0.16 * bump) * r.range(0.8, 1.2);
      list.push([px, base - pr * r.range(0.35, 0.8) - bump * w * 0.07, pr]);
    }
    list.sort((a, b) => a[1] - b[1]);
    for (const [px, py, pr] of list) {
      const g = l.createRadialGradient(px - pr * 0.3, py - pr * 0.45, pr * 0.1, px, py, pr);
      g.addColorStop(0, rgba(light, 1));
      g.addColorStop(0.62, rgba(mix(light, shade, 0.35), 1));
      g.addColorStop(1, rgba(shade, 1));
      l.fillStyle = g;
      l.beginPath(); l.arc(px, py, pr, 0, Math.PI * 2); l.fill();
    }
    // Flatten the underside and shade it.
    l.globalCompositeOperation = "destination-out";
    l.fillRect(x - w, base + w * 0.02, w * 2, w);
    l.globalCompositeOperation = "source-atop";
    const s = l.createLinearGradient(0, base - w * flat, 0, base);
    s.addColorStop(0, rgba(shade, 0));
    s.addColorStop(1, rgba(shade, 0.75));
    l.fillStyle = s;
    l.fillRect(x - w, base - w * flat, w * 2, w * flat + 2);
    composite(ctx, l, { blur: Math.max(0.6, w / 140), alpha });
  }

  /**
   * Mountain / hill ridge filled to the bottom of the canvas.
   * peaks: [{x:0..1, h:px, w:0..1}] tent-shaped peaks added to a noise baseline.
   */
  function ridge(ctx, W, H, { y, amp = 0, peaks = [], rough = 0.04, freq = 3, seed = 7, top = "#9cc3e0", bottom = "#6aa7cf",
                               blur = 0, alpha = 1, highlight = null, snow = 0, texture = 0, x0 = 0, x1 = W } = {}) {
    const n = noise1D(seed), l = layer(W, H), step = Math.max(1, Math.round(W / 480));
    const hAt = (px) => {
      const u = (px - x0) / (x1 - x0);
      let h = amp * (0.5 + 0.5 * n(u * freq, 3));
      for (const p of peaks) {
        const d = Math.abs(u - p.x) / (p.w || 0.2);
        if (d < 1) h += p.h * (1 - d) ** 1.35 * (1 + 0.12 * n(u * 40 + p.x * 9, 2));
      }
      return y - h - rough * H * n(u * freq * 9 + 50, 3);
    };
    const pts = [];
    for (let px = -step; px <= W + step; px += step) pts.push([px, hAt(px)]);
    const minY = Math.min(...pts.map((p) => p[1]));
    l.beginPath(); l.moveTo(-step, H + 2);
    pts.forEach(([px, py]) => l.lineTo(px, py));
    l.lineTo(W + step, H + 2); l.closePath();
    const g = l.createLinearGradient(0, minY, 0, y + (H - y) * 0.2);
    g.addColorStop(0, rgba(top)); g.addColorStop(1, rgba(bottom));
    l.fillStyle = g; l.fill();
    if (snow) { // brighten the upper slopes
      l.save(); l.clip();
      const sg = l.createLinearGradient(0, minY, 0, minY + snow);
      sg.addColorStop(0, "rgba(255,255,255,0.55)"); sg.addColorStop(1, "rgba(255,255,255,0)");
      l.fillStyle = sg; l.fillRect(0, minY, W, snow); l.restore();
    }
    if (highlight) { // sunlit faces: light falls from the upper left onto each peak's left flank
      l.save(); l.clip();
      for (const p of peaks) {
        const px = x0 + p.x * (x1 - x0), py = hAt(px), half = (p.w || 0.2) * (x1 - x0);
        const g2 = l.createLinearGradient(px - half, 0, px, 0);
        g2.addColorStop(0, rgba(highlight, 0)); g2.addColorStop(0.75, rgba(highlight, 0.32)); g2.addColorStop(1, rgba(highlight, 0.05));
        l.fillStyle = g2;
        l.beginPath(); l.moveTo(px, py); l.lineTo(px - half, py + p.h * 1.05); l.lineTo(px + half * 0.08, py + p.h * 1.1); l.closePath(); l.fill();
      }
      l.restore();
    }
    if (texture) { // faint vertical erosion streaks so large faces aren't flat
      l.save(); l.globalCompositeOperation = "source-atop";
      const tr = rng(seed + 99);
      for (let i = 0; i < W / 6; i++) {
        const tx = tr() * W, ty = hAt(tx) + tr() * (H - y) * 0.2;
        l.strokeStyle = rgba(tr() > 0.5 ? "#ffffff" : "#1d4a6a", texture * tr());
        l.lineWidth = tr.range(1, 3);
        l.beginPath(); l.moveTo(tx, ty); l.lineTo(tx + tr.range(-8, 8), ty + tr.range(10, 60)); l.stroke();
      }
      l.restore();
    }
    composite(ctx, l, { blur, alpha });
    return hAt;
  }

  /** Rounded broadleaf trees along a ridge function (from ridge()). */
  function trees(ctx, W, H, ridgeAt, { count = 20, size = 30, seed = 3, dark = "#3d7a3a", light = "#79b35a", blur = 0, alpha = 1, band = [0, 1] } = {}) {
    const r = rng(seed), l = layer(W, H);
    for (let i = 0; i < count; i++) {
      const x = W * r.range(band[0], band[1]);
      const s = size * r.range(0.6, 1.4);
      const y = ridgeAt(x) + s * r.range(0.3, 1.2);
      for (let k = 0; k < 5; k++) {
        const cx = x + r.range(-0.35, 0.35) * s, cy = y - s * r.range(0.4, 1.1), cr = s * r.range(0.35, 0.55);
        const g = l.createRadialGradient(cx - cr * 0.3, cy - cr * 0.4, cr * 0.1, cx, cy, cr);
        g.addColorStop(0, rgba(light)); g.addColorStop(1, rgba(dark));
        l.fillStyle = g; l.beginPath(); l.arc(cx, cy, cr, 0, Math.PI * 2); l.fill();
      }
    }
    composite(ctx, l, { blur, alpha });
  }

  /** Grass field from y0 (far) to y1 (near). Blades grow with depth; far rows are blurred. */
  function grass(ctx, W, H, { y0, y1 = H, seed = 11, colors = ["#6fae2a", "#8cc63a", "#5c9a22", "#a5cf45", "#4f8a1e"],
                              tip = "#c8e27a", base = "#3f7a1c", ground = ["#9cc54a", "#5f9a2a"], density = 1, maxBlade = 0.12, layers = 4, farBlur = 2.2, fillGround = true } = {}) {
    const r = rng(seed);
    if (fillGround) {
      const g = ctx.createLinearGradient(0, y0, 0, y1);
      g.addColorStop(0, rgba(ground[0])); g.addColorStop(1, rgba(ground[1]));
      ctx.fillStyle = g; ctx.fillRect(0, y0, W, H - y0);
    }
    const unit = Math.min(W, H);
    for (let li = 0; li < layers; li++) {
      const t0 = li / layers, t1 = (li + 1) / layers;
      const l = layer(W, H);
      const ya = y0 + (y1 - y0) * t0 ** 1.6, yb = y0 + (y1 - y0) * t1 ** 1.6;
      const depth = (t0 + t1) / 2;
      const bladeH = unit * maxBlade * (0.12 + depth ** 1.3);
      const n = Math.round(density * W * (yb - ya) / Math.max(4, bladeH * 1.2));
      for (let i = 0; i < n; i++) {
        const x = r() * W, yy = ya + r() * (yb - ya);
        const h = bladeH * r.range(0.5, 1.2), w = Math.max(0.6, h * r.range(0.05, 0.1));
        const lean = r.gauss() * h * 0.35;
        const col = jitter(r.pick(colors), r, 14);
        const gr = l.createLinearGradient(0, yy, 0, yy - h);
        gr.addColorStop(0, rgba(mix(col, base, 0.45))); gr.addColorStop(0.7, rgba(col)); gr.addColorStop(1, rgba(mix(col, tip, 0.5)));
        l.fillStyle = gr;
        l.beginPath();
        l.moveTo(x - w, yy);
        l.quadraticCurveTo(x - w * 0.5 + lean * 0.4, yy - h * 0.55, x + lean, yy - h);
        l.quadraticCurveTo(x + w * 0.5 + lean * 0.4, yy - h * 0.55, x + w, yy);
        l.closePath(); l.fill();
      }
      composite(ctx, l, { blur: farBlur * (1 - depth) ** 2 });
    }
  }

  /** Daisy / five-petal flowers scattered by depth between y0 and y1. */
  function flowers(ctx, W, H, { y0, y1 = H, seed = 21, count = 60, kind = "daisy", size = 0.05, petal = "#ffffff", petalShade = "#dfe5ec",
                                center = "#ffc928", centerShade = "#e59200", stem = "#4f8a1e", farBlur = 2.5, nearBlur = 0, bias = 1.8, avoid = [] } = {}) {
    const r = rng(seed), unit = Math.min(W, H), list = [];
    for (let i = 0; i < count; i++) {
      const d = r() ** bias; // bias > 1 = more flowers far away
      const y = y0 + (y1 - y0) * (1 - d);
      const x = r() * W;
      if (avoid.some(([ax, ay, aw, ah]) => x > ax && x < ax + aw && y > ay && y < ay + ah)) continue;
      list.push({ x, y, depth: 1 - d });
    }
    list.sort((a, b) => a.y - b.y);
    for (const f of list) {
      const s = unit * size * (0.18 + f.depth ** 1.4) * r.range(0.75, 1.2);
      if (s < 1.2) continue;
      const l = layer(W, H);
      const tilt = r.range(0.55, 0.95), rot = r.range(-0.4, 0.4);
      // stem
      l.strokeStyle = rgba(stem); l.lineWidth = Math.max(0.6, s * 0.07); l.lineCap = "round";
      l.beginPath(); l.moveTo(f.x, f.y); l.quadraticCurveTo(f.x + r.gauss() * s * 0.3, f.y + s * 1.2, f.x + r.gauss() * s * 0.4, f.y + s * 2.6); l.stroke();
      l.save(); l.translate(f.x, f.y); l.rotate(rot); l.scale(1, tilt);
      const petals = kind === "daisy" ? 13 : 5;
      const pl = kind === "daisy" ? s : s * 0.62, pw = kind === "daisy" ? s * 0.26 : s * 0.42;
      for (let k = 0; k < petals; k++) {
        const a = (k / petals) * Math.PI * 2 + r.range(-0.08, 0.08);
        l.save(); l.rotate(a);
        const pg = l.createLinearGradient(0, 0, pl, 0);
        pg.addColorStop(0, rgba(petalShade)); pg.addColorStop(0.45, rgba(petal)); pg.addColorStop(1, rgba(mix(petal, petalShade, 0.25)));
        l.fillStyle = pg;
        l.beginPath(); l.ellipse(pl * 0.52, 0, pl * 0.5, pw * 0.5, 0, 0, Math.PI * 2); l.fill();
        l.restore();
      }
      const cr = kind === "daisy" ? s * 0.26 : s * 0.2;
      const cg = l.createRadialGradient(-cr * 0.3, -cr * 0.35, cr * 0.1, 0, 0, cr);
      cg.addColorStop(0, rgba(mix(center, "#ffffff", 0.35))); cg.addColorStop(0.6, rgba(center)); cg.addColorStop(1, rgba(centerShade));
      l.fillStyle = cg; l.beginPath(); l.arc(0, 0, cr, 0, Math.PI * 2); l.fill();
      l.restore();
      const blur = farBlur * (1 - f.depth) ** 2 + nearBlur * f.depth ** 4;
      composite(ctx, l, { blur });
    }
  }

  /** Out-of-focus light discs. */
  function bokeh(ctx, W, H, { seed = 5, count = 20, color = "#ffffff", size = [0.01, 0.05], alpha = [0.1, 0.35], area = [0, 0, 1, 1] } = {}) {
    const r = rng(seed), unit = Math.min(W, H);
    for (let i = 0; i < count; i++) {
      const x = W * r.range(area[0], area[2]), y = H * r.range(area[1], area[3]);
      const rad = unit * r.range(size[0], size[1]);
      const g = ctx.createRadialGradient(x, y, rad * 0.2, x, y, rad);
      const a = r.range(alpha[0], alpha[1]);
      g.addColorStop(0, rgba(color, a)); g.addColorStop(0.8, rgba(color, a * 0.8)); g.addColorStop(1, rgba(color, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, rad, 0, Math.PI * 2); ctx.fill();
    }
  }

  /** Four-point sparkle with glow. */
  function sparkle(ctx, x, y, s, color = "#ffffff") {
    glow(ctx, x, y, s * 1.6, color, 0.55);
    ctx.save(); ctx.translate(x, y); ctx.fillStyle = rgba(color);
    ctx.beginPath();
    for (let k = 0; k < 4; k++) {
      const a = (k * Math.PI) / 2;
      ctx.lineTo(Math.cos(a) * s, Math.sin(a) * s);
      ctx.lineTo(Math.cos(a + Math.PI / 4) * s * 0.18, Math.sin(a + Math.PI / 4) * s * 0.18);
    }
    ctx.closePath(); ctx.fill(); ctx.restore();
  }

  /** Atmospheric haze band (aerial perspective) between y0 and y1. */
  function haze(ctx, W, y0, y1, color = "#e6f3fc", alpha = 0.5) {
    const g = ctx.createLinearGradient(0, y0, 0, y1);
    g.addColorStop(0, rgba(color, 0)); g.addColorStop(0.5, rgba(color, alpha)); g.addColorStop(1, rgba(color, 0));
    ctx.fillStyle = g; ctx.fillRect(0, y0, W, y1 - y0);
  }

  /** Fine film grain so flat gradients don't band and match rendered sources. */
  function grain(ctx, W, H, amount = 6, seed = 9) {
    const r = rng(seed), img = ctx.getImageData(0, 0, ctx.canvas.width, ctx.canvas.height), d = img.data;
    for (let i = 0; i < d.length; i += 4) {
      const n = (r() - 0.5) * amount;
      d[i] += n; d[i + 1] += n; d[i + 2] += n;
    }
    ctx.putImageData(img, 0, 0);
  }

  // ---------------------------------------------------------------- plate + layout helpers
  /** Load an image; try each URL in order (e.g. exact-size plate, then generic plate). */
  function loadImage(...urls) {
    return new Promise((resolve, reject) => {
      const tryAt = (i) => {
        if (i >= urls.length) return reject(new Error("no image: " + urls.join(", ")));
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => tryAt(i + 1);
        img.src = urls[i];
      };
      tryAt(0);
    });
  }
  /** Draw an image with object-fit: cover semantics; fx/fy = focus 0..1; flip for RTL. */
  function cover(ctx, img, W, H, { fx = 0.5, fy = 0.5, flip = false } = {}) {
    const s = Math.max(W / img.width, H / img.height);
    const w = img.width * s, h = img.height * s;
    ctx.save();
    if (flip) { ctx.translate(W, 0); ctx.scale(-1, 1); }
    ctx.drawImage(img, (W - w) * fx, (H - h) * fy, w, h);
    ctx.restore();
  }
  /** Union rect (canvas px) of the elements matching the selectors that are visible. */
  function rectOf(...selectors) {
    const rs = selectors.flatMap((sel) => [...document.querySelectorAll(sel)])
      .filter((el) => getComputedStyle(el).display !== "none")
      .map((el) => el.getBoundingClientRect()).filter((r) => r.width && r.height);
    if (!rs.length) return null;
    const l = Math.min(...rs.map((r) => r.left)), t = Math.min(...rs.map((r) => r.top));
    const r = Math.max(...rs.map((r) => r.right)), b = Math.max(...rs.map((r) => r.bottom));
    return { x: l, y: t, w: r - l, h: b - t, cx: (l + r) / 2, cy: (t + b) / 2 };
  }
  /** Soft legibility haze behind a rect (the "glow behind the copy" many ads use). */
  function haloBehind(ctx, rect, { color = "#ffffff", alpha = 0.6, spread = 0.75 } = {}) {
    if (!rect) return;
    const r = Math.max(rect.w, rect.h) * spread;
    glow(ctx, rect.cx, rect.cy, r, color, alpha, Math.max(0.6, rect.w / Math.max(rect.w, rect.h)) * 1.15,
         Math.max(0.6, rect.h / Math.max(rect.w, rect.h)) * 1.15);
  }

  /** Soft-edged haze panel over a rect: an even legibility wash behind a copy block
   *  (many ad sources have one; it also keeps accent-coloured words readable). */
  function veil(ctx, rect, { color = "#ffffff", alpha = 0.6, pad = 0.12, soft = 0.18 } = {}) {
    if (!rect) return;
    const u = Math.max(rect.w, rect.h);
    const p = u * pad, b = Math.max(2, u * soft);
    ctx.save();
    ctx.filter = `blur(${b}px)`;
    ctx.fillStyle = rgba(color, alpha);
    ctx.fillRect(rect.x - p, rect.y - p, rect.w + 2 * p, rect.h + 2 * p);
    ctx.restore();
  }

  // ---------------------------------------------------------------- registration
  // register(fn)                       paints <canvas id="scene"> (behind everything)
  // register(fn, "#scene-front")       paints another canvas, e.g. a foreground grass
  //                                    layer above the hero that hides cropped legs
  const painters = [];
  function register(fn, target = "#scene") { painters.push([fn, target]); }
  async function paint(info) {
    const dpr = window.devicePixelRatio || 1;
    const prepared = new Map();
    for (const [fn, target] of painters) {
      const cv = document.querySelector(target);
      if (!cv) continue;
      if (!prepared.has(cv)) {
        cv.width = Math.round(info.W * dpr); cv.height = Math.round(info.H * dpr);
        cv.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
        prepared.set(cv, true);
      }
      await fn(api, { ...info, ctx: cv.getContext("2d") });
    }
  }
  const api = { rng, noise1D, hex, mix, rgba, layer, composite, sky, glow, cloud, ridge, trees, grass, flowers, bokeh, sparkle, haze, grain,
                loadImage, cover, rectOf, haloBehind, veil };
  window.SceneKit = { ...api, register, paint };
  (window.__adHooks = window.__adHooks || []).push(paint);
})();
